from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class FrameSample:
    rgb_w: int
    rgb_h: int
    depth_w: int
    depth_h: int
    rgb_points_xy: np.ndarray
    depth_dt: np.ndarray


def _load_depth(depth_path: Path, w: int, h: int) -> np.ndarray | None:
    raw = np.fromfile(depth_path, dtype=np.float32)
    if raw.size != w * h:
        return None
    return raw.reshape((h, w))


def _depth_edges(depth: np.ndarray) -> np.ndarray:
    valid = np.isfinite(depth) & (depth > 0)
    if np.count_nonzero(valid) < 64:
        return np.zeros(depth.shape, dtype=np.uint8)

    vals = depth[valid]
    lo = float(np.percentile(vals, 5))
    hi = float(np.percentile(vals, 95))
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        return np.zeros(depth.shape, dtype=np.uint8)

    d8 = np.zeros(depth.shape, dtype=np.uint8)
    norm = (np.clip(depth, lo, hi) - lo) / (hi - lo)
    d8[valid] = np.uint8((1.0 - norm[valid]) * 255.0)

    edges = cv2.Canny(d8, 25, 90)
    return edges


def _rgb_edge_points(gray: np.ndarray, max_points: int = 3000) -> np.ndarray:
    edges = cv2.Canny(gray, 60, 140)
    pts = np.argwhere(edges > 0)
    if pts.shape[0] == 0:
        return np.empty((0, 2), dtype=np.float32)
    if pts.shape[0] > max_points:
        idx = np.linspace(0, pts.shape[0] - 1, max_points).astype(np.int32)
        pts = pts[idx]
    return np.stack([pts[:, 1], pts[:, 0]], axis=1).astype(np.float32)


def load_samples(capture_dir: Path, max_frames: int, seed: int) -> list[FrameSample]:
    pair_dirs = sorted([p for p in capture_dir.glob("pair_*") if p.is_dir()])
    if not pair_dirs:
        raise RuntimeError(f"No pair_* directories in {capture_dir}")

    if max_frames > 0 and len(pair_dirs) > max_frames:
        rnd = random.Random(seed)
        pair_dirs = sorted(rnd.sample(pair_dirs, max_frames))

    samples: list[FrameSample] = []
    for pair_dir in pair_dirs:
        meta_path = pair_dir / "meta.json"
        rgb_path = pair_dir / "rgb.jpg"
        depth_path = pair_dir / "depth.f32"
        if not (meta_path.exists() and rgb_path.exists() and depth_path.exists()):
            continue

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        rgb_w = int(meta["rgb"]["width"])
        rgb_h = int(meta["rgb"]["height"])
        depth_w = int(meta["depth"]["width"])
        depth_h = int(meta["depth"]["height"])

        rgb_img = cv2.imread(str(rgb_path), cv2.IMREAD_GRAYSCALE)
        if rgb_img is None:
            continue

        depth = _load_depth(depth_path, depth_w, depth_h)
        if depth is None:
            continue

        depth_edges = _depth_edges(depth)
        if np.count_nonzero(depth_edges) < 20:
            continue
        dt = cv2.distanceTransform(255 - depth_edges, cv2.DIST_L2, 3).astype(np.float32)

        rgb_pts = _rgb_edge_points(rgb_img, max_points=3000)
        if rgb_pts.shape[0] < 80:
            continue

        samples.append(
            FrameSample(
                rgb_w=rgb_w,
                rgb_h=rgb_h,
                depth_w=depth_w,
                depth_h=depth_h,
                rgb_points_xy=rgb_pts,
                depth_dt=dt,
            )
        )

    if not samples:
        raise RuntimeError("No valid samples for fitting")
    return samples


def eval_params(params: np.ndarray, samples: list[FrameSample]) -> float:
    a, b, c, d, e, f = params.tolist()
    total = 0.0
    used = 0

    s0 = samples[0]
    det = abs(a * e - b * d)
    projected_area = det * s0.rgb_w * s0.rgb_h
    area_ratio = projected_area / max(1.0, s0.depth_w * s0.depth_h)

    # Expect RGB footprint to be a subset of depth, not tiny and not full-frame blown up.
    if area_ratio < 0.10:
        total += (0.10 - area_ratio) * 80.0
    elif area_ratio > 1.20:
        total += (area_ratio - 1.20) * 80.0

    # Expect center of RGB to land somewhere near depth center region.
    cx_r = (s0.rgb_w - 1) * 0.5
    cy_r = (s0.rgb_h - 1) * 0.5
    cx_d = a * cx_r + b * cy_r + c
    cy_d = d * cx_r + e * cy_r + f
    total += (
        abs(cx_d - s0.depth_w * 0.5) / max(1.0, s0.depth_w)
        + abs(cy_d - s0.depth_h * 0.5) / max(1.0, s0.depth_h)
    ) * 6.0

    for s in samples:
        pts = s.rgb_points_xy
        xr = pts[:, 0]
        yr = pts[:, 1]

        xd = a * xr + b * yr + c
        yd = d * xr + e * yr + f
        xi = np.rint(xd).astype(np.int32)
        yi = np.rint(yd).astype(np.int32)

        inside = (xi >= 0) & (yi >= 0) & (xi < s.depth_w) & (yi < s.depth_h)
        if not np.any(inside):
            total += 200.0
            continue

        xi = xi[inside]
        yi = yi[inside]
        dist = s.depth_dt[yi, xi]
        coverage = float(np.count_nonzero(inside)) / float(len(inside))

        total += float(np.mean(dist)) + (1.0 - coverage) * 10.0
        used += 1

    if used == 0:
        return 1e9
    return total / used


def fit_affine_rgb_to_depth(samples: list[FrameSample], seed: int) -> np.ndarray:
    rnd = random.Random(seed)
    s0 = samples[0]

    # Initial guess: scale RGB down into depth domain and center it.
    sx0 = s0.depth_w / max(1.0, s0.rgb_w)
    sy0 = s0.depth_h / max(1.0, s0.rgb_h)
    x0 = (s0.depth_w - sx0 * s0.rgb_w) * 0.5
    y0 = (s0.depth_h - sy0 * s0.rgb_h) * 0.5

    best = np.array([sx0, 0.0, x0, 0.0, sy0, y0], dtype=np.float64)
    best_score = eval_params(best, samples)

    bounds = np.array(
        [
            [0.03, 0.45],  # a
            [-0.08, 0.08],  # b
            [-25.0, 130.0],  # c
            [-0.08, 0.08],  # d
            [0.03, 0.45],  # e
            [-25.0, 130.0],  # f
        ],
        dtype=np.float64,
    )

    for stage_scale, iters in [(1.0, 2000), (0.35, 2400), (0.12, 2800)]:
        for _ in range(iters):
            p = best.copy()
            p[0] += rnd.gauss(0.0, 0.03 * stage_scale)
            p[1] += rnd.gauss(0.0, 0.01 * stage_scale)
            p[2] += rnd.gauss(0.0, 5.0 * stage_scale)
            p[3] += rnd.gauss(0.0, 0.01 * stage_scale)
            p[4] += rnd.gauss(0.0, 0.03 * stage_scale)
            p[5] += rnd.gauss(0.0, 5.0 * stage_scale)

            for i in range(6):
                lo, hi = bounds[i]
                p[i] = min(hi, max(lo, p[i]))

            score = eval_params(p, samples)
            if score < best_score:
                best = p
                best_score = score

    return best


def build_luts(
    params: np.ndarray,
    rgb_w: int,
    rgb_h: int,
    depth_w: int,
    depth_h: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    a, b, c, d, e, f = params.tolist()

    gy_r, gx_r = np.meshgrid(
        np.arange(rgb_h, dtype=np.float32),
        np.arange(rgb_w, dtype=np.float32),
        indexing="ij",
    )
    map_rgb_to_depth_x = a * gx_r + b * gy_r + c
    map_rgb_to_depth_y = d * gx_r + e * gy_r + f
    valid_rgb_to_depth = (
        (map_rgb_to_depth_x >= 0.0)
        & (map_rgb_to_depth_x < depth_w)
        & (map_rgb_to_depth_y >= 0.0)
        & (map_rgb_to_depth_y < depth_h)
    )

    # Invert by nearest fill: depth -> rgb (valid only where RGB maps).
    map_depth_to_rgb_x = np.full((depth_h, depth_w), -1.0, dtype=np.float32)
    map_depth_to_rgb_y = np.full((depth_h, depth_w), -1.0, dtype=np.float32)
    dist_best = np.full((depth_h, depth_w), np.inf, dtype=np.float32)

    for ry in range(rgb_h):
        for rx in range(rgb_w):
            dx = map_rgb_to_depth_x[ry, rx]
            dy = map_rgb_to_depth_y[ry, rx]
            if dx < 0.0 or dy < 0.0 or dx >= depth_w or dy >= depth_h:
                continue
            ix = int(round(dx))
            iy = int(round(dy))
            if ix < 0 or iy < 0 or ix >= depth_w or iy >= depth_h:
                continue
            err = (dx - ix) * (dx - ix) + (dy - iy) * (dy - iy)
            if err < dist_best[iy, ix]:
                dist_best[iy, ix] = err
                map_depth_to_rgb_x[iy, ix] = float(rx)
                map_depth_to_rgb_y[iy, ix] = float(ry)

    valid_depth_to_rgb = (map_depth_to_rgb_x >= 0.0) & (map_depth_to_rgb_y >= 0.0)

    return (
        map_rgb_to_depth_x.astype(np.float32),
        map_rgb_to_depth_y.astype(np.float32),
        valid_rgb_to_depth.astype(np.uint8),
        map_depth_to_rgb_x,
        map_depth_to_rgb_y,
        valid_depth_to_rgb.astype(np.uint8),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fit RGB->Depth alignment and export bidirectional LUTs."
    )
    parser.add_argument("--capture-dir", default="backend/calib_capture")
    parser.add_argument("--out", default="backend/calib_capture/rgb_depth_lut.npz")
    parser.add_argument("--max-frames", type=int, default=150)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    capture_dir = Path(args.capture_dir).resolve()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    samples = load_samples(capture_dir, args.max_frames, args.seed)
    print(f"loaded samples: {len(samples)}")

    params = fit_affine_rgb_to_depth(samples, seed=args.seed)
    score = eval_params(params, samples)

    s0 = samples[0]
    (
        map_rgb_to_depth_x,
        map_rgb_to_depth_y,
        valid_rgb_to_depth,
        map_depth_to_rgb_x,
        map_depth_to_rgb_y,
        valid_depth_to_rgb,
    ) = build_luts(
        params,
        rgb_w=s0.rgb_w,
        rgb_h=s0.rgb_h,
        depth_w=s0.depth_w,
        depth_h=s0.depth_h,
    )

    np.savez_compressed(
        out_path,
        map_rgb_to_depth_x=map_rgb_to_depth_x,
        map_rgb_to_depth_y=map_rgb_to_depth_y,
        valid_rgb_to_depth=valid_rgb_to_depth,
        map_depth_to_rgb_x=map_depth_to_rgb_x,
        map_depth_to_rgb_y=map_depth_to_rgb_y,
        valid_depth_to_rgb=valid_depth_to_rgb,
        rgb_w=np.int32(s0.rgb_w),
        rgb_h=np.int32(s0.rgb_h),
        depth_w=np.int32(s0.depth_w),
        depth_h=np.int32(s0.depth_h),
        params_rgb_to_depth=params.astype(np.float32),
        score=np.float32(score),
    )

    report = {
        "capture_dir": str(capture_dir),
        "samples_used": len(samples),
        "score": float(score),
        "params_rgb_to_depth": {
            "a": float(params[0]),
            "b": float(params[1]),
            "c": float(params[2]),
            "d": float(params[3]),
            "e": float(params[4]),
            "f": float(params[5]),
        },
        "rgb_size": [int(s0.rgb_w), int(s0.rgb_h)],
        "depth_size": [int(s0.depth_w), int(s0.depth_h)],
        "valid_rgb_to_depth_ratio": float(np.mean(valid_rgb_to_depth)),
        "valid_depth_to_rgb_ratio": float(np.mean(valid_depth_to_rgb)),
        "lut_path": str(out_path),
    }

    report_path = out_path.with_suffix(".json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8"
    )

    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
