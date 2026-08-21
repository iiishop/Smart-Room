from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
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

DEFAULT_VISION_PROMPT = "chair . table . person . sofa . monitor . cup . bottle . book . backpack . door"


@dataclass(slots=True)
class VisionModelSettings:
    enabled: bool
    grounding_dino_model: str
    sam2_model: str
    box_threshold: float
    text_threshold: float
    detect_interval: int
    max_objects: int
    frame_cache_dir: str

    @classmethod
    def from_env(cls) -> "VisionModelSettings":
        return cls(
            enabled=os.getenv("QUEST3_VISION_ENABLED", "1").lower()
            in {"1", "true", "yes", "on"},
            grounding_dino_model=os.getenv(
                "QUEST3_GROUNDING_DINO_MODEL",
                "IDEA-Research/grounding-dino-tiny",
            ),
            sam2_model=os.getenv(
                "QUEST3_SAM2_MODEL",
                "facebook/sam2.1-hiera-tiny",
            ),
            box_threshold=float(os.getenv("QUEST3_GROUNDING_BOX_THRESHOLD", "0.25")),
            text_threshold=float(os.getenv("QUEST3_GROUNDING_TEXT_THRESHOLD", "0.25")),
            detect_interval=max(1, int(os.getenv("QUEST3_VISION_DETECT_INTERVAL", "30"))),
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


class PerFrameSegmenterTracker(VideoTrackingProvider):
    """Per-frame segmentation tracker — replaces SAM2 video tracking.

    Instead of temporal propagation, this runs the image segmenter on every
    frame using the stored detection boxes.  Simpler but loses inter-frame
    mask consistency and is more expensive per frame.
    """

    def __init__(self, segmenter: ImageSegmentationProvider) -> None:
        self._segmenter = segmenter
        self._detections: list[DetectionCandidate] = []

    def reset(self) -> None:
        self._detections = []

    def bootstrap(
        self,
        image_bgr: np.ndarray,
        detections: list[DetectionCandidate],
        masks: list[np.ndarray],
    ) -> list[np.ndarray]:
        self._detections = list(detections)
        return masks

    def track(self, image_bgr: np.ndarray) -> list[tuple[int, np.ndarray]]:
        if not self._detections:
            return []
        masks = self._segmenter.segment(image_bgr, self._detections)
        return [
            (self._detections[i].object_id, mask)
            for i, mask in enumerate(masks)
        ]

    def add_objects(
        self,
        detections: list[DetectionCandidate],
        masks: list[np.ndarray],
    ) -> list[tuple[int, np.ndarray]]:
        self._detections.extend(detections)
        return [
            (detection.object_id, mask)
            for detection, mask in zip(detections, masks, strict=True)
        ]


class Sam2VideoRepropagatingTracker(VideoTrackingProvider):
    def __init__(self, *, model_name: str, frame_cache_dir: str = "") -> None:
        try:
            from sam2.sam2_video_predictor import SAM2VideoPredictor
        except ImportError as exc:
            raise VisionPipelineError(
                "SAM2 video predictor is missing. Install the optional sam2 dependency."
            ) from exc

        self._predictor = SAM2VideoPredictor.from_pretrained(model_name)
        self._frame_cache_dir = frame_cache_dir
        self._state: Any | None = None
        self._frames: list[Any] = []
        self._frame_load_config: dict[str, Any] = {
            "first_frame_num": 0,
            "max_frames": 0,
        }
        self._tracked_object_ids: list[int] = []
        self._current_frame_index = -1

    def reset(self) -> None:
        if self._state is not None and hasattr(self._predictor, "reset_state"):
            self._predictor.reset_state(self._state)
        self._state = None
        self._frames = []
        self._frame_load_config = {"first_frame_num": 0, "max_frames": 0}
        self._tracked_object_ids = []
        self._current_frame_index = -1

    def bootstrap(
        self,
        image_bgr: np.ndarray,
        detections: list[DetectionCandidate],
        masks: list[np.ndarray],
    ) -> list[np.ndarray]:
        self.reset()
        self._append_frame(image_bgr)
        self._state = self._init_state()
        for item in detections:
            self._add_box_prompt(frame_idx=0, object_id=item.object_id, box_xyxy=item.box_xyxy)
            self._tracked_object_ids.append(item.object_id)
        return masks

    def track(self, image_bgr: np.ndarray) -> list[tuple[int, np.ndarray]]:
        if self._state is None:
            raise VisionPipelineError("tracker has not been bootstrapped")

        frame_idx = self._append_frame(image_bgr)
        latest_masks = self._propagate_frame(frame_idx)
        if not latest_masks:
            raise VisionPipelineError(
                f"SAM2 video propagation produced no masks for frame {frame_idx}"
            )
        return [
            (object_id, latest_masks[object_id])
            for object_id in self._tracked_object_ids
            if object_id in latest_masks
        ]

    def add_objects(
        self,
        detections: list[DetectionCandidate],
        masks: list[np.ndarray],
    ) -> list[tuple[int, np.ndarray]]:
        if self._state is None or self._current_frame_index < 0:
            raise VisionPipelineError("tracker has not been bootstrapped")
        if len(detections) != len(masks):
            raise VisionPipelineError("new detections and masks must have the same length")

        added: list[tuple[int, np.ndarray]] = []
        for detection, fallback_mask in zip(detections, masks, strict=True):
            frame_idx, object_ids, predicted_masks = self._add_box_prompt(
                frame_idx=self._current_frame_index,
                object_id=detection.object_id,
                box_xyxy=detection.box_xyxy,
            )
            extracted = self._extract_masks(object_ids, predicted_masks)
            mask = extracted.get(detection.object_id, np.asarray(fallback_mask, dtype=bool))
            if detection.object_id not in self._tracked_object_ids:
                self._tracked_object_ids.append(detection.object_id)
            if int(frame_idx) != self._current_frame_index:
                raise VisionPipelineError("SAM2 returned an unexpected frame index for add_objects")
            added.append((detection.object_id, mask))
        return added

    def _append_frame(self, image_bgr: np.ndarray) -> int:
        try:
            from PIL import Image
        except ImportError as exc:
            raise VisionPipelineError(
                "Pillow is required for the SAM2 streaming video tracker."
            ) from exc

        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        self._frames.append(Image.fromarray(image_rgb))
        self._frame_load_config["max_frames"] = len(self._frames)
        self._current_frame_index = len(self._frames) - 1
        return self._current_frame_index

    def _init_state(self) -> Any:
        try:
            return self._predictor.init_state(
                video_path=self._load_frame,
                frame_load_config=self._frame_load_config,
            )
        except TypeError as exc:
            raise VisionPipelineError(
                "Installed SAM2 video predictor is too old for the SAM2.1 streaming API. Upgrade the sam2 package to the latest code."
            ) from exc

    def _load_frame(self, frame_idx: int) -> Any:
        return self._frames[frame_idx]

    def _add_box_prompt(
        self,
        *,
        frame_idx: int,
        object_id: int,
        box_xyxy: tuple[int, int, int, int],
    ) -> tuple[Any, Any, Any]:
        if self._state is None:
            raise VisionPipelineError("tracker state is not initialized")
        box = np.asarray(box_xyxy, dtype=np.float32)
        return self._predictor.add_new_points_or_box(
            self._state,
            frame_idx=frame_idx,
            obj_id=object_id,
            box=box,
        )

    def _propagate_frame(self, frame_idx: int) -> dict[int, np.ndarray]:
        if self._state is None:
            raise VisionPipelineError("tracker state is not initialized")

        iterator = self._build_propagation_iterator(frame_idx)
        latest_masks: dict[int, np.ndarray] = {}
        for propagated_frame_idx, object_ids, masks in iterator:
            if int(propagated_frame_idx) != frame_idx:
                continue
            latest_masks = self._extract_masks(object_ids, masks)
        return latest_masks

    def _build_propagation_iterator(self, frame_idx: int) -> Any:
        try:
            return self._predictor.propagate_in_video(
                self._state,
                start_frame_idx=frame_idx,
                max_frame_num_to_track=1,
            )
        except TypeError:
            return self._predictor.propagate_in_video(self._state)

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


def build_default_pipeline() -> GroundedSamTrackingPipeline | None:
    pipeline_factory = os.getenv("QUEST3_VISION_PIPELINE_FACTORY", "").strip()
    if pipeline_factory:
        return _build_pipeline_from_factory(pipeline_factory)

    settings = VisionModelSettings.from_env()
    if not settings.enabled:
        return None

    detector = TransformersGroundingDinoDetector(
        model_name=settings.grounding_dino_model,
        box_threshold=settings.box_threshold,
        text_threshold=settings.text_threshold,
    )
    segmenter = Sam2ImagePredictorSegmenter(model_name=settings.sam2_model)
    tracker = PerFrameSegmenterTracker(segmenter=segmenter)
    return GroundedSamTrackingPipeline(
        detector=detector,
        segmenter=segmenter,
        tracker=tracker,
        detect_interval=settings.detect_interval,
        max_objects=settings.max_objects,
    )


def _build_pipeline_from_factory(factory_path: str) -> GroundedSamTrackingPipeline | None:
    module_name, sep, attr_name = factory_path.partition(":")
    if not sep or not module_name or not attr_name:
        raise VisionPipelineError(
            "QUEST3_VISION_PIPELINE_FACTORY must be in the form 'module:function'"
        )

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise VisionPipelineError(
            f"failed to import vision pipeline factory module '{module_name}'"
        ) from exc

    factory = getattr(module, attr_name, None)
    if factory is None or not callable(factory):
        raise VisionPipelineError(
            f"vision pipeline factory '{factory_path}' was not found or is not callable"
        )

    return factory()
