from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import torch


DEFAULT_SAM2_MODEL_ID = "facebook/sam2.1-hiera-small"


@dataclass(frozen=True)
class Sam2RuntimeConfig:
    model_id: str = DEFAULT_SAM2_MODEL_ID
    checkpoint: Path | None = None
    config: str | None = None
    device: str = "cuda"


@dataclass(frozen=True)
class Sam2PromptConfig:
    depth_abs_band_m: float = 0.18
    depth_rel_band: float = 0.12
    positive_window_px: int = 90
    negative_inner_radius_px: int = 36
    negative_outer_radius_px: int = 150
    max_extra_positive_points: int = 4
    max_negative_points: int = 8
    min_mask_area_ratio: float = 0.0005
    max_mask_area_ratio: float = 0.85
    use_box_prompt: bool = True
    component_area_weight: float = 0.35


@dataclass(frozen=True)
class Sam2SegmentResult:
    mask: np.ndarray
    score: float
    sam_score: float
    depth_consistency: float | None
    selected_index: int
    point_coords: np.ndarray
    point_labels: np.ndarray


def _seed_xy(prompt: dict) -> tuple[int, int]:
    if prompt.get("valid") and "rgb_x" in prompt and "rgb_y" in prompt:
        return int(prompt["rgb_x"]), int(prompt["rgb_y"])
    coords = prompt.get("point_coords") or []
    if not coords:
        raise ValueError("prompt has no point coordinates")
    return int(coords[0][0]), int(coords[0][1])


def _seed_depth(prompt: dict, depth: np.ndarray, x: int, y: int) -> float | None:
    for key in ("depth_sample_m", "rgb_camera_z_m"):
        value = prompt.get(key)
        if value is not None:
            value = float(value)
            if np.isfinite(value) and value > 0:
                return value
    h, w = depth.shape
    if 0 <= x < w and 0 <= y < h and np.isfinite(depth[y, x]) and depth[y, x] > 0:
        return float(depth[y, x])
    return None


def _sample_component_points(component: np.ndarray, seed_x: int, seed_y: int, max_points: int) -> list[tuple[int, int]]:
    ys, xs = np.where(component)
    if xs.size == 0 or max_points <= 0:
        return []
    candidates = []
    directions = np.array([[1, 0], [-1, 0], [0, 1], [0, -1]], dtype=np.float32)
    centered = np.stack([xs - seed_x, ys - seed_y], axis=1).astype(np.float32)
    for direction in directions:
        projection = centered @ direction
        idx = int(np.argmax(projection))
        if projection[idx] > 12:
            candidates.append((int(xs[idx]), int(ys[idx])))
    unique = []
    seen = {(seed_x, seed_y)}
    for point in candidates:
        if point not in seen:
            unique.append(point)
            seen.add(point)
        if len(unique) >= max_points:
            break
    return unique


def _component_at_seed(mask: np.ndarray, seed_x: int, seed_y: int) -> np.ndarray:
    h, w = mask.shape
    if not (0 <= seed_x < w and 0 <= seed_y < h):
        return np.zeros_like(mask, dtype=bool)
    num_labels, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    if num_labels <= 1:
        return mask.astype(bool)
    label = int(labels[seed_y, seed_x])
    if label <= 0:
        return np.zeros_like(mask, dtype=bool)
    return labels == label


def build_depth_guided_sam_points(
    depth: np.ndarray | None,
    prompt: dict,
    config: Sam2PromptConfig = Sam2PromptConfig(),
) -> tuple[np.ndarray, np.ndarray]:
    if "sam_point_coords" in prompt and "sam_point_labels" in prompt:
        points = np.asarray(prompt["sam_point_coords"], dtype=np.float32)
        labels = np.asarray(prompt["sam_point_labels"], dtype=np.int32)
        if points.ndim == 2 and points.shape[1] == 2 and labels.shape == (points.shape[0],):
            return points, labels

    seed_x, seed_y = _seed_xy(prompt)
    points: list[tuple[int, int]] = [(seed_x, seed_y)]
    labels: list[int] = [1]

    if depth is not None:
        h, w = depth.shape
        seed_depth = _seed_depth(prompt, depth, seed_x, seed_y)
        if seed_depth is not None and 0 <= seed_x < w and 0 <= seed_y < h:
            band = max(float(config.depth_abs_band_m), float(config.depth_rel_band) * seed_depth)
            valid = np.isfinite(depth) & (depth > 0)
            similar = valid & (np.abs(depth - np.float32(seed_depth)) <= np.float32(band))
            radius = max(8, int(config.positive_window_px))
            crop_mask = np.zeros_like(similar, dtype=bool)
            crop_mask[
                max(0, seed_y - radius) : min(h, seed_y + radius + 1),
                max(0, seed_x - radius) : min(w, seed_x + radius + 1),
            ] = True
            component = _component_at_seed(similar & crop_mask, seed_x, seed_y)
            for point in _sample_component_points(component, seed_x, seed_y, config.max_extra_positive_points):
                points.append(point)
                labels.append(1)

            angles = np.linspace(0.0, 2.0 * np.pi, max(12, config.max_negative_points * 2), endpoint=False)
            added_negatives = 0
            for radius in (config.negative_inner_radius_px, config.negative_outer_radius_px):
                for angle in angles:
                    if added_negatives >= config.max_negative_points:
                        break
                    x = int(round(seed_x + np.cos(angle) * radius))
                    y = int(round(seed_y + np.sin(angle) * radius))
                    if not (0 <= x < w and 0 <= y < h):
                        continue
                    d = float(depth[y, x]) if np.isfinite(depth[y, x]) else 0.0
                    if d <= 0 or abs(d - seed_depth) > band * 1.7:
                        points.append((x, y))
                        labels.append(0)
                        added_negatives += 1
                if added_negatives >= config.max_negative_points:
                    break

    return np.asarray(points, dtype=np.float32), np.asarray(labels, dtype=np.int32)


def build_depth_guided_sam_box(prompt: dict, config: Sam2PromptConfig = Sam2PromptConfig()) -> np.ndarray | None:
    if not config.use_box_prompt:
        return None
    box = prompt.get("sam_box_xyxy")
    if box is None:
        return None
    arr = np.asarray(box, dtype=np.float32)
    if arr.shape != (4,):
        return None
    if not np.all(np.isfinite(arr)) or arr[2] <= arr[0] or arr[3] <= arr[1]:
        return None
    return arr


def _mask_depth_consistency(mask: np.ndarray, depth: np.ndarray | None, prompt: dict) -> float | None:
    if depth is None or not np.any(mask):
        return None
    seed_x, seed_y = _seed_xy(prompt)
    seed_depth = _seed_depth(prompt, depth, seed_x, seed_y)
    if seed_depth is None:
        return None
    values = depth[mask & np.isfinite(depth) & (depth > 0)]
    if values.size == 0:
        return 0.0
    band = max(0.25, 0.18 * seed_depth)
    return float(np.mean(np.abs(values - seed_depth) <= band))


def _mask_component_support(mask: np.ndarray, prompt: dict) -> float | None:
    component_area = int(prompt.get("rgbd_component_area_px") or 0)
    if component_area <= 0:
        return None
    mask_area = int(np.count_nonzero(mask))
    if mask_area <= 0:
        return 0.0
    box = prompt.get("sam_box_xyxy")
    if box is not None:
        x0, y0, x1, y1 = [int(round(float(v))) for v in box]
        h, w = mask.shape
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w - 1, x1), min(h - 1, y1)
        if x1 >= x0 and y1 >= y0:
            in_box = int(np.count_nonzero(mask[y0 : y1 + 1, x0 : x1 + 1]))
            box_support = in_box / max(mask_area, 1)
        else:
            box_support = 0.0
    else:
        box_support = 1.0
    area_ratio = min(mask_area, component_area) / max(mask_area, component_area)
    return float(0.65 * area_ratio + 0.35 * box_support)


def _candidate_score(
    mask: np.ndarray,
    sam_score: float,
    depth: np.ndarray | None,
    prompt: dict,
    config: Sam2PromptConfig,
) -> tuple[float, float | None]:
    seed_x, seed_y = _seed_xy(prompt)
    h, w = mask.shape
    if not (0 <= seed_x < w and 0 <= seed_y < h) or not mask[seed_y, seed_x]:
        return -1e9, None
    area_ratio = float(np.count_nonzero(mask)) / float(h * w)
    if area_ratio < config.min_mask_area_ratio or area_ratio > config.max_mask_area_ratio:
        return -1e9, None
    consistency = _mask_depth_consistency(mask, depth, prompt)
    score = float(sam_score)
    if consistency is not None:
        score += 0.35 * consistency
    component_support = _mask_component_support(mask, prompt)
    if component_support is not None:
        score += float(config.component_area_weight) * component_support
    return score, consistency


class Sam2DeviceSegmenter:
    def __init__(
        self,
        runtime: Sam2RuntimeConfig = Sam2RuntimeConfig(),
        prompt_config: Sam2PromptConfig = Sam2PromptConfig(),
    ) -> None:
        self.runtime = runtime
        self.prompt_config = prompt_config
        self.predictor = None
        self._current_rgb_hash: int | None = None

    @property
    def ready(self) -> bool:
        return self.predictor is not None

    def load(self) -> None:
        if self.predictor is not None:
            return
        if self.runtime.checkpoint is not None:
            if self.runtime.config is None:
                raise ValueError("--sam2-config is required when --sam2-checkpoint is used")
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor

            model = build_sam2(
                self.runtime.config,
                str(self.runtime.checkpoint),
                device=self.runtime.device,
            )
            self.predictor = SAM2ImagePredictor(model)
            return

        from sam2.sam2_image_predictor import SAM2ImagePredictor

        self.predictor = SAM2ImagePredictor.from_pretrained(
            self.runtime.model_id,
            device=self.runtime.device,
        )

    def segment(self, rgb: np.ndarray, depth: np.ndarray | None, prompt: dict) -> Sam2SegmentResult:
        if not prompt.get("valid", False):
            raise ValueError(f"invalid prompt: {prompt.get('reason')}")
        self.load()
        assert self.predictor is not None

        points, labels = build_depth_guided_sam_points(depth, prompt, self.prompt_config)
        box = build_depth_guided_sam_box(prompt, self.prompt_config)
        use_cuda_amp = str(self.runtime.device).startswith("cuda") and torch.cuda.is_available()
        with torch.inference_mode():
            if use_cuda_amp:
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    self.predictor.set_image(rgb.astype(np.uint8))
                    masks, scores, _ = self.predictor.predict(
                        point_coords=points,
                        point_labels=labels,
                        box=box,
                        multimask_output=True,
                        return_logits=False,
                    )
            else:
                self.predictor.set_image(rgb.astype(np.uint8))
                masks, scores, _ = self.predictor.predict(
                    point_coords=points,
                    point_labels=labels,
                    box=box,
                    multimask_output=True,
                    return_logits=False,
                )
        masks = masks.astype(bool)
        scores = np.asarray(scores, dtype=np.float32)

        best_index = -1
        best_score = -1e9
        best_consistency = None
        for idx, mask in enumerate(masks):
            score, consistency = _candidate_score(mask, float(scores[idx]), depth, prompt, self.prompt_config)
            if score > best_score:
                best_score = score
                best_index = idx
                best_consistency = consistency
        if best_index < 0:
            raise RuntimeError("SAM2 produced no candidate mask containing the cursor point")

        return Sam2SegmentResult(
            mask=masks[best_index],
            score=float(best_score),
            sam_score=float(scores[best_index]),
            depth_consistency=best_consistency,
            selected_index=int(best_index),
            point_coords=points,
            point_labels=labels,
        )

    def reset_for_image(self, rgb: np.ndarray) -> None:
        """Encode a new RGB frame once so later point updates are cheap."""
        self.load()
        assert self.predictor is not None
        self.predictor.set_image(rgb.astype(np.uint8))
        self._current_rgb_hash = hash(rgb.tobytes())

    def re_predict(
        self,
        point_coords: np.ndarray,
        point_labels: np.ndarray,
        box: np.ndarray | None = None,
    ) -> np.ndarray | None:
        assert self.predictor is not None
        use_cuda_amp = str(self.runtime.device).startswith("cuda") and torch.cuda.is_available()
        with torch.inference_mode():
            if use_cuda_amp:
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    masks, scores, _ = self.predictor.predict(
                        point_coords=point_coords,
                        point_labels=point_labels,
                        box=box,
                        multimask_output=True,
                        return_logits=False,
                    )
            else:
                masks, scores, _ = self.predictor.predict(
                    point_coords=point_coords,
                    point_labels=point_labels,
                    box=box,
                    multimask_output=True,
                    return_logits=False,
                )
        masks = masks.astype(bool)
        scores = np.asarray(scores, dtype=np.float32)
        if masks.size == 0:
            return None
        return masks[int(np.argmax(scores))]


def mask_overlay(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = rgb.astype(np.uint8).copy()
    out[mask] = (out[mask].astype(np.float32) * 0.50 + np.array([0, 255, 120], dtype=np.float32) * 0.50).astype(np.uint8)
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out, contours, -1, (255, 255, 255), 2)
    return out


def _read_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prompted SAM2 device segmentation")
    parser.add_argument("--rgb", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--out-mask", type=Path, required=True)
    parser.add_argument("--depth", type=Path, default=None)
    parser.add_argument("--overlay-out", type=Path, default=None)
    parser.add_argument("--meta-out", type=Path, default=None)
    parser.add_argument("--sam2-model-id", default=DEFAULT_SAM2_MODEL_ID)
    parser.add_argument("--sam2-checkpoint", type=Path, default=None)
    parser.add_argument("--sam2-config", default=None)
    parser.add_argument("--sam2-device", default="cuda")
    parser.add_argument("--depth-abs-band-m", type=float, default=0.18)
    parser.add_argument("--depth-rel-band", type=float, default=0.12)
    args = parser.parse_args()

    rgb = _read_rgb(args.rgb)
    depth = np.load(args.depth).astype(np.float32) if args.depth is not None else None
    prompt = json.loads(args.prompt.read_text(encoding="utf-8"))
    segmenter = Sam2DeviceSegmenter(
        Sam2RuntimeConfig(
            model_id=args.sam2_model_id,
            checkpoint=args.sam2_checkpoint,
            config=args.sam2_config,
            device=args.sam2_device,
        ),
        Sam2PromptConfig(
            depth_abs_band_m=args.depth_abs_band_m,
            depth_rel_band=args.depth_rel_band,
        ),
    )
    result = segmenter.segment(rgb, depth, prompt)
    args.out_mask.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.out_mask), result.mask.astype(np.uint8) * 255)
    if args.overlay_out is not None:
        args.overlay_out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.overlay_out), cv2.cvtColor(mask_overlay(rgb, result.mask), cv2.COLOR_RGB2BGR))
    info = {
        "score": result.score,
        "sam_score": result.sam_score,
        "depth_consistency": result.depth_consistency,
        "selected_index": result.selected_index,
        "point_coords": result.point_coords.astype(float).tolist(),
        "point_labels": result.point_labels.astype(int).tolist(),
    }
    if args.meta_out is not None:
        args.meta_out.parent.mkdir(parents=True, exist_ok=True)
        args.meta_out.write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps(info), flush=True)


if __name__ == "__main__":
    main()
