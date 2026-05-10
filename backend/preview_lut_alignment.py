from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def load_depth(depth_path: Path, w: int, h: int) -> np.ndarray:
    arr = np.fromfile(depth_path, dtype=np.float32)
    if arr.size != w * h:
        raise ValueError(f"depth size mismatch for {depth_path}")
    return arr.reshape((h, w))


def depth_to_color(depth: np.ndarray) -> np.ndarray:
    valid = np.isfinite(depth) & (depth > 0)
    canvas = np.zeros((depth.shape[0], depth.shape[1], 3), dtype=np.uint8)
    if np.count_nonzero(valid) == 0:
        return canvas

    vals = depth[valid]
    lo = float(np.percentile(vals, 5))
    hi = float(np.percentile(vals, 95))
    if hi <= lo:
        hi = lo + 1e-3

    norm = np.clip((depth - lo) / (hi - lo), 0.0, 1.0)
    gray = np.uint8((1.0 - norm) * 255.0)
    color = cv2.applyColorMap(np.ascontiguousarray(gray), cv2.COLORMAP_TURBO)
    canvas[valid] = color[valid]
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview RGB-in-Depth alignment")
    parser.add_argument("--capture-dir", default="backend/calib_capture")
    parser.add_argument("--lut", default="backend/calib_capture/rgb_depth_lut.npz")
    parser.add_argument("--out", default="backend/calib_capture/lut_preview")
    parser.add_argument("--count", type=int, default=24)
    args = parser.parse_args()

    capture_dir = Path(args.capture_dir).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    lut = np.load(Path(args.lut).resolve())
    params = lut["params_rgb_to_depth"].astype(np.float64)
    a, b, c, d, e, f = params.tolist()

    pair_dirs = sorted([p for p in capture_dir.glob("pair_*") if p.is_dir()])
    if args.count > 0 and len(pair_dirs) > args.count:
        idx = np.linspace(0, len(pair_dirs) - 1, args.count).astype(np.int32)
        pair_dirs = [pair_dirs[i] for i in idx]

    rendered = 0
    for pair in pair_dirs:
        meta = json.loads((pair / "meta.json").read_text(encoding="utf-8"))
        rgb = cv2.imread(str(pair / "rgb.jpg"), cv2.IMREAD_COLOR)
        if rgb is None:
            continue

        dw = int(meta["depth"]["width"])
        dh = int(meta["depth"]["height"])
        depth = load_depth(pair / "depth.f32", dw, dh)
        depth_canvas = depth_to_color(depth)

        warp_m = np.array([[a, b, c], [d, e, f]], dtype=np.float32)
        rgb_in_depth = cv2.warpAffine(
            rgb,
            warp_m,
            (dw, dh),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )

        valid_mask = (
            (rgb_in_depth[:, :, 0] > 0)
            | (rgb_in_depth[:, :, 1] > 0)
            | (rgb_in_depth[:, :, 2] > 0)
        )
        overlay = depth_canvas.copy()
        alpha = 0.55
        overlay[valid_mask] = np.uint8(
            (1.0 - alpha) * overlay[valid_mask] + alpha * rgb_in_depth[valid_mask]
        )

        # Draw projected RGB bounding polygon on depth canvas.
        h, w = rgb.shape[:2]
        corners = np.array(
            [[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32
        )
        poly = np.zeros((4, 2), dtype=np.int32)
        for i, (x, y) in enumerate(corners):
            px = int(round(a * x + b * y + c))
            py = int(round(d * x + e * y + f))
            poly[i] = [px, py]

        cv2.polylines(overlay, [poly], isClosed=True, color=(0, 255, 255), thickness=1)

        # Side-by-side view for inspection.
        rgb_small = cv2.resize(rgb, (dw, dh), interpolation=cv2.INTER_LINEAR)
        panel = np.concatenate([rgb_small, depth_canvas, overlay], axis=1)
        cv2.imwrite(str(out_dir / f"{pair.name}.png"), panel)
        rendered += 1

    print(f"rendered previews: {rendered} -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
