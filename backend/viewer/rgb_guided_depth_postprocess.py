from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from rgb_edge_depth_refine import dilate, rgb_edge_mask


@dataclass(frozen=True)
class RgbGuidedPostprocessConfig:
    color_sigma: float = 24.0
    depth_sigma_m: float = 0.28
    iterations: int = 4
    anchor_radius_px: int = 3
    edge_percentile: float = 88.0
    edge_dilate_px: int = 2
    sparse_trust_radius_px: int = 12
    sparse_blend: float = 0.85


@dataclass(frozen=True)
class RgbGuidedPostprocessResult:
    depth: np.ndarray
    confidence: np.ndarray
    edge_mask: np.ndarray
    anchor_mask: np.ndarray


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
            valid = (current > 0) & (n_depth > 0)
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
    edges = rgb_edge_mask(rgb, config.edge_percentile, config.edge_dilate_px)
    confidence, anchor_mask = _build_confidence(sparse, edges, config)
    refined = _edge_aware_smooth(rgb, dense, confidence, anchor_mask, sparse, config)
    refined[~np.isfinite(refined)] = 0.0
    refined[refined < 0.0] = 0.0
    return RgbGuidedPostprocessResult(
        depth=refined.astype(np.float32),
        confidence=confidence.astype(np.float32),
        edge_mask=edges,
        anchor_mask=anchor_mask,
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
    parser.add_argument("--color-sigma", type=float, default=24.0)
    parser.add_argument("--depth-sigma-m", type=float, default=0.28)
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--anchor-radius-px", type=int, default=3)
    parser.add_argument("--edge-percentile", type=float, default=88.0)
    parser.add_argument("--edge-dilate-px", type=int, default=2)
    parser.add_argument("--sparse-trust-radius-px", type=int, default=12)
    parser.add_argument("--sparse-blend", type=float, default=0.85)
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
