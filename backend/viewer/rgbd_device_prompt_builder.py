from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class RgbdPromptConfig:
    local_depth_jump_m: float = 0.06
    local_depth_jump_rel: float = 0.05
    global_depth_span_m: float = 0.55
    max_radius_px: int = 170
    bbox_pad_px: int = 10
    max_positive_points: int = 9
    max_negative_points: int = 12
    negative_margin_px: int = 28
    min_component_area_px: int = 80
    max_component_area_ratio: float = 0.08
    rgb_edge_percentile: float = 92.0
    rgb_edge_dilate_px: int = 1
    rgb_edge_requires_depth_jump: bool = False
    enable_stage2_growth: bool = True
    stage2_dilate_px: int = 8
    stage2_max_radius_px: int = 250


@dataclass(frozen=True)
class RgbdPromptResult:
    prompt: dict
    component_mask: np.ndarray


def _seed_xy(prompt: dict) -> tuple[int, int]:
    if prompt.get("valid") and "rgb_x" in prompt and "rgb_y" in prompt:
        return int(prompt["rgb_x"]), int(prompt["rgb_y"])
    coords = prompt.get("point_coords") or []
    if not coords:
        raise ValueError("prompt has no cursor point")
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


def _nearest_valid_seed(depth: np.ndarray, x: int, y: int, radius: int = 16) -> tuple[int, int, float] | None:
    h, w = depth.shape
    if 0 <= x < w and 0 <= y < h and np.isfinite(depth[y, x]) and depth[y, x] > 0:
        return x, y, float(depth[y, x])
    x0 = max(0, x - radius)
    x1 = min(w, x + radius + 1)
    y0 = max(0, y - radius)
    y1 = min(h, y + radius + 1)
    crop = depth[y0:y1, x0:x1]
    valid_y, valid_x = np.where(np.isfinite(crop) & (crop > 0))
    if valid_x.size == 0:
        return None
    dx = valid_x + x0 - x
    dy = valid_y + y0 - y
    idx = int(np.argmin(dx * dx + dy * dy))
    sx = int(valid_x[idx] + x0)
    sy = int(valid_y[idx] + y0)
    return sx, sy, float(depth[sy, sx])


def _grow_depth_component(
    depth: np.ndarray,
    rgb: np.ndarray | None,
    seed_x: int,
    seed_y: int,
    seed_depth: float,
    config: RgbdPromptConfig,
) -> np.ndarray:
    h, w = depth.shape
    x0 = max(0, seed_x - config.max_radius_px)
    x1 = min(w, seed_x + config.max_radius_px + 1)
    y0 = max(0, seed_y - config.max_radius_px)
    y1 = min(h, seed_y + config.max_radius_px + 1)
    crop = depth[y0:y1, x0:x1].astype(np.float32, copy=False)
    valid = np.isfinite(crop) & (crop > 0)
    global_ok = valid & (np.abs(crop - np.float32(seed_depth)) <= np.float32(config.global_depth_span_m))
    if not global_ok.any():
        return np.zeros(depth.shape, dtype=bool)

    edge_block = np.zeros_like(global_ok, dtype=bool)
    if rgb is not None and rgb.shape[:2] == depth.shape:
        edge_full = _rgb_edge_mask(rgb, config.rgb_edge_percentile, config.rgb_edge_dilate_px)
        edge_block = edge_full[y0:y1, x0:x1]

    local_jump = max(float(config.local_depth_jump_m), abs(float(seed_depth)) * float(config.local_depth_jump_rel))
    h_crop, w_crop = crop.shape
    sx = seed_x - x0
    sy = seed_y - y0
    component = np.zeros((h_crop, w_crop), dtype=bool)
    if not (0 <= sx < w_crop and 0 <= sy < h_crop) or not global_ok[sy, sx]:
        return np.zeros(depth.shape, dtype=bool)

    queue_x = [int(sx)]
    queue_y = [int(sy)]
    component[sy, sx] = True
    head = 0
    while head < len(queue_x):
        cx = queue_x[head]
        cy = queue_y[head]
        head += 1
        current_depth = float(crop[cy, cx])
        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if nx < 0 or nx >= w_crop or ny < 0 or ny >= h_crop or component[ny, nx]:
                continue
            if not global_ok[ny, nx]:
                continue
            if abs(float(crop[ny, nx]) - current_depth) > local_jump:
                continue
            if edge_block[ny, nx] and not config.rgb_edge_requires_depth_jump:
                continue
            component[ny, nx] = True
            queue_x.append(nx)
            queue_y.append(ny)

    full = np.zeros(depth.shape, dtype=bool)
    full[y0:y1, x0:x1] = component
    return full


def _grow_depth_component_stage2(
    depth: np.ndarray,
    seed_mask: np.ndarray,
    seed_depth: float,
    config: RgbdPromptConfig,
) -> np.ndarray:
    """Grow past RGB edges to recover the enclosing same-depth object."""
    bbox = _mask_bbox(seed_mask, 0)
    if bbox is None:
        return seed_mask.copy()

    h, w = depth.shape
    x0, y0, x1, y1 = bbox
    radius = max(0, int(config.stage2_max_radius_px))
    x0 = max(0, x0 - radius)
    y0 = max(0, y0 - radius)
    x1 = min(w, x1 + radius + 1)
    y1 = min(h, y1 + radius + 1)

    crop = depth[y0:y1, x0:x1].astype(np.float32, copy=False)
    valid = np.isfinite(crop) & (crop > 0)
    global_ok = valid & (np.abs(crop - np.float32(seed_depth)) <= np.float32(config.global_depth_span_m))
    if not global_ok.any():
        return seed_mask.copy()

    local_jump = max(float(config.local_depth_jump_m), abs(float(seed_depth)) * float(config.local_depth_jump_rel))
    component = seed_mask[y0:y1, x0:x1].copy()
    queue_y, queue_x = np.where(component & global_ok)
    if queue_x.size == 0:
        return seed_mask.copy()

    queue_x = queue_x.astype(int).tolist()
    queue_y = queue_y.astype(int).tolist()
    h_crop, w_crop = crop.shape
    head = 0
    while head < len(queue_x):
        cx = queue_x[head]
        cy = queue_y[head]
        head += 1
        current_depth = float(crop[cy, cx])
        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if nx < 0 or nx >= w_crop or ny < 0 or ny >= h_crop or component[ny, nx]:
                continue
            if not global_ok[ny, nx]:
                continue
            if abs(float(crop[ny, nx]) - current_depth) > local_jump:
                continue
            component[ny, nx] = True
            queue_x.append(nx)
            queue_y.append(ny)

    full = seed_mask.copy()
    full[y0:y1, x0:x1] = component
    return full


def _rgb_edge_mask(rgb: np.ndarray, percentile: float, dilate_px: int) -> np.ndarray:
    gray = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    threshold = float(np.percentile(mag, np.clip(percentile, 0.0, 100.0)))
    mask = mag >= max(threshold, 1e-6)
    radius = max(0, int(dilate_px))
    if radius > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        mask = cv2.dilate(mask.astype(np.uint8), kernel, iterations=1).astype(bool)
    return mask.astype(bool)


def _largest_or_seed_component(mask: np.ndarray, x: int, y: int) -> np.ndarray:
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if num_labels <= 1:
        return mask.astype(bool)
    if 0 <= x < mask.shape[1] and 0 <= y < mask.shape[0]:
        label = int(labels[y, x])
        if label > 0:
            return labels == label
    areas = stats[1:, cv2.CC_STAT_AREA]
    label = int(np.argmax(areas)) + 1
    return labels == label


def _mask_bbox(mask: np.ndarray, pad: int) -> list[int] | None:
    ys, xs = np.where(mask)
    if xs.size == 0:
        return None
    h, w = mask.shape
    return [
        int(max(0, xs.min() - pad)),
        int(max(0, ys.min() - pad)),
        int(min(w - 1, xs.max() + pad)),
        int(min(h - 1, ys.max() + pad)),
    ]


def _sample_positive_points(mask: np.ndarray, seed_x: int, seed_y: int, max_points: int) -> list[list[int]]:
    points = [[int(seed_x), int(seed_y)]]
    ys, xs = np.where(mask)
    if xs.size == 0 or max_points <= 1:
        return points[:max_points]
    bbox = _mask_bbox(mask, 0)
    assert bbox is not None
    x0, y0, x1, y1 = bbox
    candidates = [
        ((x0 + x1) // 2, (y0 + y1) // 2),
        (x0, (y0 + y1) // 2),
        (x1, (y0 + y1) // 2),
        ((x0 + x1) // 2, y0),
        ((x0 + x1) // 2, y1),
        (x0, y0),
        (x1, y0),
        (x0, y1),
        (x1, y1),
    ]
    dist = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    for cx, cy in candidates:
        if not (0 <= cx < mask.shape[1] and 0 <= cy < mask.shape[0]) or not mask[cy, cx]:
            yy, xx = np.where(mask)
            if xx.size == 0:
                continue
            idx = int(np.argmin((xx - cx) ** 2 + (yy - cy) ** 2))
            cx = int(xx[idx])
            cy = int(yy[idx])
        if dist[cy, cx] < 2.0:
            continue
        point = [int(cx), int(cy)]
        if point not in points:
            points.append(point)
        if len(points) >= max_points:
            break
    return points


def _sample_negative_points(mask: np.ndarray, bbox: list[int] | None, max_points: int, margin: int) -> list[list[int]]:
    if bbox is None or max_points <= 0:
        return []
    h, w = mask.shape
    x0, y0, x1, y1 = bbox
    x0m = max(0, x0 - margin)
    y0m = max(0, y0 - margin)
    x1m = min(w - 1, x1 + margin)
    y1m = min(h - 1, y1 + margin)
    candidates = [
        ((x0m + x1m) // 2, y0m),
        ((x0m + x1m) // 2, y1m),
        (x0m, (y0m + y1m) // 2),
        (x1m, (y0m + y1m) // 2),
        (x0m, y0m),
        (x1m, y0m),
        (x0m, y1m),
        (x1m, y1m),
        ((x0 + x1) // 2, max(0, y0 - margin // 2)),
        ((x0 + x1) // 2, min(h - 1, y1 + margin // 2)),
        (max(0, x0 - margin // 2), (y0 + y1) // 2),
        (min(w - 1, x1 + margin // 2), (y0 + y1) // 2),
    ]
    points: list[list[int]] = []
    for x, y in candidates:
        if 0 <= x < w and 0 <= y < h and not mask[y, x]:
            point = [int(x), int(y)]
            if point not in points:
                points.append(point)
        if len(points) >= max_points:
            break
    return points


def build_rgbd_device_prompt(
    depth: np.ndarray,
    cursor_prompt: dict,
    rgb: np.ndarray | None = None,
    config: RgbdPromptConfig = RgbdPromptConfig(),
) -> RgbdPromptResult:
    if not cursor_prompt.get("valid", False):
        return RgbdPromptResult({**cursor_prompt, "rgbd_prompt_valid": False}, np.zeros(depth.shape, dtype=bool))

    seed_x, seed_y = _seed_xy(cursor_prompt)
    seed_depth = _seed_depth(cursor_prompt, depth, seed_x, seed_y)
    nearest = _nearest_valid_seed(depth, seed_x, seed_y)
    if seed_depth is None and nearest is not None:
        seed_x, seed_y, seed_depth = nearest
    if seed_depth is None:
        return RgbdPromptResult({**cursor_prompt, "rgbd_prompt_valid": False, "rgbd_prompt_reason": "no_seed_depth"}, np.zeros(depth.shape, dtype=bool))

    component = _grow_depth_component(depth, rgb, seed_x, seed_y, seed_depth, config)
    component = _largest_or_seed_component(component, seed_x, seed_y)
    component_area = int(np.count_nonzero(component))
    if config.enable_stage2_growth and component_area > 0:
        radius = max(0, int(config.stage2_dilate_px))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        stage2_seed = cv2.dilate(component.astype(np.uint8), kernel, iterations=1).astype(bool)
        stage2_component = _grow_depth_component_stage2(depth, stage2_seed, seed_depth, config)
        stage2_component = _largest_or_seed_component(stage2_component, seed_x, seed_y)
        stage2_area = int(np.count_nonzero(stage2_component))
        stage2_bbox = _mask_bbox(stage2_component, config.bbox_pad_px)
        stage2_bbox_ratio = 0.0
        if stage2_bbox is not None:
            x0, y0, x1, y1 = stage2_bbox
            stage2_bbox_ratio = float((x1 - x0 + 1) * (y1 - y0 + 1)) / float(depth.shape[0] * depth.shape[1])
        if stage2_area > int(component_area * 1.5) and stage2_bbox_ratio <= float(config.max_component_area_ratio):
            component = stage2_component
            component_area = stage2_area

    bbox = _mask_bbox(component, config.bbox_pad_px)
    bbox_area_ratio = 0.0
    if bbox is not None:
        x0, y0, x1, y1 = bbox
        bbox_area_ratio = float((x1 - x0 + 1) * (y1 - y0 + 1)) / float(depth.shape[0] * depth.shape[1])

    if (
        component_area < int(config.min_component_area_px)
        or bbox_area_ratio > float(config.max_component_area_ratio)
    ):
        component = np.zeros(depth.shape, dtype=bool)
        component_area = 0

    bbox = _mask_bbox(component, config.bbox_pad_px)
    positives = _sample_positive_points(component, seed_x, seed_y, config.max_positive_points)
    negatives = _sample_negative_points(component, bbox, config.max_negative_points, config.negative_margin_px)
    point_coords = positives + negatives
    point_labels = [1] * len(positives) + [0] * len(negatives)

    prompt = {
        **cursor_prompt,
        "rgb_x": int(seed_x),
        "rgb_y": int(seed_y),
        "depth_sample_m": float(seed_depth),
        "rgbd_prompt_valid": bbox is not None,
        "rgbd_component_area_px": component_area,
        "rgbd_component_bbox_area_ratio": bbox_area_ratio,
        "sam_box_xyxy": bbox,
        "sam_point_coords": point_coords,
        "sam_point_labels": point_labels,
    }
    return RgbdPromptResult(prompt, component)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build depth-guided SAM prompts for device segmentation")
    parser.add_argument("--rgb", type=Path, default=None)
    parser.add_argument("--depth", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--component-mask-out", type=Path, default=None)
    parser.add_argument("--global-depth-span-m", type=float, default=0.55)
    parser.add_argument("--local-depth-jump-m", type=float, default=0.06)
    parser.add_argument("--local-depth-jump-rel", type=float, default=0.05)
    parser.add_argument("--max-radius-px", type=int, default=170)
    parser.add_argument("--bbox-pad-px", type=int, default=10)
    parser.add_argument("--max-component-area-ratio", type=float, default=0.08)
    args = parser.parse_args()

    rgb = None
    if args.rgb is not None:
        bgr = cv2.imread(str(args.rgb), cv2.IMREAD_COLOR)
        if bgr is None:
            raise FileNotFoundError(args.rgb)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    depth = np.load(args.depth).astype(np.float32)
    prompt = json.loads(args.prompt.read_text(encoding="utf-8"))
    result = build_rgbd_device_prompt(
        depth,
        prompt,
        rgb,
        RgbdPromptConfig(
            global_depth_span_m=args.global_depth_span_m,
            local_depth_jump_m=args.local_depth_jump_m,
            local_depth_jump_rel=args.local_depth_jump_rel,
            max_radius_px=args.max_radius_px,
            bbox_pad_px=args.bbox_pad_px,
            max_component_area_ratio=args.max_component_area_ratio,
        ),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result.prompt, indent=2), encoding="utf-8")
    if args.component_mask_out is not None:
        args.component_mask_out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.component_mask_out), result.component_mask.astype(np.uint8) * 255)
    print(
        json.dumps(
            {
                "valid": bool(result.prompt.get("rgbd_prompt_valid", False)),
                "area_px": int(result.prompt.get("rgbd_component_area_px", 0)),
                "box": result.prompt.get("sam_box_xyxy"),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
