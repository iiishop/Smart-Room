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
#
# SigLIP 2 label verification:
#   https://huggingface.co/docs/transformers/model_doc/siglip2
#   https://huggingface.co/google/siglip2-base-patch16-224
#   model.get_image_features() / get_text_features() → cosine similarity
# ═══════════════════════════════════════════════════════════════════════

_LOC_RE = re.compile(r"</?loc_\d+>", re.IGNORECASE)
_LOC_PATTERN = re.compile(r"<loc_\d+>", re.IGNORECASE)


class TrackingEngine:
    """Object detection + segmentation + label verification engine.

    Stage 0 ― SAM2 point-prompt at cursor → mask (guaranteed spatial accuracy).
    Stage 1 ― Florence-2 multi-task ensemble detects all objects → bboxes + labels.
    Stage 2 ― IoU-match cursor mask against all detection bboxes → pick best.
    Stage 3 ― SigLIP 2 label verification → calibrated confidence score.
    Stage 4 ― SAM2 box + multi-point refinement → tight mask → clean bbox.

    If no bbox matches the cursor mask (IoU < 0.3), the cursor object was
    missed by detection — fall back to captioning the mask region directly.

    References
    ----------
    - Florence-2 <OD> task:
      https://huggingface.co/florence-community/Florence-2-base
    - SAM2 point + box prompt:
      https://huggingface.co/docs/transformers/model_doc/sam2
    - SigLIP 2:
      https://huggingface.co/google/siglip2-base-patch16-224
    """

    _IMAGE_PREDICTOR_MODEL = "facebook/sam2.1-hiera-tiny"
    _FLORENCE2_MODEL = "florence-community/Florence-2-base"
    _SIGLIP_MODEL = "google/siglip2-base-patch16-224"
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

    _CROP_MASK_IOU_THRESHOLD = 0.3  # min IoU for bbox→mask match

    def detect(self, pixel_x: float, pixel_y: float, rgb_bgr: np.ndarray) -> TrackingResult:
        """Detect and describe the object at (pixel_x, pixel_y) in rgb_bgr.

        Pipeline:
        0. SAM2 point-prompt at cursor → mask (ground truth for "what's here").
        1. Florence-2 multi-task ensemble on full image → detection bboxes.
        2. IoU-match cursor mask against all bboxes → select the bbox that
           actually overlaps the object at cursor.
        3. If no good match: multi-scale crop cascade → IoU match again.
        4. SigLIP 2 label verification; fall back to region description
           if the label still doesn't match.
        5. If a bbox matched: SAM2 box + multi-point refinement → tight bbox.
        6. If no bbox matched: caption the cursor mask region directly.
        """
        self._ensure_models()

        h, w = rgb_bgr.shape[:2]
        px = int(np.clip(pixel_x, 0, w - 1))
        py = int(np.clip(pixel_y, 0, h - 1))
        rgb_rgb = cv2.cvtColor(rgb_bgr, cv2.COLOR_BGR2RGB)

        # ══════ Stage 0: SAM2 point prompt → cursor mask ══════
        # This mask is the ground truth for "what's at the cursor" —
        # SAM2 guarantees the mask covers the prompt point.
        cursor_mask = self._segment_at_point(rgb_rgb, px, py)
        if cursor_mask is None or not cursor_mask.any():
            logger.warning("SAM2 point prompt produced empty mask")
            self._state = TrackState.IDLE
            return TrackingResult(object_id=0, state=TrackState.IDLE,
                                  label="", score=0.0, box_xyxy=(0, 0, 0, 0))

        # ══════ Stage 1: Florence-2 full-image detection ══════
        detections_full = self._run_detection(rgb_rgb, w, h)

        # ══════ Stage 2: IoU-match cursor mask against full-image bboxes ══════
        best_bbox, best_label, best_iou = self._match_bboxes_by_mask_iou(
            detections_full, cursor_mask,
        )
        logger.info(
            "Full-image IoU best: '%s' (%.3f)", best_label or "(none)", best_iou,
        )

        # ══════ Stage 3: Crop cascade if full-image didn't find the cursor object ══════
        if best_iou < self._CROP_MASK_IOU_THRESHOLD:
            logger.info("Full-image IoU %.3f < %.2f — trying crop cascade",
                        best_iou, self._CROP_MASK_IOU_THRESHOLD)
            crop_dets = self._try_crop_detection_all(rgb_rgb, px, py, w, h)
            if crop_dets:
                c_bbox, c_label, c_iou = self._match_bboxes_by_mask_iou(
                    crop_dets, cursor_mask,
                )
                logger.info(
                    "Crop cascade IoU best: '%s' (%.3f)", c_label or "(none)", c_iou,
                )
                if c_iou > best_iou:
                    best_bbox, best_label, best_iou = c_bbox, c_label, c_iou

        # ══════ Stage 4 & 5: Verify + refine, or caption mask ══════
        if best_iou >= self._CROP_MASK_IOU_THRESHOLD and best_bbox is not None:
            # ── Bbox matched the cursor mask: verify label + refine ──
            verified_label, clip_score = self._verify_label_with_clip(
                rgb_rgb, best_bbox, best_label,
            )
            if clip_score < self._CLIP_SIM_THRESHOLD:
                logger.warning(
                    "SigLIP2 verification low (%.3f) for '%s', trying caption→phrase grounding",
                    clip_score, verified_label,
                )
                # Grounded-SAM-2 style cascade: caption the crop → extract
                # noun phrases with bboxes → verify each against SigLIP 2.
                phrases = self._caption_to_phrases(rgb_rgb, *best_bbox)
                for phrase, phrase_bbox in phrases:
                    if phrase == verified_label or phrase == "object":
                        continue
                    _, phrase_score = self._verify_label_with_clip(
                        rgb_rgb, phrase_bbox, phrase,
                    )
                    if phrase_score > clip_score:
                        verified_label = phrase
                        clip_score = phrase_score
                        logger.info(
                            "Caption→phrase: '%s' (%.3f) beats original",
                            phrase, phrase_score,
                        )
                # If phrase grounding didn't help, caption the isolated crop
                if clip_score < self._CLIP_SIM_THRESHOLD:
                    alt_label = self._caption_crop(rgb_rgb, *best_bbox)
                    if alt_label != verified_label and alt_label != "object":
                        _, alt_score = self._verify_label_with_clip(
                            rgb_rgb, best_bbox, alt_label,
                        )
                        if alt_score > clip_score:
                            verified_label = alt_label
                            clip_score = alt_score

            # SAM2 box refine — image already set from Stage 0
            refined_bbox = self._refine_with_sam2_box_preserve(rgb_rgb, best_bbox)

            label = _clean_label(verified_label)
            self._label = label
            self._score = clip_score
            self._box_xyxy = refined_bbox
            self._state = TrackState.TRACKING

            logger.info(
                "Detected: %s (SigLIP2=%.3f, bbox=%s)", label, clip_score, refined_bbox,
            )

            return TrackingResult(
                object_id=1, state=TrackState.TRACKING,
                label=label, score=clip_score, box_xyxy=refined_bbox,
                center_pixel=(
                    (refined_bbox[0] + refined_bbox[2]) / 2.0,
                    (refined_bbox[1] + refined_bbox[3]) / 2.0,
                ),
            )
        else:
            # ── No bbox matched the cursor mask — caption the mask region ──
            mask_bbox = self._bbox_from_mask(cursor_mask)
            label = self._caption_crop(rgb_rgb, *mask_bbox)
            logger.info("No matching bbox — captioning mask: '%s'", label)

            self._label = label
            self._score = 0.5
            self._box_xyxy = mask_bbox
            self._state = TrackState.TRACKING

            return TrackingResult(
                object_id=1, state=TrackState.TRACKING,
                label=label, score=0.5, box_xyxy=mask_bbox,
                center_pixel=(
                    (mask_bbox[0] + mask_bbox[2]) / 2.0,
                    (mask_bbox[1] + mask_bbox[3]) / 2.0,
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
            return [(bbox, _shorten_label(label), 0.65) for bbox, label, _ in drc]

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
                # Replace OD label with shorter DRC description
                merged[best_oi] = (merged[best_oi][0], _shorten_label(dr_label), 0.85)
                drc_matched[di] = True

        # Add unmatched DRC detections (objects <OD> missed)
        for di, (dr_bbox, dr_label, _) in enumerate(drc):
            if not drc_matched[di]:
                merged.append((dr_bbox, _shorten_label(dr_label), 0.65))

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

    # ══════ Small-object multi-scale crop re-detection ══════

    _CROP_SIZES = [256, 384, 512]

    def _try_crop_detection_all(
        self, rgb: np.ndarray, px: int, py: int, img_w: int, img_h: int,
    ) -> list[tuple[list[int], str, float]]:
        """Run multi-scale crop cascade, return ALL detections from all crops.

        Each crop produces a list of (bbox_in_full_coords, label, score).
        The caller (detect) uses mask-IoU matching to pick the right bbox.
        """
        all_dets: list[tuple[list[int], str, float]] = []
        for size in self._CROP_SIZES:
            crop_dets = self._try_crop_at_size(rgb, px, py, img_w, img_h, size)
            all_dets.extend(crop_dets)
            if crop_dets:
                # If this crop found something, stop — avoid redundant
                # larger crops once the object is already in the pool.
                break
        return all_dets

    def _try_crop_at_size(
        self, rgb: np.ndarray, px: int, py: int,
        img_w: int, img_h: int, size: int,
    ) -> list[tuple[list[int], str, float]]:
        """Run detection on a single size×size crop.

        Returns all detections remapped to full-image coordinates.
        Returns empty list if the crop is too small or nothing detected.
        """
        crop, cx0, cy0 = self._make_crop(rgb, px, py, img_w, img_h, size)
        if crop is None:
            return []

        crop_h, crop_w = crop.shape[:2]

        if size < 512:
            detections = self._run_single_task(
                crop, crop_w, crop_h, "<OD>", max_tokens=768,
            )
            task_name = "<OD>"
            if not detections:
                detections = self._run_single_task(
                    crop, crop_w, crop_h, "<DENSE_REGION_CAPTION>", max_tokens=512,
                )
                task_name = "<DRC>"
        else:
            detections = self._run_detection(crop, crop_w, crop_h)
            task_name = "ensemble"

        if not detections:
            logger.debug("Crop %d×%d %s: no detections", size, size, task_name)
            return []

        # Remap all detections to full-image coordinates
        remapped = [
            ([cx0 + bbox[0], cy0 + bbox[1], cx0 + bbox[2], cy0 + bbox[3]], label, score)
            for bbox, label, score in detections
        ]
        logger.info(
            "Crop %d×%d %s: %d detections", size, size, task_name, len(remapped),
        )
        return remapped

    @staticmethod
    def _make_crop(
        rgb: np.ndarray, px: int, py: int,
        img_w: int, img_h: int, size: int,
    ) -> tuple[np.ndarray | None, int, int]:
        """Extract a size×size window centred on (px, py).

        Returns (crop_array, x0, y0) or (None, 0, 0) if the resulting
        region is too small (< 64 px in either dimension).
        """
        half = size // 2
        x0 = max(0, px - half)
        y0 = max(0, py - half)
        x1 = min(img_w, x0 + size)
        y1 = min(img_h, y0 + size)
        # Re-anchor to keep the crop exactly `size` when possible
        if x1 - x0 < size and x0 > 0:
            x0 = max(0, x1 - size)
        if y1 - y0 < size and y0 > 0:
            y0 = max(0, y1 - size)

        crop = rgb[y0:y1, x0:x1]
        if crop.shape[0] < 64 or crop.shape[1] < 64:
            return None, 0, 0
        return crop, x0, y0

    # ══════ Mask ←→ bbox IoU matching ══════

    @staticmethod
    def _compute_mask_bbox_iou(mask: np.ndarray, bbox: list[int]) -> float:
        """IoU between a binary mask and an axis-aligned bbox [x0, y0, x1, y1].

        The bbox is treated as a solid rectangular mask.  IoU = intersection
        area / union area.  Returns 0.0 if either region is empty.
        """
        x0, y0, x1, y1 = bbox
        h, w = mask.shape
        # Clip bbox to image bounds
        x0_c = max(0, x0)
        y0_c = max(0, y0)
        x1_c = min(w, x1)
        y1_c = min(h, y1)
        if x1_c <= x0_c or y1_c <= y0_c:
            return 0.0

        bbox_area = (x1_c - x0_c) * (y1_c - y0_c)
        mask_area = int(mask.sum())
        if bbox_area == 0 or mask_area == 0:
            return 0.0

        # Intersection: mask pixels inside the bbox
        inter = int(mask[y0_c:y1_c, x0_c:x1_c].sum())
        union = mask_area + bbox_area - inter
        return inter / max(union, 1)

    def _match_bboxes_by_mask_iou(
        self,
        detections: list[tuple[list[int], str, float]],
        mask: np.ndarray,
    ) -> tuple[list[int] | None, str, float]:
        """Return the (bbox, label, iou) with the highest IoU against mask.

        Returns (None, "", 0.0) if detections is empty.
        """
        best_bbox: list[int] | None = None
        best_label = ""
        best_iou = 0.0
        for bbox, label, _ in detections:
            iou = self._compute_mask_bbox_iou(mask, bbox)
            if iou > best_iou:
                best_iou = iou
                best_bbox = bbox
                best_label = label
        return best_bbox, best_label, best_iou

    @staticmethod
    def _bbox_from_mask(mask: np.ndarray) -> tuple[int, int, int, int]:
        """Tight axis-aligned bbox from a binary mask."""
        ys, xs = np.where(mask)
        return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))

    # ══════ SigLIP 2 label verification ══════

    def _verify_label_with_clip(
        self, rgb: np.ndarray, bbox: list[int], label: str,
    ) -> tuple[str, float]:
        """Verify Florence-2 label by matching cropped region against label text.

        Uses SigLIP 2 (google/siglip2-base-patch16-224) for zero-shot
        image–text alignment, replacing the original CLIP-ViT-B/32.
        Returns (label, cosine_similarity). The similarity score becomes
        the calibrated confidence, replacing the hardcoded 0.85.

        Reference: https://huggingface.co/google/siglip2-base-patch16-224
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
                text=[f"This is a photo of {label}."], return_tensors="pt",
            ).to("cuda")

            image_features = self._clip_model.get_image_features(**inputs).pooler_output
            text_features = self._clip_model.get_text_features(**text_inputs).pooler_output

        # Cosine similarity
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        similarity = float((image_features @ text_features.T).item())

        return label, similarity

    # ══════ Stage 0: SAM2 point prompt (cursor → mask) ══════

    def _segment_at_point(
        self, rgb: np.ndarray, px: int, py: int,
    ) -> np.ndarray | None:
        """Run SAM2 point prompt at (px, py) → binary mask.

        Sets the image on the SAM2 predictor so subsequent refine calls
        can reuse the embeddings without re-encoding.
        Returns None if SAM2 fails.
        """
        point = np.array([[px, py]], dtype=np.float32)
        try:
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                self._sam2_image.set_image(rgb)
                masks, _, _ = self._sam2_image.predict(
                    point_coords=point,
                    point_labels=np.array([1]),
                    multimask_output=False,
                )
            return np.asarray(masks[0], dtype=bool)
        except Exception as exc:
            logger.warning("SAM2 point prompt failed: %s", exc)
            return None

    # ══════ Stage 4: SAM2 multi-point + box refinement ══════

    _SAM2_GRID_SIZE = 4  # N×N grid of positive points inside the detection bbox

    def _refine_with_sam2_box(
        self, rgb: np.ndarray, rough_bbox: list[int]
    ) -> tuple[int, int, int, int]:
        """Refine a detection bbox using SAM2 box + multi-point prompt.

        Box provides the coarse spatial constraint; positive grid points inside the
        bbox tell SAM2 which pixels belong to the object; negative points outside
        each edge explicitly reject background. Multi-point prompts are officially
        supported by SAM2 and improve edge precision over single-box prompts.

        Reference: https://huggingface.co/docs/transformers/model_doc/sam2
        """
        x0, y0, x1, y1 = rough_bbox
        input_box = np.array([[x0, y0, x1, y1]], dtype=np.float32)

        # Positive points: grid sampled from bbox interior
        pos_points = self._sample_grid_points(x0, y0, x1, y1, grid=self._SAM2_GRID_SIZE)
        pos_labels = np.ones(len(pos_points), dtype=np.int32)

        # Negative points: just outside each edge
        neg_points = self._sample_boundary_points(x0, y0, x1, y1)
        neg_labels = np.zeros(len(neg_points), dtype=np.int32) if len(neg_points) > 0 else np.array([], dtype=np.int32)

        all_points = np.concatenate([pos_points, neg_points], axis=0)
        all_labels = np.concatenate([pos_labels, neg_labels], axis=0)

        try:
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                self._sam2_image.set_image(rgb)
                masks, scores, _ = self._sam2_image.predict(
                    point_coords=all_points,
                    point_labels=all_labels,
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
            # Fallback: box-only if multi-point fails
            logger.warning("SAM2 multi-point failed, trying box-only: %s", exc)
            try:
                with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                    self._sam2_image.set_image(rgb)
                    masks, scores, _ = self._sam2_image.predict(
                        box=input_box, multimask_output=False,
                    )
                mask = np.asarray(masks[0], dtype=bool)
                if mask.any():
                    bbox_y, bbox_x = np.where(mask)
                    return (
                        int(bbox_x.min()), int(bbox_y.min()),
                        int(bbox_x.max()), int(bbox_y.max()),
                    )
            except Exception as exc2:
                logger.warning("SAM2 box-only fallback also failed: %s", exc2)

        return (x0, y0, x1, y1)

    def _refine_with_sam2_box_preserve(
        self, rgb: np.ndarray, rough_bbox: list[int],
    ) -> tuple[int, int, int, int]:
        """Like _refine_with_sam2_box, but assumes image is already set on predictor.

        Call after _segment_at_point() to avoid re-encoding the image.
        """
        x0, y0, x1, y1 = rough_bbox
        input_box = np.array([[x0, y0, x1, y1]], dtype=np.float32)

        pos_points = self._sample_grid_points(x0, y0, x1, y1, grid=self._SAM2_GRID_SIZE)
        pos_labels = np.ones(len(pos_points), dtype=np.int32)
        neg_points = self._sample_boundary_points(x0, y0, x1, y1)
        neg_labels = np.zeros(len(neg_points), dtype=np.int32) if len(neg_points) > 0 else np.array([], dtype=np.int32)
        all_points = np.concatenate([pos_points, neg_points], axis=0)
        all_labels = np.concatenate([pos_labels, neg_labels], axis=0)

        try:
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                masks, scores, _ = self._sam2_image.predict(
                    point_coords=all_points,
                    point_labels=all_labels,
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
            logger.warning("SAM2 multi-point (preserve) failed, trying box-only: %s", exc)
            try:
                with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                    masks, scores, _ = self._sam2_image.predict(
                        box=input_box, multimask_output=False,
                    )
                mask = np.asarray(masks[0], dtype=bool)
                if mask.any():
                    bbox_y, bbox_x = np.where(mask)
                    return (
                        int(bbox_x.min()), int(bbox_y.min()),
                        int(bbox_x.max()), int(bbox_y.max()),
                    )
            except Exception as exc2:
                logger.warning("SAM2 box-only (preserve) also failed: %s", exc2)

        return (x0, y0, x1, y1)

    @staticmethod
    def _sample_grid_points(
        x0: int, y0: int, x1: int, y1: int, grid: int = 4,
    ) -> np.ndarray:
        """Sample center points of an N×N grid within the bbox.

        For very small bboxes (< grid*2 px per dimension), returns just the
        bbox center point rather than trying to fit a full grid.
        """
        bw, bh = x1 - x0, y1 - y0
        if bw < grid * 2 or bh < grid * 2:
            return np.array([[x0 + bw / 2.0, y0 + bh / 2.0]], dtype=np.float32)

        cell_w = bw / grid
        cell_h = bh / grid
        points = []
        for i in range(grid):
            for j in range(grid):
                points.append([
                    x0 + (i + 0.5) * cell_w,
                    y0 + (j + 0.5) * cell_h,
                ])
        return np.array(points, dtype=np.float32)

    @staticmethod
    def _sample_boundary_points(
        x0: int, y0: int, x1: int, y1: int,
    ) -> np.ndarray:
        """Sample one negative point outside each edge of the bbox.

        Margin is 10% of the bbox dimension or 5px, whichever is larger.
        This tells SAM2 explicitly 'not this region'.
        """
        margin_x = max(5, int((x1 - x0) * 0.1))
        margin_y = max(5, int((y1 - y0) * 0.1))
        return np.array([
            [x0 - margin_x, (y0 + y1) / 2.0],  # left
            [x1 + margin_x, (y0 + y1) / 2.0],  # right
            [(x0 + x1) / 2.0, y0 - margin_y],  # top
            [(x0 + x1) / 2.0, y1 + margin_y],  # bottom
        ], dtype=np.float32)

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
            return _shorten_label(_clean_label(result))
        except Exception as exc:
            logger.warning("Florence-2 region description failed: %s", exc)
            return "object"

    def _caption_crop(
        self, rgb: np.ndarray, x0: int, y0: int, x1: int, y1: int, pad_ratio: float = 0.15,
    ) -> str:
        """Crop and pad a bbox region, then run Florence-2 caption on the isolated object.

        Unlike _describe_region which passes the full image with location tokens,
        this method crops the object out, pads a border, and runs a clean caption
        with no background interference. Much better for small objects surrounded
        by dominant context (e.g., a cup on a laptop desk).

        Tries <DETAILED_CAPTION> first (most descriptive), falls back to <CAPTION>.
        """
        h, w = rgb.shape[:2]
        bw, bh = max(4, x1 - x0), max(4, y1 - y0)
        pad_x = max(8, int(bw * pad_ratio))
        pad_y = max(8, int(bh * pad_ratio))
        cx0 = max(0, x0 - pad_x)
        cy0 = max(0, y0 - pad_y)
        cx1 = min(w, x1 + pad_x)
        cy1 = min(h, y1 + pad_y)

        crop = rgb[cy0:cy1, cx0:cx1]
        if crop.size == 0:
            return "object"

        for task in ("<DETAILED_CAPTION>", "<CAPTION>"):
            try:
                raw = self._florence2_infer(crop, task, max_tokens=64)
                parsed = self._florence2_proc.post_process_generation(
                    raw, task=task, image_size=(crop.shape[1], crop.shape[0]),
                )
                label = parsed.get(task, "").strip()
                if label and len(label) > 2:
                    return _shorten_label(_clean_label(label))
            except Exception as exc:
                logger.warning("Florence-2 %s on crop failed: %s", task, exc)

        return "object"

    def _caption_to_phrases(
        self, rgb: np.ndarray, x0: int, y0: int, x1: int, y1: int,
    ) -> list[tuple[str, list[int]]]:
        """Grounded-SAM-2 style cascade: caption crop → extract noun phrases.

        1. Crop + pad the bbox region, run <DETAILED_CAPTION>.
        2. Feed caption to <CAPTION_TO_PHRASE_GROUNDING> → bboxes + labels.
        3. Remap phrase bboxes to full-image coords.
        Returns list of (phrase_text, bbox_in_full_coords), or empty list.
        """
        h, w = rgb.shape[:2]
        bw, bh = max(4, x1 - x0), max(4, y1 - y0)
        pad_x = max(8, int(bw * 0.15))
        pad_y = max(8, int(bh * 0.15))
        cx0 = max(0, x0 - pad_x)
        cy0 = max(0, y0 - pad_y)
        cx1 = min(w, x1 + pad_x)
        cy1 = min(h, y1 + pad_y)
        crop = rgb[cy0:cy1, cx0:cx1]
        if crop.size == 0:
            return []

        # Step 1: caption the isolated object region
        caption = ""
        for task in ("<DETAILED_CAPTION>", "<CAPTION>"):
            try:
                raw = self._florence2_infer(crop, task, max_tokens=128)
                parsed = self._florence2_proc.post_process_generation(
                    raw, task=task, image_size=(crop.shape[1], crop.shape[0]),
                )
                caption = parsed.get(task, "").strip()
                if caption and len(caption) > 3:
                    break
            except Exception as exc:
                logger.warning("Florence-2 %s for phrases failed: %s", task, exc)
        if not caption:
            return []

        # Step 2: <CAPTION_TO_PHRASE_GROUNDING> on the CROP (not full image).
        # Running on the full image would ground phrases against all objects
        # in the scene, not just the isolated crop — easily picks a neighbor.
        try:
            task = "<CAPTION_TO_PHRASE_GROUNDING>"
            text_input = f"{task}{caption}"
            raw = self._florence2_infer(crop, text_input, max_tokens=256)
            parsed = self._florence2_proc.post_process_generation(
                raw, task=task, image_size=(crop.shape[1], crop.shape[0]),
            )
            regions = parsed.get(task, {})
            bboxes = regions.get("bboxes", [])
            labels = regions.get("labels", [])
        except Exception as exc:
            logger.warning("Florence-2 %s failed: %s", task, exc)
            return []

        # Step 3: remap phrase bboxes from crop→full coords
        phrases: list[tuple[str, list[int]]] = []
        for box, label in zip(bboxes, labels):
            phrase = _clean_label(str(label))
            if not phrase or phrase == "object":
                continue
            px0, py0, px1, py1 = [int(v) for v in box]
            phrases.append((phrase, [cx0 + px0, cy0 + py0, cx0 + px1, cy0 + py1]))
        return phrases

    # ══════ Model loading ══════

    def _ensure_models(self) -> None:
        if self._sam2_image is not None:
            return
        from sam2.sam2_image_predictor import SAM2ImagePredictor
        from transformers import AutoModel, AutoProcessor, Florence2ForConditionalGeneration

        self._sam2_image = SAM2ImagePredictor.from_pretrained(
            self._IMAGE_PREDICTOR_MODEL, device="cuda",
        )
        self._florence2 = Florence2ForConditionalGeneration.from_pretrained(
            self._FLORENCE2_MODEL,
            torch_dtype=torch.bfloat16,
        ).to("cuda")
        self._florence2_proc = AutoProcessor.from_pretrained(self._FLORENCE2_MODEL)
        self._clip_model = AutoModel.from_pretrained(
            self._SIGLIP_MODEL,
            torch_dtype=torch.bfloat16,
            attn_implementation="sdpa",
        ).to("cuda")
        self._clip_proc = AutoProcessor.from_pretrained(self._SIGLIP_MODEL)
        logger.info(
            "TrackingEngine models loaded (SAM2-tiny + Florence-2-base + SigLIP2-B/16, %.1f GB VRAM)",
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


def _shorten_label(text: str, max_words: int = 5) -> str:
    """Trim Florence-2 caption boilerplate, keep only the key object phrase.

    Strips common preamble patterns like 'The image shows', 'This is a photo of',
    'In this picture there is', etc.  If the result is still longer than
    max_words, truncate to the first max_words.
    """
    text = text.strip()
    # Common Florence-2 caption preambles
    for prefix in [
        "The image shows ", "The image depicts ", "The picture shows ",
        "This is a photo of ", "This is an image of ", "This image shows ",
        "In this image, ", "In this picture, ", "In the image, ",
        "A photo of ", "An image of ",
    ]:
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):]
            break
    # Also strip trailing period
    text = text.rstrip(".")
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])
    return text.strip() if text.strip() else "object"
