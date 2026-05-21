from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from shutil import rmtree
from typing import Any

import cv2
import numpy as np

from .pipeline import (
    DetectionProvider,
    GroundedSamTrackingPipeline,
    ImageSegmentationProvider,
    VideoTrackingProvider,
    VisionPipelineError,
)
from .types import DetectionCandidate


@dataclass(slots=True)
class VisionModelSettings:
    enabled: bool
    grounding_dino_model: str
    sam2_model: str
    box_threshold: float
    text_threshold: float
    max_objects: int
    frame_cache_dir: str

    @classmethod
    def from_env(cls) -> "VisionModelSettings":
        return cls(
            enabled=os.getenv("QUEST3_VISION_ENABLED", "0").lower()
            in {"1", "true", "yes", "on"},
            grounding_dino_model=os.getenv(
                "QUEST3_GROUNDING_DINO_MODEL",
                "IDEA-Research/grounding-dino-tiny",
            ),
            sam2_model=os.getenv(
                "QUEST3_SAM2_MODEL",
                "facebook/sam2-hiera-tiny",
            ),
            box_threshold=float(os.getenv("QUEST3_GROUNDING_BOX_THRESHOLD", "0.25")),
            text_threshold=float(os.getenv("QUEST3_GROUNDING_TEXT_THRESHOLD", "0.25")),
            max_objects=int(os.getenv("QUEST3_VISION_MAX_OBJECTS", "8")),
            frame_cache_dir=os.getenv("QUEST3_VISION_FRAME_CACHE_DIR", ""),
        )


class TransformersGroundingDinoDetector(DetectionProvider):
    def __init__(
        self,
        *,
        model_name: str,
        box_threshold: float,
        text_threshold: float,
    ) -> None:
        try:
            import torch
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        except ImportError as exc:
            raise VisionPipelineError(
                "Grounding DINO dependencies are missing. Install the vision extras."
            ) from exc

        self._torch = torch
        self._processor = AutoProcessor.from_pretrained(model_name)
        self._model = AutoModelForZeroShotObjectDetection.from_pretrained(model_name)
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model.to(self._device)
        self._threshold = box_threshold
        self._text_threshold = text_threshold

    def detect(self, image_bgr: np.ndarray, prompt: str) -> list[DetectionCandidate]:
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        inputs = self._processor(
            images=image_rgb,
            text=prompt,
            return_tensors="pt",
        )
        inputs = {key: value.to(self._device) for key, value in inputs.items()}

        with self._torch.inference_mode():
            outputs = self._model(**inputs)

        height, width = image_rgb.shape[:2]
        results = self._processor.post_process_grounded_object_detection(
            outputs,
            inputs["input_ids"],
            threshold=self._threshold,
            text_threshold=self._text_threshold,
            target_sizes=[(height, width)],
        )[0]

        candidates: list[DetectionCandidate] = []
        scores = results.get("scores", [])
        boxes = results.get("boxes", [])
        labels = results.get("text_labels") or results.get("labels") or []
        for index, (score, box, label) in enumerate(
            zip(scores, boxes, labels, strict=False),
            start=1,
        ):
            x0, y0, x1, y1 = [int(round(float(value))) for value in box]
            candidates.append(
                DetectionCandidate(
                    object_id=index,
                    label=str(label),
                    score=float(score),
                    box_xyxy=(x0, y0, x1, y1),
                )
            )
        return candidates


class Sam2ImagePredictorSegmenter(ImageSegmentationProvider):
    def __init__(self, *, model_name: str) -> None:
        try:
            from sam2.sam2_image_predictor import SAM2ImagePredictor
        except ImportError as exc:
            raise VisionPipelineError(
                "SAM2 image predictor is missing. Install the optional sam2 dependency."
            ) from exc

        self._predictor = SAM2ImagePredictor.from_pretrained(model_name)

    def segment(
        self, image_bgr: np.ndarray, detections: list[DetectionCandidate]
    ) -> list[np.ndarray]:
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        self._predictor.set_image(image_rgb)
        masks: list[np.ndarray] = []
        for detection in detections:
            box = np.asarray(detection.box_xyxy, dtype=np.float32)
            predicted_masks, _, _ = self._predictor.predict(
                box=box[None, :],
                multimask_output=False,
            )
            mask = np.asarray(predicted_masks[0], dtype=bool)
            masks.append(mask)
        return masks


class Sam2VideoRepropagatingTracker(VideoTrackingProvider):
    def __init__(self, *, model_name: str, frame_cache_dir: str = "") -> None:
        try:
            from sam2.sam2_video_predictor import SAM2VideoPredictor
        except ImportError as exc:
            raise VisionPipelineError(
                "SAM2 video predictor is missing. Install the optional sam2 dependency."
            ) from exc

        self._predictor = SAM2VideoPredictor.from_pretrained(model_name)
        self._root_dir = frame_cache_dir or tempfile.mkdtemp(prefix="quest3-vision-")
        self._owns_root_dir = not frame_cache_dir
        self._session_dir: Path | None = None
        self._seed_boxes: list[tuple[int, tuple[int, int, int, int]]] = []
        self._next_frame_index = 0

    def reset(self) -> None:
        if self._session_dir is not None and self._session_dir.exists():
            rmtree(self._session_dir, ignore_errors=True)
        self._session_dir = None
        self._seed_boxes = []
        self._next_frame_index = 0

    def bootstrap(
        self,
        image_bgr: np.ndarray,
        detections: list[DetectionCandidate],
        masks: list[np.ndarray],
    ) -> list[np.ndarray]:
        self.reset()
        session_dir = Path(self._root_dir) / "current"
        session_dir.mkdir(parents=True, exist_ok=True)
        self._session_dir = session_dir
        self._seed_boxes = [(item.object_id, item.box_xyxy) for item in detections]
        self._write_frame(image_bgr)
        self._next_frame_index = 1
        return masks

    def track(self, image_bgr: np.ndarray) -> list[tuple[int, np.ndarray]]:
        if self._session_dir is None:
            raise VisionPipelineError("tracker has not been bootstrapped")

        self._write_frame(image_bgr)
        latest_index = self._next_frame_index
        self._next_frame_index += 1
        state = self._predictor.init_state(video_path=str(self._session_dir))
        for object_id, box_xyxy in self._seed_boxes:
            box = np.asarray(box_xyxy, dtype=np.float32)
            self._predictor.add_new_points_or_box(
                state,
                frame_idx=0,
                obj_id=object_id,
                box=box,
            )

        latest_masks: dict[int, np.ndarray] = {}
        for frame_idx, object_ids, masks in self._predictor.propagate_in_video(state):
            if int(frame_idx) != latest_index:
                continue
            latest_masks = self._extract_masks(object_ids, masks)

        if not latest_masks:
            raise VisionPipelineError(
                f"SAM2 video propagation produced no masks for frame {latest_index}"
            )
        return [(object_id, latest_masks[object_id]) for object_id, _ in self._seed_boxes]

    def _write_frame(self, image_bgr: np.ndarray) -> None:
        if self._session_dir is None:
            raise VisionPipelineError("tracker session directory is not initialized")
        path = self._session_dir / f"{self._next_frame_index:06d}.jpg"
        ok, encoded = cv2.imencode(".jpg", image_bgr)
        if not ok:
            raise VisionPipelineError("failed to encode video frame for SAM2 tracker")
        path.write_bytes(encoded.tobytes())

    @staticmethod
    def _extract_masks(object_ids: Any, masks: Any) -> dict[int, np.ndarray]:
        object_id_list = [int(item) for item in object_ids]
        mask_array = np.asarray(masks)
        if mask_array.ndim == 4:
            mask_array = mask_array[:, 0, :, :]
        if mask_array.ndim != 3:
            raise VisionPipelineError("unexpected SAM2 mask tensor shape")
        return {
            object_id: np.asarray(mask_array[index] > 0, dtype=bool)
            for index, object_id in enumerate(object_id_list)
        }

    def __del__(self) -> None:
        if self._owns_root_dir:
            rmtree(self._root_dir, ignore_errors=True)


def build_default_pipeline() -> GroundedSamTrackingPipeline | None:
    settings = VisionModelSettings.from_env()
    if not settings.enabled:
        return None

    detector = TransformersGroundingDinoDetector(
        model_name=settings.grounding_dino_model,
        box_threshold=settings.box_threshold,
        text_threshold=settings.text_threshold,
    )
    segmenter = Sam2ImagePredictorSegmenter(model_name=settings.sam2_model)
    tracker = Sam2VideoRepropagatingTracker(
        model_name=settings.sam2_model,
        frame_cache_dir=settings.frame_cache_dir,
    )
    return GroundedSamTrackingPipeline(
        detector=detector,
        segmenter=segmenter,
        tracker=tracker,
        max_objects=settings.max_objects,
    )
