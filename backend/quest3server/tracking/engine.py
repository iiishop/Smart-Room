from __future__ import annotations

import logging
import re
from typing import Any

import cv2
import numpy as np
import torch
from PIL import Image

from .types import TrackState, TrackingResult

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Verified API references:
#
# SAM 2.1 box prompt (image predictor):
#   https://huggingface.co/docs/transformers/model_doc/sam2
#   predictor.predict(box=..., multimask_output=False)
#
# Florence-2 object detection:
#   https://huggingface.co/docs/transformers/model_doc/florence2
#   https://huggingface.co/florence-community/Florence-2-base
#   task_prompt="<OD>" → bboxes + class labels
#
# Florence-2 region description (fallback):
#   task_prompt="<REGION_TO_DESCRIPTION>" → natural language description
# ═══════════════════════════════════════════════════════════════════════

_LOC_RE = re.compile(r"</?loc_\d+>", re.IGNORECASE)
_LOC_PATTERN = re.compile(r"<loc_\d+>", re.IGNORECASE)


class TrackingEngine:
    """Two-stage object detection + segmentation engine.

    Stage 1 ― Florence-2 <OD> detects all objects → compact detection bboxes + labels.
    Stage 2 ― Find which bbox contains the cursor point → SAM2 box-prompt segments
              within that bbox (much more precise than free-form point prompt).

    References
    ----------
    - Florence-2 <OD> task:
      https://huggingface.co/florence-community/Florence-2-base
    - SAM2 box prompt:
      https://huggingface.co/docs/transformers/model_doc/sam2
    """

    _IMAGE_PREDICTOR_MODEL = "facebook/sam2.1-hiera-tiny"
    _FLORENCE2_MODEL = "florence-community/Florence-2-base"

    def __init__(self) -> None:
        self._label = ""
        self._score = 0.0
        self._box_xyxy: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._state = TrackState.IDLE

        self._sam2_image: Any = None
        self._florence2: Any = None
        self._florence2_proc: Any = None

    # ── public API ────────────────────────────────────────────────────

    @property
    def state(self) -> TrackState:
        return self._state

    @property
    def label(self) -> str:
        return self._label

    def detect(self, pixel_x: float, pixel_y: float, rgb_bgr: np.ndarray) -> TrackingResult:
        """Detect and describe the object at (pixel_x, pixel_y) in rgb_bgr.

        Two-stage pipeline:
        1. Florence-2 <OD> → all detection bboxes + labels
        2. Find which bbox contains the cursor → SAM2 box prompt → refined mask → bbox
        """
        self._ensure_models()

        h, w = rgb_bgr.shape[:2]
        px = int(np.clip(pixel_x, 0, w - 1))
        py = int(np.clip(pixel_y, 0, h - 1))
        rgb_rgb = cv2.cvtColor(rgb_bgr, cv2.COLOR_BGR2RGB)

        # ══════ Stage 1: Florence-2 detection ══════
        detections = self._run_detection(rgb_rgb, w, h)
        if not detections:
            # Fallback to point prompt if detection fails
            return self._fallback_point_prompt(rgb_rgb, px, py)

        # Find the smallest detection bbox containing the cursor point
        target_bbox, target_label, target_score = self._find_containing_bbox(
            detections, px, py
        )

        if target_bbox is None:
            # No detection bbox contains the point — fallback
            return self._fallback_point_prompt(rgb_rgb, px, py)

        # ══════ Stage 2: SAM2 box-prompt refinement ══════
        refined_bbox = self._refine_with_sam2_box(rgb_rgb, target_bbox)

        # Clean label of any residual location tokens
        label = _clean_label(target_label)

        self._label = label
        self._score = target_score
        self._box_xyxy = refined_bbox
        self._state = TrackState.TRACKING

        logger.info("Detected: %s (score=%.3f, bbox=%s)", label, target_score, refined_bbox)

        return TrackingResult(
            object_id=1,
            state=TrackState.TRACKING,
            label=label,
            score=target_score,
            box_xyxy=refined_bbox,
            center_pixel=(
                (refined_bbox[0] + refined_bbox[2]) / 2.0,
                (refined_bbox[1] + refined_bbox[3]) / 2.0,
            ),
        )

    def stop(self) -> None:
        self._state = TrackState.IDLE
        self._label = ""
        self._box_xyxy = (0, 0, 0, 0)

    # ══════ Stage 1: Florence-2 detection ══════

    def _run_detection(
        self, rgb: np.ndarray, img_w: int, img_h: int
    ) -> list[tuple[list[int], str, float]]:
        """Run Florence-2 <OD> and return list of (bbox, label, score).

        Also tries <OPEN_VOCABULARY_DETECTION> as fallback if <OD> returns nothing.
        """
        for task_prompt in ("<OD>", "<OPEN_VOCABULARY_DETECTION>"):
            try:
                raw = self._florence2_infer(rgb, task_prompt, max_tokens=768)
                parsed = self._florence2_proc.post_process_generation(
                    raw, task=task_prompt, image_size=(img_w, img_h),
                )
                regions = parsed.get(task_prompt, {})
                bboxes = regions.get("bboxes", [])
                labels = regions.get("labels", [])
                if not bboxes:
                    continue

                results = []
                for i, (box, label) in enumerate(zip(bboxes, labels)):
                    x0, y0, x1, y1 = [int(v) for v in box]
                    results.append(([x0, y0, x1, y1], _clean_label(str(label)), 0.85))
                if results:
                    return results
            except Exception as exc:
                logger.warning("Florence-2 %s failed: %s", task_prompt, exc)
        return []

    def _find_containing_bbox(
        self,
        detections: list[tuple[list[int], str, float]],
        px: int, py: int,
    ) -> tuple[list[int] | None, str, float]:
        """Find the smallest detection bbox that contains (px, py)."""
        best: list[int] | None = None
        best_label = ""
        best_score = 0.0
        best_area = float("inf")

        for bbox, label, score in detections:
            x0, y0, x1, y1 = bbox
            if x0 <= px <= x1 and y0 <= py <= y1:
                area = (x1 - x0) * (y1 - y0)
                if area < best_area:
                    best_area = area
                    best = bbox
                    best_label = label
                    best_score = score

        return best, best_label, best_score

    # ══════ Stage 2: SAM2 box-prompt refinement ══════

    def _refine_with_sam2_box(
        self, rgb: np.ndarray, rough_bbox: list[int]
    ) -> tuple[int, int, int, int]:
        """Refine a detection bbox using SAM2 box prompt.

        The detection bbox constrains SAM2 to that region, giving a tight mask.
        """
        x0, y0, x1, y1 = rough_bbox
        input_box = np.array([[x0, y0, x1, y1]], dtype=np.float32)

        try:
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                self._sam2_image.set_image(rgb)
                masks, scores, _ = self._sam2_image.predict(
                    box=input_box,
                    multimask_output=False,
                )

            mask = np.asarray(masks[0], dtype=bool)
            if mask.any():
                bbox_y, bbox_x = np.where(mask)
                return (
                    int(bbox_x.min()), int(bbox_y.min()),
                    int(bbox_x.max()), int(bbox_y.max()),
                )
        except Exception as exc:
            logger.warning("SAM2 box prompt failed, using detection bbox: %s", exc)

        # Fallback: return the detection bbox as-is
        return (x0, y0, x1, y1)

    # ══════ Fallback: SAM2 point prompt ══════

    def _fallback_point_prompt(self, rgb: np.ndarray, px: int, py: int) -> TrackingResult:
        """Fallback when detection fails: use SAM2 point prompt directly."""
        point = np.array([[px, py]], dtype=np.float32)
        try:
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                self._sam2_image.set_image(rgb)
                masks, scores, _ = self._sam2_image.predict(
                    point_coords=point,
                    point_labels=np.array([1]),
                    multimask_output=False,
                )
            mask = np.asarray(masks[0], dtype=bool)
            if not mask.any():
                self._state = TrackState.IDLE
                return TrackingResult(
                    object_id=0, state=TrackState.IDLE, label="", score=0.0,
                    box_xyxy=(0, 0, 0, 0),
                )
            bbox_y, bbox_x = np.where(mask)
            bbox = (int(bbox_x.min()), int(bbox_y.min()), int(bbox_x.max()), int(bbox_y.max()))
        except Exception:
            self._state = TrackState.IDLE
            return TrackingResult(
                object_id=0, state=TrackState.IDLE, label="", score=0.0,
                box_xyxy=(0, 0, 0, 0),
            )

        # Try to get label from region description
        label = self._describe_region(rgb, *bbox)

        self._label = label
        self._score = float(scores[0])
        self._box_xyxy = bbox
        self._state = TrackState.TRACKING

        return TrackingResult(
            object_id=1,
            state=TrackState.TRACKING,
            label=label,
            score=float(scores[0]),
            box_xyxy=bbox,
            center_pixel=((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0),
        )

    # ══════ Florence-2 helpers ══════

    def _florence2_infer(self, rgb: np.ndarray, task: str, max_tokens: int = 512) -> str:
        """Run Florence-2 inference with a task prompt, return raw decoded text."""
        pil_img = Image.fromarray(rgb)
        inputs = self._florence2_proc(
            text=task, images=pil_img, return_tensors="pt",
        ).to("cuda", torch.bfloat16)

        with torch.inference_mode():
            generated_ids = self._florence2.generate(
                **inputs, max_new_tokens=max_tokens, num_beams=3,
            )
        return self._florence2_proc.batch_decode(
            generated_ids, skip_special_tokens=False,
        )[0]

    def _describe_region(self, rgb: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> str:
        """Fallback: Florence-2 <REGION_TO_DESCRIPTION> for a specific bbox.

        Location tokens follow the Florence-2 convention (0-999 normalized):
        https://huggingface.co/florence-community/Florence-2-base
        """
        h, w = rgb.shape[:2]
        loc_x0 = int(x0 / max(w, 1) * 999)
        loc_y0 = int(y0 / max(h, 1) * 999)
        loc_x1 = int(x1 / max(w, 1) * 999)
        loc_y1 = int(y1 / max(h, 1) * 999)

        task = "<REGION_TO_DESCRIPTION>"
        text = f"{task}<loc_{loc_x0}><loc_{loc_y0}><loc_{loc_x1}><loc_{loc_y1}>"

        try:
            raw = self._florence2_infer(rgb, text, max_tokens=128)
            parsed = self._florence2_proc.post_process_generation(
                raw, task=task, image_size=(w, h),
            )
            result = parsed.get(task, "object")
            return _clean_label(result)
        except Exception as exc:
            logger.warning("Florence-2 region description failed: %s", exc)
            return "object"

    # ══════ Model loading ══════

    def _ensure_models(self) -> None:
        if self._sam2_image is not None:
            return
        from sam2.sam2_image_predictor import SAM2ImagePredictor
        from transformers import AutoProcessor, Florence2ForConditionalGeneration

        self._sam2_image = SAM2ImagePredictor.from_pretrained(self._IMAGE_PREDICTOR_MODEL)
        self._florence2 = Florence2ForConditionalGeneration.from_pretrained(
            self._FLORENCE2_MODEL,
            torch_dtype=torch.bfloat16,
        ).to("cuda")
        self._florence2_proc = AutoProcessor.from_pretrained(self._FLORENCE2_MODEL)
        logger.info(
            "TrackingEngine models loaded (SAM2-tiny + Florence-2-base, %.1f GB VRAM)",
            torch.cuda.memory_allocated() / 1024 ** 3,
        )


def _clean_label(text: str) -> str:
    """Strip Florence-2 location tokens and whitespace from label text."""
    if not text:
        return "object"
    cleaned = _LOC_RE.sub("", text).strip()
    # Remove any remaining standalone loc tokens
    cleaned = _LOC_PATTERN.sub("", cleaned).strip()
    # Collapse multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned if cleaned else "object"
