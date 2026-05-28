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
    _CLIP_MODEL = "openai/clip-vit-base-patch32"
    _CLIP_SIM_THRESHOLD = 0.18  # below this: try region description as fallback label

    def __init__(self) -> None:
        self._label = ""
        self._score = 0.0
        self._box_xyxy: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._state = TrackState.IDLE

        self._sam2_image: Any = None
        self._florence2: Any = None
        self._florence2_proc: Any = None
        self._clip_model: Any = None
        self._clip_proc: Any = None

    # ── public API ────────────────────────────────────────────────────

    @property
    def state(self) -> TrackState:
        return self._state

    @property
    def label(self) -> str:
        return self._label

    def detect(self, pixel_x: float, pixel_y: float, rgb_bgr: np.ndarray) -> TrackingResult:
        """Detect and describe the object at (pixel_x, pixel_y) in rgb_bgr.

        Three-stage pipeline:
        1. Florence-2 multi-task ensemble → all detection bboxes + labels
        2. Find bbox containing cursor → CLIP label verification → calibrated score
        3. SAM2 box-prompt refinement → tight mask → clean bbox
        """
        self._ensure_models()

        h, w = rgb_bgr.shape[:2]
        px = int(np.clip(pixel_x, 0, w - 1))
        py = int(np.clip(pixel_y, 0, h - 1))
        rgb_rgb = cv2.cvtColor(rgb_bgr, cv2.COLOR_BGR2RGB)

        # ══════ Stage 1: Florence-2 detection ══════
        detections = self._run_detection(rgb_rgb, w, h)
        if not detections:
            return self._fallback_point_prompt(rgb_rgb, px, py)

        # Find the smallest detection bbox containing the cursor point
        target_bbox, target_label, _ = self._find_containing_bbox(
            detections, px, py
        )

        if target_bbox is None:
            return self._fallback_point_prompt(rgb_rgb, px, py)

        # ══════ CLIP label verification ══════
        verified_label, clip_score = self._verify_label_with_clip(
            rgb_rgb, target_bbox, target_label,
        )
        # If CLIP strongly disagrees with the label, try region description
        if clip_score < self._CLIP_SIM_THRESHOLD:
            logger.warning(
                "CLIP verification low (%.3f) for '%s', trying region description",
                clip_score, verified_label,
            )
            alt_label = self._describe_region(rgb_rgb, *target_bbox)
            if alt_label != verified_label and alt_label != "object":
                alt_score, _ = self._verify_label_with_clip(rgb_rgb, target_bbox, alt_label)
                if alt_score > clip_score:
                    verified_label = alt_label
                    clip_score = alt_score

        # ══════ Stage 3: SAM2 box-prompt refinement ══════
        refined_bbox = self._refine_with_sam2_box(rgb_rgb, target_bbox)

        label = _clean_label(verified_label)

        self._label = label
        self._score = clip_score
        self._box_xyxy = refined_bbox
        self._state = TrackState.TRACKING

        logger.info(
            "Detected: %s (CLIP=%.3f, bbox=%s)", label, clip_score, refined_bbox,
        )

        return TrackingResult(
            object_id=1,
            state=TrackState.TRACKING,
            label=label,
            score=clip_score,
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

    # ══════ Stage 1: Florence-2 multi-task ensemble detection ══════

    _DETECTION_IOU_THRESHOLD = 0.5
    _DRC_MIN_BBOX_AREA = 400  # filter tiny regions from dense captioning

    def _run_detection(
        self, rgb: np.ndarray, img_w: int, img_h: int
    ) -> list[tuple[list[int], str, float]]:
        """Run Florence-2 multi-task ensemble: <OD> + <DENSE_REGION_CAPTION>.

        <OD> provides reliable detection bboxes with concise class labels.
        <DENSE_REGION_CAPTION> generates richer descriptive labels and often
        catches objects missed by <OD> (small, occluded, or unusual objects).

        Merge strategy:
        - Overlapping bboxes → keep OD bbox, replace label with DRC description.
        - Non-overlapping DRC bboxes → added to pool (objects <OD> missed).
        - Falls back to <OPEN_VOCABULARY_DETECTION> if both tasks return empty.
        """
        od_detections = self._run_single_task(rgb, img_w, img_h, "<OD>", max_tokens=768)
        drc_detections = self._run_single_task(rgb, img_w, img_h, "<DENSE_REGION_CAPTION>", max_tokens=512)

        # Filter DRC: drop tiny regions (noise from dense captioning)
        drc_detections = [
            (bbox, label, score) for bbox, label, score in drc_detections
            if (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) >= self._DRC_MIN_BBOX_AREA
        ]

        if not od_detections and not drc_detections:
            logger.info("Ensemble empty, falling back to <OPEN_VOCABULARY_DETECTION>")
            return self._run_single_task(rgb, img_w, img_h, "<OPEN_VOCABULARY_DETECTION>", max_tokens=768)

        merged = self._merge_od_drc(od_detections, drc_detections)
        logger.info(
            "Ensemble: %d OD + %d DRC → %d merged detections",
            len(od_detections), len(drc_detections), len(merged),
        )
        return merged

    def _run_single_task(
        self, rgb: np.ndarray, img_w: int, img_h: int,
        task: str, max_tokens: int = 768,
    ) -> list[tuple[list[int], str, float]]:
        """Run a single Florence-2 task and return (bbox, label, score) list."""
        try:
            raw = self._florence2_infer(rgb, task, max_tokens=max_tokens)
            parsed = self._florence2_proc.post_process_generation(
                raw, task=task, image_size=(img_w, img_h),
            )
            regions = parsed.get(task, {})
            bboxes = regions.get("bboxes", [])
            labels = regions.get("labels", [])
            if not bboxes:
                return []

            results = []
            for box, label in zip(bboxes, labels):
                x0, y0, x1, y1 = [int(v) for v in box]
                results.append(([x0, y0, x1, y1], _clean_label(str(label)), 0.85))
            return results
        except Exception as exc:
            logger.warning("Florence-2 %s failed: %s", task, exc)
            return []

    def _merge_od_drc(
        self,
        od: list[tuple[list[int], str, float]],
        drc: list[tuple[list[int], str, float]],
    ) -> list[tuple[list[int], str, float]]:
        """Merge <OD> and <DENSE_REGION_CAPTION> detections.

        For each DRC detection, find the best-matching OD bbox by IoU:
        - IoU ≥ threshold → keep OD bbox, replace label with DRC label (richer).
        - No match → add DRC detection as a new entry (object <OD> missed).

        Confidence: OD-matched = 0.85, DRC-only = 0.65 (less reliable for
        detection precision but adds recall).
        """
        if not drc:
            return od
        if not od:
            return [(bbox, label, 0.65) for bbox, label, _ in drc]

        merged = list(od)  # start with OD as base
        drc_matched = [False] * len(drc)

        for di, (dr_bbox, dr_label, _) in enumerate(drc):
            best_iou = 0.0
            best_oi = -1
            for oi, (od_bbox, _, _) in enumerate(od):
                iou = self._compute_iou(dr_bbox, od_bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_oi = oi

            if best_iou >= self._DETECTION_IOU_THRESHOLD and best_oi >= 0:
                # Replace OD label with richer DRC description
                merged[best_oi] = (merged[best_oi][0], dr_label, 0.85)
                drc_matched[di] = True

        # Add unmatched DRC detections (objects <OD> missed)
        for di, (dr_bbox, dr_label, _) in enumerate(drc):
            if not drc_matched[di]:
                merged.append((dr_bbox, dr_label, 0.65))

        return merged

    @staticmethod
    def _compute_iou(
        a: list[int], b: list[int],
    ) -> float:
        """Intersection over Union for two axis-aligned bboxes."""
        x_left = max(a[0], b[0])
        y_top = max(a[1], b[1])
        x_right = min(a[2], b[2])
        y_bottom = min(a[3], b[3])
        if x_right <= x_left or y_bottom <= y_top:
            return 0.0

        inter = (x_right - x_left) * (y_bottom - y_top)
        area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
        area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
        return inter / (area_a + area_b - inter)

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

    # ══════ CLIP label verification ══════

    def _verify_label_with_clip(
        self, rgb: np.ndarray, bbox: list[int], label: str,
    ) -> tuple[str, float]:
        """Verify Florence-2 label by matching cropped region against label text with CLIP.

        Returns (label, cosine_similarity). The similarity score becomes the
        calibrated confidence, replacing the hardcoded 0.85.

        Reference: https://huggingface.co/openai/clip-vit-base-patch32
        """
        x0, y0, x1, y1 = [max(0, int(v)) for v in bbox]
        crop = rgb[y0:y1, x0:x1]
        if crop.size == 0 or crop.shape[0] < 3 or crop.shape[1] < 3:
            return label, 0.0

        with torch.inference_mode():
            crop_pil = Image.fromarray(crop)
            inputs = self._clip_proc(
                images=crop_pil, return_tensors="pt",
            ).to("cuda")
            text_inputs = self._clip_proc(
                text=[label], return_tensors="pt", padding=True,
            ).to("cuda")

            image_features = self._clip_model.get_image_features(**inputs)
            text_features = self._clip_model.get_text_features(**text_inputs)

        # Cosine similarity
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        similarity = float((image_features @ text_features.T).item())

        return label, similarity

    # ══════ Stage 2 (ex-3): SAM2 box-prompt refinement ══════

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
        from transformers import AutoProcessor, CLIPModel, CLIPProcessor, Florence2ForConditionalGeneration

        self._sam2_image = SAM2ImagePredictor.from_pretrained(self._IMAGE_PREDICTOR_MODEL)
        self._florence2 = Florence2ForConditionalGeneration.from_pretrained(
            self._FLORENCE2_MODEL,
            torch_dtype=torch.bfloat16,
        ).to("cuda")
        self._florence2_proc = AutoProcessor.from_pretrained(self._FLORENCE2_MODEL)
        self._clip_model = CLIPModel.from_pretrained(self._CLIP_MODEL).to("cuda")
        self._clip_proc = CLIPProcessor.from_pretrained(self._CLIP_MODEL)
        logger.info(
            "TrackingEngine models loaded (SAM2-tiny + Florence-2-base + CLIP-ViT-B/32, %.1f GB VRAM)",
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
