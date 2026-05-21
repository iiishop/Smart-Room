from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .rle import bbox_from_mask, encode_binary_mask
from .types import DetectionCandidate, TrackedMask, VisionFrameResult


class VisionPipelineError(RuntimeError):
    pass


class DetectionProvider(Protocol):
    def detect(self, image_bgr: np.ndarray, prompt: str) -> list[DetectionCandidate]: ...


class ImageSegmentationProvider(Protocol):
    def segment(
        self, image_bgr: np.ndarray, detections: list[DetectionCandidate]
    ) -> list[np.ndarray]: ...


class VideoTrackingProvider(Protocol):
    def reset(self) -> None: ...

    def bootstrap(
        self,
        image_bgr: np.ndarray,
        detections: list[DetectionCandidate],
        masks: list[np.ndarray],
    ) -> list[np.ndarray]: ...

    def track(self, image_bgr: np.ndarray) -> list[tuple[int, np.ndarray]]: ...


@dataclass(slots=True)
class _SessionState:
    prompt: str
    seeded_detections: list[DetectionCandidate]
    seeded: bool


class GroundedSamTrackingPipeline:
    def __init__(
        self,
        detector: DetectionProvider,
        segmenter: ImageSegmentationProvider,
        tracker: VideoTrackingProvider,
        *,
        max_objects: int = 8,
        source: str = "grounding-dino+sam2",
    ) -> None:
        self._detector = detector
        self._segmenter = segmenter
        self._tracker = tracker
        self._max_objects = max_objects
        self._source = source
        self._session: _SessionState | None = None

    @property
    def is_active(self) -> bool:
        return self._session is not None

    @property
    def prompt(self) -> str | None:
        return None if self._session is None else self._session.prompt

    def start_session(self, prompt: str) -> None:
        normalized = prompt.strip()
        if not normalized:
            raise VisionPipelineError("prompt must not be empty")
        self._tracker.reset()
        self._session = _SessionState(
            prompt=normalized,
            seeded_detections=[],
            seeded=False,
        )

    def stop_session(self) -> None:
        self._tracker.reset()
        self._session = None

    def process_frame(
        self,
        *,
        frame_id: int,
        timestamp_ms: int,
        image_bgr: np.ndarray,
    ) -> VisionFrameResult | None:
        session = self._session
        if session is None:
            return None

        if not session.seeded:
            detections = self._detector.detect(image_bgr, session.prompt)
            if not detections:
                raise VisionPipelineError(
                    f"no detections found for prompt '{session.prompt}'"
                )
            detections = detections[: self._max_objects]
            masks = self._segmenter.segment(image_bgr, detections)
            if len(masks) != len(detections):
                raise VisionPipelineError("segmenter returned mismatched mask count")
            propagated = self._tracker.bootstrap(image_bgr, detections, masks)
            if len(propagated) != len(detections):
                raise VisionPipelineError("tracker bootstrap returned mismatched masks")
            session.seeded_detections = detections
            session.seeded = True
            object_masks = [
                (detection.object_id, mask)
                for detection, mask in zip(detections, propagated, strict=True)
            ]
        else:
            object_masks = self._tracker.track(image_bgr)

        tracked_objects = self._build_tracked_objects(
            session.seeded_detections,
            object_masks,
        )
        height, width = image_bgr.shape[:2]
        return VisionFrameResult(
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
            frame_width=width,
            frame_height=height,
            prompt=session.prompt,
            objects=tracked_objects,
            source=self._source,
        )

    def _build_tracked_objects(
        self,
        detections: list[DetectionCandidate],
        object_masks: list[tuple[int, np.ndarray]],
    ) -> list[TrackedMask]:
        metadata = {item.object_id: item for item in detections}
        tracked: list[TrackedMask] = []
        for object_id, raw_mask in object_masks:
            detection = metadata.get(object_id)
            if detection is None:
                continue
            mask = np.asarray(raw_mask, dtype=bool)
            if mask.ndim > 2:
                mask = np.squeeze(mask)
            if mask.ndim != 2 or not mask.any():
                continue
            bbox = bbox_from_mask(mask)
            if bbox is None:
                continue
            tracked.append(
                TrackedMask(
                    object_id=object_id,
                    label=detection.label,
                    score=detection.score,
                    box_xyxy=bbox,
                    area=int(mask.sum()),
                    mask_rle=encode_binary_mask(mask),
                )
            )
        return tracked
