from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from rgb_edge_depth_refine import dilate, rgb_edge_mask
from rgb_edge_depth_refine import depth_jump_mask


@dataclass(frozen=True)
class RgbGuidedPostprocessConfig:
    color_sigma: float = 18.0
    depth_sigma_m: float = 0.20
    iterations: int = 3
    anchor_radius_px: int = 3
    edge_percentile: float = 90.0
    edge_dilate_px: int = 2
    sparse_trust_radius_px: int = 12
    sparse_blend: float = 0.85
    depth_edge_abs_m: float = 0.12
    depth_edge_rel: float = 0.06
    boundary_dilate_px: int = 2
    plane_smooth_radius_px: int = 5
    plane_smooth_weight: float = 0.35


@dataclass(frozen=True)
class RgbGuidedPostprocessResult:
    depth: np.ndarray
    confidence: np.ndarray
    edge_mask: np.ndarray
    anchor_mask: np.ndarray
    depth_edge_mask: np.ndarray
    corrected_mask: np.ndarray


def _sanitize_depth(depth: np.ndarray) -> np.ndarray:
    out = depth.astype(np.float32, copy=True)
    out[~np.isfinite(out)] = 0.0
    out[out < 0.0] = 0.0
    return out


def _build_confidence(
    sparse_depth: np.ndarray,
    edge_mask: np.ndarray,
    config: RgbGuidedPostprocessConfig,
) -> tuple[np.ndarray, np.ndarray]:
    valid_sparse = np.isfinite(sparse_depth) & (sparse_depth > 0)
    anchor_mask = dilate(valid_sparse, config.anchor_radius_px)

    if np.any(valid_sparse):
        invalid_u8 = (~valid_sparse).astype(np.uint8)
        dist = cv2.distanceTransform(invalid_u8, cv2.DIST_L2, 3).astype(np.float32)
        radius = max(float(config.sparse_trust_radius_px), 1.0)
        confidence = np.exp(-dist / radius).astype(np.float32)
        confidence[valid_sparse] = 1.0
    else:
        confidence = np.full(sparse_depth.shape, 0.15, dtype=np.float32)

    confidence[edge_mask] *= 0.35
    confidence[anchor_mask] = np.maximum(confidence[anchor_mask], 0.8)
    return np.clip(confidence, 0.0, 1.0), anchor_mask


def _weighted_box_filter(values: np.ndarray, weights: np.ndarray, radius_px: int) -> np.ndarray:
    radius_px = max(1, int(radius_px))
    ksize = radius_px * 2 + 1
    numerator = cv2.boxFilter(values * weights, cv2.CV_32F, (ksize, ksize), normalize=False)
    denominator = cv2.boxFilter(weights, cv2.CV_32F, (ksize, ksize), normalize=False)
    return numerator / np.maximum(denominator, 1e-6)


def _snap_boundary_to_sparse(
    dense_depth: np.ndarray,
    sparse_depth: np.ndarray,
    boundary_mask: np.ndarray,
    anchor_radius_px: int,
    max_delta_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    refined = dense_depth.astype(np.float32, copy=True)
    valid_sparse = sparse_depth > 0
    if not np.any(valid_sparse):
        return refined, np.zeros(dense_depth.shape, dtype=bool)

    h, w = dense_depth.shape
    corrected = np.zeros((h, w), dtype=bool)
    radius = max(1, int(anchor_radius_px))
    ys, xs = np.where(boundary_mask & (dense_depth > 0))
    for y, x in zip(ys.tolist(), xs.tolist()):
        y0 = max(0, y - radius)
        y1 = min(h, y + radius + 1)
        x0 = max(0, x - radius)
        x1 = min(w, x + radius + 1)
        local = sparse_depth[y0:y1, x0:x1]
        local_y, local_x = np.where(local > 0)
        if local_x.size == 0:
            continue
        dx = local_x + x0 - x
        dy = local_y + y0 - y
        idx = int(np.argmin(dx * dx + dy * dy))
        anchor = float(local[local_y[idx], local_x[idx]])
        current = float(refined[y, x])
        if current > 0 and abs(current - anchor) <= max_delta_m:
            refined[y, x] = np.float32(anchor)
            corrected[y, x] = True
    return refined, corrected


def _shift(arr: np.ndarray, dy: int, dx: int) -> np.ndarray:
    shifted = np.empty_like(arr)
    if dy < 0:
        shifted[:dy, :] = arr[-dy:, :]
        shifted[dy:, :] = arr[-1:, :]
    elif dy > 0:
        shifted[dy:, :] = arr[:-dy, :]
        shifted[:dy, :] = arr[:1, :]
    else:
        shifted[:, :] = arr

    base = shifted.copy()
    if dx < 0:
        shifted[:, :dx] = base[:, -dx:]
        shifted[:, dx:] = base[:, -1:]
    elif dx > 0:
        shifted[:, dx:] = base[:, :-dx]
        shifted[:, :dx] = base[:, :1]
    return shifted


def _edge_aware_smooth(
    rgb: np.ndarray,
    dense_depth: np.ndarray,
    confidence: np.ndarray,
    anchor_mask: np.ndarray,
    sparse_depth: np.ndarray,
    boundary_mask: np.ndarray,
    config: RgbGuidedPostprocessConfig,
) -> np.ndarray:
    rgb_f = rgb.astype(np.float32)
    current = dense_depth.astype(np.float32, copy=True)
    current[current <= 0] = 0.0

    color_sigma2 = max(float(config.color_sigma) ** 2, 1e-6)
    depth_sigma2 = max(float(config.depth_sigma_m) ** 2, 1e-6)
    center_weight = np.float32(1.0)
    offsets = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))

    for _ in range(max(0, int(config.iterations))):
        numerator = current * center_weight
        denominator = np.full(current.shape, center_weight, dtype=np.float32)

        for dy, dx in offsets:
            n_depth = _shift(current, dy, dx)
            n_rgb = _shift(rgb_f, dy, dx)
            n_conf = _shift(confidence, dy, dx)
            color_dist2 = np.sum((rgb_f - n_rgb) ** 2, axis=2)
            depth_dist2 = (current - n_depth) ** 2
            n_boundary = _shift(boundary_mask, dy, dx)
            valid = (current > 0) & (n_depth > 0) & ~(boundary_mask | n_boundary)
            weight = np.exp(-color_dist2 / (2.0 * color_sigma2)) * np.exp(-depth_dist2 / (2.0 * depth_sigma2))
            weight = (weight.astype(np.float32) * (0.25 + 0.75 * n_conf)) * valid.astype(np.float32)
            numerator += n_depth * weight
            denominator += weight

        updated = numerator / np.maximum(denominator, 1e-6)
        if np.any(anchor_mask):
            blend = float(np.clip(config.sparse_blend, 0.0, 1.0))
            anchored_sparse = sparse_depth > 0
            updated[anchored_sparse] = (
                sparse_depth[anchored_sparse] * blend
                + updated[anchored_sparse] * (1.0 - blend)
            )
        current = updated.astype(np.float32, copy=False)

    return current


def postprocess_depth(
    rgb: np.ndarray,
    sparse_depth: np.ndarray,
    dense_depth: np.ndarray,
    config: RgbGuidedPostprocessConfig = RgbGuidedPostprocessConfig(),
) -> RgbGuidedPostprocessResult:
    if rgb.shape[:2] != dense_depth.shape or sparse_depth.shape != dense_depth.shape:
        raise ValueError(
            f"shape mismatch: rgb={rgb.shape[:2]} sparse={sparse_depth.shape} dense={dense_depth.shape}"
        )

    sparse = _sanitize_depth(sparse_depth)
    dense = _sanitize_depth(dense_depth)
    rgb_edges = rgb_edge_mask(rgb, config.edge_percentile, config.edge_dilate_px)
    sparse_depth_edges = depth_jump_mask(
        sparse,
        config.depth_edge_abs_m,
        config.depth_edge_rel,
        config.boundary_dilate_px,
    )
    dense_depth_edges = depth_jump_mask(
        dense,
        config.depth_edge_abs_m,
        config.depth_edge_rel,
        config.boundary_dilate_px,
    )
    depth_edges = sparse_depth_edges | dense_depth_edges
    boundary = dilate(rgb_edges | depth_edges, config.boundary_dilate_px)
    snapped, corrected = _snap_boundary_to_sparse(
        dense,
        sparse,
        boundary,
        config.anchor_radius_px,
        config.depth_sigma_m * 1.8,
    )
    confidence, anchor_mask = _build_confidence(sparse, boundary, config)
    confidence[corrected] = 1.0
    refined = _edge_aware_smooth(rgb, snapped, confidence, anchor_mask, sparse, boundary, config)

    smooth_weight = float(np.clip(config.plane_smooth_weight, 0.0, 1.0))
    if smooth_weight > 0:
        planar = (~boundary) & (refined > 0)
        plane_smooth = _weighted_box_filter(
            refined,
            planar.astype(np.float32),
            config.plane_smooth_radius_px,
        )
        valid_plane = planar & (plane_smooth > 0)
        refined[valid_plane] = (
            refined[valid_plane] * (1.0 - smooth_weight)
            + plane_smooth[valid_plane] * smooth_weight
        )
        refined[boundary] = snapped[boundary]

    refined[~np.isfinite(refined)] = 0.0
    refined[refined < 0.0] = 0.0
    return RgbGuidedPostprocessResult(
        depth=refined.astype(np.float32),
        confidence=confidence.astype(np.float32),
        edge_mask=boundary,
        anchor_mask=anchor_mask,
        depth_edge_mask=depth_edges,
        corrected_mask=corrected,
    )


def confidence_overlay(rgb: np.ndarray, confidence: np.ndarray) -> np.ndarray:
    conf_u8 = np.clip(confidence * 255.0, 0, 255).astype(np.uint8)
    heat_bgr = cv2.applyColorMap(conf_u8, cv2.COLORMAP_VIRIDIS)
    heat_rgb = cv2.cvtColor(heat_bgr, cv2.COLOR_BGR2RGB)
    return cv2.addWeighted(rgb.astype(np.uint8), 0.55, heat_rgb, 0.45, 0.0)


def _read_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def main() -> None:
    parser = argparse.ArgumentParser(description="RGB-guided dense depth postprocess")
    parser.add_argument("--rgb", type=Path, required=True)
    parser.add_argument("--sparse", type=Path, required=True)
    parser.add_argument("--dense", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--confidence-out", type=Path, default=None)
    parser.add_argument("--overlay-out", type=Path, default=None)
    parser.add_argument("--edge-mask-out", type=Path, default=None)
    parser.add_argument("--color-sigma", type=float, default=18.0)
    parser.add_argument("--depth-sigma-m", type=float, default=0.20)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--anchor-radius-px", type=int, default=3)
    parser.add_argument("--edge-percentile", type=float, default=90.0)
    parser.add_argument("--edge-dilate-px", type=int, default=2)
    parser.add_argument("--sparse-trust-radius-px", type=int, default=12)
    parser.add_argument("--sparse-blend", type=float, default=0.85)
    parser.add_argument("--depth-edge-abs-m", type=float, default=0.12)
    parser.add_argument("--depth-edge-rel", type=float, default=0.06)
    parser.add_argument("--boundary-dilate-px", type=int, default=2)
    parser.add_argument("--plane-smooth-radius-px", type=int, default=5)
    parser.add_argument("--plane-smooth-weight", type=float, default=0.35)
    args = parser.parse_args()

    result = postprocess_depth(
        _read_rgb(args.rgb),
        np.load(args.sparse),
        np.load(args.dense),
        RgbGuidedPostprocessConfig(
            color_sigma=args.color_sigma,
            depth_sigma_m=args.depth_sigma_m,
            iterations=args.iterations,
            anchor_radius_px=args.anchor_radius_px,
            edge_percentile=args.edge_percentile,
            edge_dilate_px=args.edge_dilate_px,
            sparse_trust_radius_px=args.sparse_trust_radius_px,
            sparse_blend=args.sparse_blend,
            depth_edge_abs_m=args.depth_edge_abs_m,
            depth_edge_rel=args.depth_edge_rel,
            boundary_dilate_px=args.boundary_dilate_px,
            plane_smooth_radius_px=args.plane_smooth_radius_px,
            plane_smooth_weight=args.plane_smooth_weight,
        ),
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.save(args.out, result.depth)
    if args.confidence_out is not None:
        args.confidence_out.parent.mkdir(parents=True, exist_ok=True)
        np.save(args.confidence_out, result.confidence)
    if args.overlay_out is not None:
        args.overlay_out.parent.mkdir(parents=True, exist_ok=True)
        overlay_rgb = confidence_overlay(_read_rgb(args.rgb), result.confidence)
        cv2.imwrite(str(args.overlay_out), cv2.cvtColor(overlay_rgb, cv2.COLOR_RGB2BGR))
    if args.edge_mask_out is not None:
        args.edge_mask_out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.edge_mask_out), result.edge_mask.astype(np.uint8) * 255)

    print(
        f"valid={int(np.count_nonzero(result.depth > 0))} "
        f"confidence_mean={float(np.mean(result.confidence)):.4f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
