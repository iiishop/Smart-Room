from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class RgbdMaskRefineConfig:
    max_global_depth_span_m: float = 1.20
    min_depth_keep_ratio: float = 0.35
    depth_component_union: bool = False
    depth_component_dilate_px: int = 2
    open_px: int = 2
    close_px: int = 5
    min_area_px: int = 200


@dataclass(frozen=True)
class RgbdMaskRefineResult:
    mask: np.ndarray
    bbox_xyxy: tuple[int, int, int, int] | None
    area_px: int
    depth_median_m: float | None
    depth_min_m: float | None
    depth_max_m: float | None


def _kernel(radius_px: int) -> np.ndarray:
    radius_px = max(0, int(radius_px))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius_px * 2 + 1, radius_px * 2 + 1))


def _seed_xy(prompt: dict) -> tuple[int, int] | None:
    if prompt.get("valid") and "rgb_x" in prompt and "rgb_y" in prompt:
        return int(prompt["rgb_x"]), int(prompt["rgb_y"])
    coords = prompt.get("point_coords") or []
    if coords:
        return int(coords[0][0]), int(coords[0][1])
    return None


def _normalized_point_pairs(prompt: dict) -> tuple[list[tuple[int, int]], list[int]]:
    coords = prompt.get("user_point_coords") or prompt.get("point_coords") or []
    labels = prompt.get("user_point_labels") or prompt.get("point_labels") or []
    if not isinstance(coords, list) or not isinstance(labels, list) or len(coords) != len(labels):
        return [], []

    out_coords: list[tuple[int, int]] = []
    out_labels: list[int] = []
    seen: set[tuple[int, int, int]] = set()
    for coord, label in zip(coords, labels):
        if not isinstance(coord, (list, tuple)) or len(coord) != 2:
            continue
        try:
            x = int(round(float(coord[0])))
            y = int(round(float(coord[1])))
            lbl = 1 if int(label) > 0 else 0
        except (TypeError, ValueError):
            continue
        key = (x, y, lbl)
        if key in seen:
            continue
        seen.add(key)
        out_coords.append((x, y))
        out_labels.append(lbl)
    return out_coords, out_labels


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


def _component_containing_or_nearest(mask: np.ndarray, x: int, y: int) -> np.ndarray:
    mask_u8 = mask.astype(np.uint8)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask_u8, connectivity=8)
    if num_labels <= 1:
        return mask.astype(bool)
    h, w = mask.shape
    if 0 <= x < w and 0 <= y < h:
        label = int(labels[y, x])
        if label > 0:
            return labels == label

    best_label = 0
    best_dist = float("inf")
    for label in range(1, num_labels):
        if int(stats[label, cv2.CC_STAT_AREA]) <= 0:
            continue
        cx, cy = centroids[label]
        dist = (float(cx) - x) ** 2 + (float(cy) - y) ** 2
        if dist < best_dist:
            best_dist = dist
            best_label = label
    return labels == best_label if best_label > 0 else mask.astype(bool)


def _components_for_prompt_points(mask: np.ndarray, prompt: dict) -> np.ndarray:
    coords, point_labels = _normalized_point_pairs(prompt)
    positive_points = [coord for coord, label in zip(coords, point_labels) if label > 0]
    if not positive_points:
        seed = _seed_xy(prompt)
        if seed is None:
            return mask.astype(bool)
        return _component_containing_or_nearest(mask, seed[0], seed[1])

    mask_u8 = mask.astype(np.uint8)
    num_labels, labels_img = cv2.connectedComponents(mask_u8, connectivity=8)
    if num_labels <= 1:
        return mask.astype(bool)

    h, w = mask.shape
    keep_labels: set[int] = set()
    for x, y in positive_points:
        if 0 <= x < w and 0 <= y < h:
            label = int(labels_img[y, x])
            if label > 0:
                keep_labels.add(label)

    if not keep_labels:
        x, y = positive_points[-1]
        return _component_containing_or_nearest(mask, x, y)

    constrained = np.isin(labels_img, list(keep_labels))
    negative_points = [coord for coord, label in zip(coords, point_labels) if label <= 0]
    for x, y in negative_points:
        if 0 <= x < w and 0 <= y < h:
            label = int(labels_img[y, x])
            if label > 0 and label not in keep_labels:
                constrained[labels_img == label] = False
    return constrained.astype(bool)


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    flood = mask.astype(np.uint8).copy()
    flood = np.pad(flood, 1, mode="constant", constant_values=0)
    cv2.floodFill(flood, None, (0, 0), 1)
    holes = flood[1 : h + 1, 1 : w + 1] == 0
    return mask | holes


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def refine_device_mask(
    rgb: np.ndarray,
    depth: np.ndarray,
    raw_mask: np.ndarray,
    prompt: dict,
    config: RgbdMaskRefineConfig = RgbdMaskRefineConfig(),
) -> RgbdMaskRefineResult:
    del rgb
    mask = raw_mask.astype(bool)
    if mask.shape != depth.shape:
        raise ValueError(f"mask/depth shape mismatch: {mask.shape} vs {depth.shape}")

    seed = _seed_xy(prompt)
    mask = _components_for_prompt_points(mask, prompt)

    original_area = int(np.count_nonzero(mask))
    if original_area == 0:
        return RgbdMaskRefineResult(mask, None, 0, None, None, None)

    component_from_prompt = None
    if config.depth_component_union and prompt.get("rgbd_prompt_valid") and prompt.get("sam_box_xyxy") is not None:
        x0, y0, x1, y1 = [int(v) for v in prompt["sam_box_xyxy"]]
        h, w = depth.shape
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w - 1, x1), min(h - 1, y1)
        if x1 > x0 and y1 > y0 and seed is not None:
            seed_depth = _seed_depth(prompt, depth, seed[0], seed[1])
            if seed_depth is not None:
                valid_depth = np.isfinite(depth) & (depth > 0)
                plausible = np.abs(depth - np.float32(seed_depth)) <= np.float32(config.max_global_depth_span_m)
                component_from_prompt = np.zeros_like(mask, dtype=bool)
                component_from_prompt[y0 : y1 + 1, x0 : x1 + 1] = valid_depth[y0 : y1 + 1, x0 : x1 + 1] & plausible[y0 : y1 + 1, x0 : x1 + 1]
                component_from_prompt = _component_containing_or_nearest(component_from_prompt, seed[0], seed[1])
                if config.depth_component_dilate_px > 0:
                    component_from_prompt = cv2.dilate(
                        component_from_prompt.astype(np.uint8),
                        _kernel(config.depth_component_dilate_px),
                        iterations=1,
                    ).astype(bool)
                if np.count_nonzero(component_from_prompt) >= config.min_area_px:
                    overlap = np.count_nonzero(component_from_prompt & mask)
                    if overlap > 0:
                        mask = mask | component_from_prompt

    if seed is not None:
        seed_depth = _seed_depth(prompt, depth, seed[0], seed[1])
        if seed_depth is not None:
            valid_depth = np.isfinite(depth) & (depth > 0)
            plausible = np.abs(depth - np.float32(seed_depth)) <= np.float32(config.max_global_depth_span_m)
            depth_filtered = mask & valid_depth & plausible
            keep_ratio = np.count_nonzero(depth_filtered) / max(original_area, 1)
            if keep_ratio >= float(config.min_depth_keep_ratio):
                mask = depth_filtered
                mask = _components_for_prompt_points(mask, prompt)

    if config.open_px > 0:
        mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, _kernel(config.open_px)).astype(bool)
    if config.close_px > 0:
        mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, _kernel(config.close_px)).astype(bool)
    mask = _fill_holes(mask)
    mask = _components_for_prompt_points(mask, prompt)

    if np.count_nonzero(mask) < int(config.min_area_px):
        mask = raw_mask.astype(bool)
        mask = _components_for_prompt_points(mask, prompt)

    valid_values = depth[mask & np.isfinite(depth) & (depth > 0)]
    if valid_values.size:
        depth_median = float(np.median(valid_values))
        depth_min = float(np.min(valid_values))
        depth_max = float(np.max(valid_values))
    else:
        depth_median = depth_min = depth_max = None

    return RgbdMaskRefineResult(
        mask=mask,
        bbox_xyxy=_bbox(mask),
        area_px=int(np.count_nonzero(mask)),
        depth_median_m=depth_median,
        depth_min_m=depth_min,
        depth_max_m=depth_max,
    )


def overlay_mask(rgb: np.ndarray, mask: np.ndarray, color: tuple[int, int, int] = (0, 255, 120), alpha: float = 0.42) -> np.ndarray:
    out = rgb.astype(np.uint8).copy()
    color_arr = np.array(color, dtype=np.uint8)
    out[mask] = (out[mask].astype(np.float32) * (1.0 - alpha) + color_arr.astype(np.float32) * alpha).astype(np.uint8)
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out, contours, -1, (255, 255, 255), 2)
    return out


def rgba_cutout(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    alpha = (mask.astype(np.uint8) * 255)[:, :, None]
    return np.concatenate([rgb.astype(np.uint8), alpha], axis=2)


def write_refine_outputs(
    result: RgbdMaskRefineResult,
    rgb: np.ndarray,
    depth: np.ndarray,
    out_dir: Path,
    prefix: str = "device",
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    mask_path = out_dir / f"{prefix}_mask.png"
    overlay_path = out_dir / f"{prefix}_overlay.png"
    rgba_path = out_dir / f"{prefix}_cutout.png"
    depth_path = out_dir / f"{prefix}_depth.npy"
    bbox_path = out_dir / f"{prefix}_bbox.json"

    cv2.imwrite(str(mask_path), result.mask.astype(np.uint8) * 255)
    cv2.imwrite(str(overlay_path), cv2.cvtColor(overlay_mask(rgb, result.mask), cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(rgba_path), cv2.cvtColor(rgba_cutout(rgb, result.mask), cv2.COLOR_RGBA2BGRA))
    masked_depth = np.where(result.mask, depth, 0.0).astype(np.float32)
    np.save(depth_path, masked_depth)
    info = {
        "mask": str(mask_path),
        "overlay": str(overlay_path),
        "cutout": str(rgba_path),
        "depth": str(depth_path),
        "bbox_xyxy": result.bbox_xyxy,
        "area_px": result.area_px,
        "depth_median_m": result.depth_median_m,
        "depth_min_m": result.depth_min_m,
        "depth_max_m": result.depth_max_m,
    }
    bbox_path.write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description="Refine a prompted device mask with RGB-D consistency")
    parser.add_argument("--rgb", type=Path, required=True)
    parser.add_argument("--depth", type=Path, required=True)
    parser.add_argument("--mask", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--max-global-depth-span-m", type=float, default=1.20)
    parser.add_argument("--min-depth-keep-ratio", type=float, default=0.35)
    parser.add_argument("--disable-depth-component-union", action="store_true")
    parser.add_argument("--open-px", type=int, default=2)
    parser.add_argument("--close-px", type=int, default=5)
    args = parser.parse_args()

    bgr = cv2.imread(str(args.rgb), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(args.rgb)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    depth = np.load(args.depth).astype(np.float32)
    mask = cv2.imread(str(args.mask), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise FileNotFoundError(args.mask)
    prompt = json.loads(args.prompt.read_text(encoding="utf-8"))
    result = refine_device_mask(
        rgb,
        depth,
        mask > 0,
        prompt,
        RgbdMaskRefineConfig(
            max_global_depth_span_m=args.max_global_depth_span_m,
            min_depth_keep_ratio=args.min_depth_keep_ratio,
            depth_component_union=not args.disable_depth_component_union,
            open_px=args.open_px,
            close_px=args.close_px,
        ),
    )
    info = write_refine_outputs(result, rgb, depth, args.out_dir)
    print(json.dumps({"area_px": result.area_px, "bbox_xyxy": result.bbox_xyxy, "info": info}), flush=True)


if __name__ == "__main__":
    main()
