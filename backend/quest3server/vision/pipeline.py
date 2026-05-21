from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .metrics import elapsed_ms, sample_gpu_memory_mb, start_timer
from .rle import bbox_from_mask, encode_binary_mask
from .types import (
    DetectionCandidate,
    TrackedMask,
    VisionFrameResult,
    VisionProcessingMode,
)


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

    def add_objects(
        self,
        detections: list[DetectionCandidate],
        masks: list[np.ndarray],
    ) -> list[tuple[int, np.ndarray]]: ...


@dataclass(slots=True)
class _SessionState:
    prompt: str
    detections_by_id: dict[int, DetectionCandidate]
    seeded: bool
    next_object_id: int


class GroundedSamTrackingPipeline:
    def __init__(
        self,
        detector: DetectionProvider,
        segmenter: ImageSegmentationProvider,
        tracker: VideoTrackingProvider,
        *,
        max_objects: int = 8,
        detect_interval: int = 30,
        source: str = "grounding-dino+sam2",
    ) -> None:
        self._detector = detector
        self._segmenter = segmenter
        self._tracker = tracker
        self._max_objects = max_objects
        self._detect_interval = max(1, int(detect_interval))
        self._source = source
        self._session: _SessionState | None = None

    @property
    def is_active(self) -> bool:
        return self._session is not None

    @property
    def prompt(self) -> str | None:
        return None if self._session is None else self._session.prompt

    @property
    def detect_interval(self) -> int:
        return self._detect_interval

    @property
    def max_objects(self) -> int:
        return self._max_objects

    @property
    def source(self) -> str:
        return self._source

    def start_session(self, prompt: str) -> None:
        normalized = prompt.strip()
        if not normalized:
            raise VisionPipelineError("prompt must not be empty")
        self._tracker.reset()
        self._session = _SessionState(
            prompt=normalized,
            detections_by_id={},
            seeded=False,
            next_object_id=1,
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
        started_at = start_timer()
        session = self._session
        if session is None:
            return None

        mode: VisionProcessingMode
        if self._should_run_detection(session, frame_id):
            mode = "detection"
            detections = self._detector.detect(image_bgr, session.prompt)
            if not detections and not session.seeded:
                raise VisionPipelineError(
                    f"no detections found for prompt '{session.prompt}'"
                )
            if not session.seeded:
                detections = self._assign_object_ids(
                    session,
                    detections[: self._max_objects],
                )
                masks = self._segmenter.segment(image_bgr, detections)
                if len(masks) != len(detections):
                    raise VisionPipelineError("segmenter returned mismatched mask count")
                propagated = self._tracker.bootstrap(image_bgr, detections, masks)
                if len(propagated) != len(detections):
                    raise VisionPipelineError("tracker bootstrap returned mismatched masks")
                session.detections_by_id = {item.object_id: item for item in detections}
                session.seeded = True
                object_masks = [
                    (detection.object_id, mask)
                    for detection, mask in zip(detections, propagated, strict=True)
                ]
            else:
                object_masks = self._tracker.track(image_bgr)
                additions = self._detect_new_objects(session, detections, object_masks)
                if additions:
                    masks = self._segmenter.segment(image_bgr, additions)
                    if len(masks) != len(additions):
                        raise VisionPipelineError(
                            "segmenter returned mismatched mask count for new objects"
                        )
                    added_masks = self._tracker.add_objects(additions, masks)
                    if len(added_masks) != len(additions):
                        raise VisionPipelineError(
                            "tracker add_objects returned mismatched mask count"
                        )
                    for detection in additions:
                        session.detections_by_id[detection.object_id] = detection
                    object_masks = self._merge_object_masks(object_masks, added_masks)
        else:
            mode = "propagation"
            object_masks = self._tracker.track(image_bgr)

        tracked_objects = self._build_tracked_objects(
            session.detections_by_id,
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
            mode=mode,
            process_time_ms=elapsed_ms(started_at),
            gpu_memory_mb=sample_gpu_memory_mb(),
        )

    def _should_run_detection(self, session: _SessionState, frame_id: int) -> bool:
        return (not session.seeded) or (frame_id % self._detect_interval == 0)

    def _build_tracked_objects(
        self,
        detections_by_id: dict[int, DetectionCandidate],
        object_masks: list[tuple[int, np.ndarray]],
    ) -> list[TrackedMask]:
        tracked: list[TrackedMask] = []
        for object_id, raw_mask in object_masks:
            detection = detections_by_id.get(object_id)
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

    def _assign_object_ids(
        self,
        session: _SessionState,
        detections: list[DetectionCandidate],
    ) -> list[DetectionCandidate]:
        assigned: list[DetectionCandidate] = []
        for detection in detections:
            assigned.append(
                DetectionCandidate(
                    object_id=session.next_object_id,
                    label=detection.label,
                    score=detection.score,
                    box_xyxy=detection.box_xyxy,
                )
            )
            session.next_object_id += 1
        return assigned

    def _detect_new_objects(
        self,
        session: _SessionState,
        detected: list[DetectionCandidate],
        object_masks: list[tuple[int, np.ndarray]],
    ) -> list[DetectionCandidate]:
        remaining_capacity = self._max_objects - len(session.detections_by_id)
        if remaining_capacity <= 0:
            return []

        tracked_objects = self._build_tracked_objects(
            session.detections_by_id,
            object_masks,
        )
        existing_boxes = [item.box_xyxy for item in session.detections_by_id.values()]
        existing_boxes.extend(item.box_xyxy for item in tracked_objects)
        if not detected:
            return []

        additions: list[DetectionCandidate] = []
        for detection in detected:
            if any(
                self._boxes_match(existing_box, detection.box_xyxy)
                for existing_box in existing_boxes
            ):
                continue
            additions.append(detection)
            existing_boxes.append(detection.box_xyxy)
            if len(additions) >= remaining_capacity:
                break

        return self._assign_object_ids(session, additions)

    @staticmethod
    def _merge_object_masks(
        existing: list[tuple[int, np.ndarray]],
        additions: list[tuple[int, np.ndarray]],
    ) -> list[tuple[int, np.ndarray]]:
        merged = {object_id: mask for object_id, mask in existing}
        for object_id, mask in additions:
            merged[object_id] = mask
        return list(merged.items())

    @staticmethod
    def _boxes_match(
        lhs: tuple[int, int, int, int],
        rhs: tuple[int, int, int, int],
    ) -> bool:
        left = max(lhs[0], rhs[0])
        top = max(lhs[1], rhs[1])
        right = min(lhs[2], rhs[2])
        bottom = min(lhs[3], rhs[3])
        if right <= left or bottom <= top:
            return False

        overlap = (right - left) * (bottom - top)
        lhs_area = max(1, (lhs[2] - lhs[0]) * (lhs[3] - lhs[1]))
        rhs_area = max(1, (rhs[2] - rhs[0]) * (rhs[3] - rhs[1]))
        union = lhs_area + rhs_area - overlap
        return (overlap / min(lhs_area, rhs_area) >= 0.6) or (overlap / union >= 0.3)
