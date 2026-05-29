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

    Stage 1 ― Florence-2 multi-task ensemble detects all objects → bboxes + labels.
    Stage 2 ― Find cursor-containing bbox (or multi-scale crop cascade for small objects).
    Stage 3 ― SigLIP 2 label verification → calibrated confidence score.
    Stage 4 ― SAM2 box + multi-point prompt → tight mask → clean bbox.

    References
    ----------
    - Florence-2 <OD> task:
      https://huggingface.co/florence-community/Florence-2-base
    - SAM2 box prompt:
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

    def detect(self, pixel_x: float, pixel_y: float, rgb_bgr: np.ndarray) -> TrackingResult:
        """Detect and describe the object at (pixel_x, pixel_y) in rgb_bgr.

        Pipeline:
        1. Florence-2 multi-task ensemble on full image → bbox at cursor.
        2. If full-image bbox scores poorly on SigLIP 2, or no bbox found,
           run multi-scale crop cascade (256→384→512) around cursor.
           Pick the bbox with the best SigLIP 2 score.
        3. SigLIP 2 final label verification; fall back to region description
           if the label still doesn't match.
        4. SAM2 box + multi-point refinement → tight mask → clean bbox.
        5. Fallback: SAM2 point-prompt if all detection tiers fail.
        """
        self._ensure_models()

        h, w = rgb_bgr.shape[:2]
        px = int(np.clip(pixel_x, 0, w - 1))
        py = int(np.clip(pixel_y, 0, h - 1))
        rgb_rgb = cv2.cvtColor(rgb_bgr, cv2.COLOR_BGR2RGB)

        # ══════ Stage 1: Florence-2 full-image detection ══════
        detections = self._run_detection(rgb_rgb, w, h)

        target_bbox: list[int] | None = None
        target_label = ""
        from_crop = False

        if detections:
            target_bbox, target_label, _ = self._find_containing_bbox(detections, px, py)

        # ══════ Stage 2: SigLIP 2 pre-check + crop cascade ══════
        # Full-image bbox found — but is it really the object at cursor?
        if target_bbox is not None:
            _, quick_score = self._verify_label_with_clip(
                rgb_rgb, target_bbox, target_label,
            )
            if quick_score < self._CLIP_SIM_THRESHOLD:
                # Bbox looks wrong (often: large object like "chair" containing
                # a small object like "cup").  Try crop cascade.
                logger.info(
                    "Full-image '%s' SigLIP2=%.3f < %.2f — trying crop cascade",
                    target_label, quick_score, self._CLIP_SIM_THRESHOLD,
                )
                crop_result = self._try_crop_detection(rgb_rgb, px, py, w, h)
                if crop_result is not None:
                    crop_bbox, crop_label = crop_result
                    _, crop_score = self._verify_label_with_clip(
                        rgb_rgb, crop_bbox, crop_label,
                    )
                    if crop_score > quick_score:
                        target_bbox, target_label = crop_bbox, crop_label
                        from_crop = True
                        logger.info(
                            "Crop cascade better: '%s' (%.3f > %.3f)",
                            crop_label, crop_score, quick_score,
                        )
                    # else: keep the original full-image bbox — even
                    # a low-score bbox is better than nothing.
        else:
            # No bbox at cursor in full image — crop cascade is mandatory
            logger.info(
                "No bbox at (%d,%d) in full image, trying crop cascade", px, py,
            )
            crop_result = self._try_crop_detection(rgb_rgb, px, py, w, h)
            if crop_result is not None:
                target_bbox, target_label = crop_result[0], crop_result[1]
                from_crop = True

        if target_bbox is None:
            return self._fallback_point_prompt(rgb_rgb, px, py)

        # ══════ SigLIP 2 label verification ══════
        verified_label, clip_score = self._verify_label_with_clip(
            rgb_rgb, target_bbox, target_label,
        )
        # Last resort: if the label still doesn't match, ask Florence-2
        # to describe the region directly.
        if clip_score < self._CLIP_SIM_THRESHOLD:
            logger.warning(
                "SigLIP2 verification low (%.3f) for '%s', trying region description",
                clip_score, verified_label,
            )
            alt_label = self._describe_region(rgb_rgb, *target_bbox)
            if alt_label != verified_label and alt_label != "object":
                _, alt_score = self._verify_label_with_clip(
                    rgb_rgb, target_bbox, alt_label,
                )
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
            "Detected: %s (SigLIP2=%.3f, bbox=%s)", label, clip_score, refined_bbox,
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

    # ══════ Small-object multi-scale crop re-detection ══════

    _CROP_SIZES = [256, 384, 512]
    # Ascending: start tight so small objects fill more of the frame.
    # Each crop tries <OD> first; if that returns nothing it retries with
    # <DENSE_REGION_CAPTION> on the same crop.  512 runs full ensemble.

    def _try_crop_detection(
        self, rgb: np.ndarray, px: int, py: int, img_w: int, img_h: int,
    ) -> tuple[list[int], str] | None:
        """Multi-scale crop cascade around the cursor point.

        Crop 256 → <OD> only  (very small objects: cups, cables, phones).
        Crop 384 → <OD> only  (medium objects partially visible at 256).
        Crop 512 → full ensemble (OD + DRC, last resort before fallback).

        Each tier returns on first hit — no wasted inference on larger
        crops once the object is found.
        """
        for size in self._CROP_SIZES:
            result = self._try_crop_at_size(rgb, px, py, img_w, img_h, size)
            if result is not None:
                return result
        return None

    def _try_crop_at_size(
        self, rgb: np.ndarray, px: int, py: int,
        img_w: int, img_h: int, size: int,
    ) -> tuple[list[int], str] | None:
        """Run detection on a single size×size crop and pick the best bbox.

        For crops < 512, starts with <OD> (fast).  If <OD> returns nothing,
        retries with <DENSE_REGION_CAPTION> on the same crop — DRC often
        catches non-COCO objects (boba cups, cables, gadgets) that OD misses.
        Size == 512 runs the full ensemble directly.
        """
        crop, cx0, cy0 = self._make_crop(rgb, px, py, img_w, img_h, size)
        if crop is None:
            return None

        crop_h, crop_w = crop.shape[:2]

        if size < 512:
            detections = self._run_single_task(
                crop, crop_w, crop_h, "<OD>", max_tokens=768,
            )
            task_name = "<OD>"
            if not detections:
                # <OD> missed — try DRC on same crop before giving up
                detections = self._run_single_task(
                    crop, crop_w, crop_h, "<DENSE_REGION_CAPTION>", max_tokens=512,
                )
                task_name = "<DRC>"
        else:
            detections = self._run_detection(crop, crop_w, crop_h)
            task_name = "ensemble"

        if not detections:
            logger.debug("Crop %d×%d %s: no detections", size, size, task_name)
            return None

        result = self._pick_bbox_from_crop(
            detections, px - cx0, py - cy0, cx0, cy0,
        )
        if result is not None:
            bbox, label = result
            logger.info(
                "Crop %d×%d %s: '%s' at %s",
                size, size, task_name, label, bbox,
            )
        return result

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

    def _pick_bbox_from_crop(
        self,
        detections: list[tuple[list[int], str, float]],
        crop_px: int, crop_py: int,
        crop_x0: int, crop_y0: int,
    ) -> tuple[list[int], str] | None:
        """Pick the best detection bbox for a cursor point in crop coords.

        Priority: smallest bbox that contains (crop_px, crop_py).
        Fallback: closest bbox by centre distance (cursor missed all bboxes
        but the object is likely the nearest one).
        Returns (bbox_in_full_coords, label) or None.
        """
        # Exact containment — smallest bbox containing the cursor
        best_bbox, best_label, _ = self._find_containing_bbox(
            detections, crop_px, crop_py,
        )
        if best_label and best_bbox is not None:
            return (
                [
                    crop_x0 + best_bbox[0], crop_y0 + best_bbox[1],
                    crop_x0 + best_bbox[2], crop_y0 + best_bbox[3],
                ],
                best_label,
            )

        # Cursor not inside any bbox — pick closest by centre distance
        if not detections:
            return None

        best_dist = float("inf")
        best_bbox_crop: list[int] = []
        best_label_crop = ""
        for bbox, label, _ in detections:
            cx = (bbox[0] + bbox[2]) / 2.0
            cy = (bbox[1] + bbox[3]) / 2.0
            dist = (cx - crop_px) ** 2 + (cy - crop_py) ** 2
            if dist < best_dist:
                best_dist = dist
                best_label_crop = label
                best_bbox_crop = bbox

        if not best_label_crop:
            return None

        return (
            [
                crop_x0 + best_bbox_crop[0], crop_y0 + best_bbox_crop[1],
                crop_x0 + best_bbox_crop[2], crop_y0 + best_bbox_crop[3],
            ],
            best_label_crop,
        )

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

    # ══════ Stage 3: SAM2 multi-point + box refinement ══════

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

    # ══════ Fallback: SAM2 point prompt ══════

    def _fallback_point_prompt(self, rgb: np.ndarray, px: int, py: int) -> TrackingResult:
        """Fallback when detection fails: use SAM2 point prompt directly.

        For label generation, crops the SAM2 mask region + padding and runs
        Florence-2 <DETAILED_CAPTION> on the isolated object image, avoiding
        background interference that causes mislabeling (e.g., 'a laptop'
        when the cursor is on a cup next to a laptop).
        """
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

        # Crop the mask region + padding, caption the isolated object
        label = self._caption_crop(rgb, *bbox)

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
                    return _clean_label(label)
            except Exception as exc:
                logger.warning("Florence-2 %s on crop failed: %s", task, exc)

        return "object"

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
