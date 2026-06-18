from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class EdgeDepthRefineConfig:
    rgb_edge_percentile: float = 90.0
    edge_dilate_px: int = 4
    depth_jump_abs_m: float = 0.12
    depth_jump_rel: float = 0.06
    min_keep_ratio: float = 0.35
    isolated_radius_px: int = 2
    isolated_min_neighbors: int = 2


@dataclass(frozen=True)
class EdgeDepthRefineResult:
    depth: np.ndarray
    unsafe_mask: np.ndarray
    rgb_edge_mask: np.ndarray
    depth_jump_mask: np.ndarray
    isolated_mask: np.ndarray
    removed_count: int
    kept_count: int
    original_valid_count: int


def rgb_edge_mask(rgb: np.ndarray, percentile: float, dilate_px: int) -> np.ndarray:
    gray = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    threshold = float(np.percentile(mag, np.clip(percentile, 0.0, 100.0)))
    mask = mag >= max(threshold, 1e-6)
    return dilate(mask, dilate_px)


def depth_jump_mask(depth: np.ndarray, abs_threshold_m: float, rel_threshold: float, dilate_px: int) -> np.ndarray:
    d = depth.astype(np.float32, copy=False)
    valid = np.isfinite(d) & (d > 0)
    jump = np.zeros(d.shape, dtype=bool)

    for dy, dx in ((0, 1), (1, 0)):
        a = d[:-dy or None, :-dx or None]
        b = d[dy:, dx:]
        va = valid[:-dy or None, :-dx or None]
        vb = valid[dy:, dx:]
        both = va & vb
        delta = np.abs(a - b)
        scale = np.maximum(np.minimum(a, b), 1e-6)
        edge = both & ((delta >= abs_threshold_m) | ((delta / scale) >= rel_threshold))
        if dy == 0:
            jump[:, :-1] |= edge
            jump[:, 1:] |= edge
        else:
            jump[:-1, :] |= edge
            jump[1:, :] |= edge

    return dilate(jump, dilate_px)


def dilate(mask: np.ndarray, radius_px: int) -> np.ndarray:
    radius_px = int(max(0, radius_px))
    if radius_px == 0:
        return mask.astype(bool, copy=True)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius_px * 2 + 1, radius_px * 2 + 1))
    return cv2.dilate(mask.astype(np.uint8), kernel, iterations=1).astype(bool)


def refine_depth_anchors(
    rgb: np.ndarray,
    aligned_depth: np.ndarray,
    config: EdgeDepthRefineConfig = EdgeDepthRefineConfig(),
) -> EdgeDepthRefineResult:
    depth = aligned_depth.astype(np.float32, copy=True)
    valid = np.isfinite(depth) & (depth > 0)
    original_valid = int(np.count_nonzero(valid))
    if original_valid == 0:
        empty = np.zeros(depth.shape, dtype=bool)
        return EdgeDepthRefineResult(depth, empty, empty, empty, empty, 0, 0, 0)

    rgb_edges = rgb_edge_mask(rgb, config.rgb_edge_percentile, config.edge_dilate_px)
    depth_jumps = depth_jump_mask(
        depth,
        config.depth_jump_abs_m,
        config.depth_jump_rel,
        config.edge_dilate_px,
    )
    isolated = isolated_depth_anchor_mask(
        depth,
        config.isolated_radius_px,
        config.isolated_min_neighbors,
    )
    unsafe = (rgb_edges | depth_jumps | isolated) & valid

    candidate = depth.copy()
    candidate[unsafe] = 0.0
    kept = int(np.count_nonzero(candidate > 0))
    min_keep = int(round(original_valid * np.clip(config.min_keep_ratio, 0.0, 1.0)))
    if kept < min_keep:
        # Fail open when the image is highly textured or the sparse map is tiny;
        # a badly over-pruned prompt can harm completion more than noisy edges.
        candidate = depth
        unsafe = np.zeros(depth.shape, dtype=bool)
        kept = original_valid

    removed = original_valid - kept
    return EdgeDepthRefineResult(
        depth=candidate,
        unsafe_mask=unsafe,
        rgb_edge_mask=rgb_edges,
        depth_jump_mask=depth_jumps,
        isolated_mask=isolated,
        removed_count=removed,
        kept_count=kept,
        original_valid_count=original_valid,
    )


def isolated_depth_anchor_mask(depth: np.ndarray, radius_px: int, min_neighbors: int) -> np.ndarray:
    valid = np.isfinite(depth) & (depth > 0)
    if not np.any(valid):
        return np.zeros(depth.shape, dtype=bool)
    radius_px = max(1, int(radius_px))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius_px * 2 + 1, radius_px * 2 + 1))
    count = cv2.filter2D(valid.astype(np.uint8), cv2.CV_16U, kernel)
    return valid & (count <= int(max(1, min_neighbors)))


def main() -> None:
    parser = argparse.ArgumentParser(description="RGB-edge-aware sparse aligned depth refinement")
    parser.add_argument("--rgb", type=Path, required=True)
    parser.add_argument("--depth", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mask-out", type=Path, default=None)
    parser.add_argument("--rgb-edge-percentile", type=float, default=90.0)
    parser.add_argument("--edge-dilate-px", type=int, default=4)
    parser.add_argument("--depth-jump-abs-m", type=float, default=0.12)
    parser.add_argument("--depth-jump-rel", type=float, default=0.06)
    parser.add_argument("--min-keep-ratio", type=float, default=0.35)
    parser.add_argument("--isolated-radius-px", type=int, default=2)
    parser.add_argument("--isolated-min-neighbors", type=int, default=2)
    args = parser.parse_args()

    bgr = cv2.imread(str(args.rgb), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(args.rgb)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    depth = np.load(args.depth).astype(np.float32)
    result = refine_depth_anchors(
        rgb,
        depth,
        EdgeDepthRefineConfig(
            rgb_edge_percentile=args.rgb_edge_percentile,
            edge_dilate_px=args.edge_dilate_px,
            depth_jump_abs_m=args.depth_jump_abs_m,
            depth_jump_rel=args.depth_jump_rel,
            min_keep_ratio=args.min_keep_ratio,
            isolated_radius_px=args.isolated_radius_px,
            isolated_min_neighbors=args.isolated_min_neighbors,
        ),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.save(args.out, result.depth.astype(np.float32))
    if args.mask_out is not None:
        args.mask_out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.mask_out), (result.unsafe_mask.astype(np.uint8) * 255))
    print(
        f"valid={result.original_valid_count} kept={result.kept_count} removed={result.removed_count}",
        flush=True,
    )


if __name__ == "__main__":
    main()
