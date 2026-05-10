from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def load_pair(pair_dir: Path):
    meta = json.loads((pair_dir / "meta.json").read_text(encoding="utf-8"))

    rgb = cv2.imread(str(pair_dir / "rgb.jpg"), cv2.IMREAD_COLOR)
    if rgb is None:
        raise RuntimeError(f"failed to load rgb image: {pair_dir / 'rgb.jpg'}")

    dw = int(meta["depth"]["width"])
    dh = int(meta["depth"]["height"])
    depth = np.fromfile(pair_dir / "depth.f32", dtype=np.float32)
    if depth.size != dw * dh:
        raise RuntimeError("depth size mismatch")
    depth = depth.reshape((dh, dw))

    depth_vis = depth_to_color(depth)
    return rgb, depth, depth_vis, meta


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


class PointCollector:
    def __init__(self, window_name: str, base_image: np.ndarray):
        self.window_name = window_name
        self.base_image = base_image
        self.points: list[tuple[int, int]] = []

    def on_mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.points.append((int(x), int(y)))
        elif event == cv2.EVENT_RBUTTONDOWN and self.points:
            self.points.pop()

    def run(self, target_count: int) -> list[tuple[int, int]]:
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.window_name, self.on_mouse)

        while True:
            frame = self.base_image.copy()
            for i, (x, y) in enumerate(self.points, start=1):
                cv2.circle(frame, (x, y), 4, (0, 255, 255), -1)
                cv2.putText(
                    frame,
                    str(i),
                    (x + 6, y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (255, 255, 255),
                    1,
                    cv2.LINE_AA,
                )

            msg = f"L-click add, R-click undo, ENTER confirm >= {target_count}, ESC cancel | points={len(self.points)}"
            cv2.putText(
                frame,
                msg,
                (10, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

            cv2.imshow(self.window_name, frame)
            key = cv2.waitKey(20) & 0xFF
            if key in (13, 10):
                if len(self.points) >= target_count:
                    break
            elif key == 27:
                raise RuntimeError("point selection cancelled")

        cv2.destroyWindow(self.window_name)
        return self.points


def build_lut_from_homography(
    h_depth_to_rgb: np.ndarray,
    rgb_w: int,
    rgb_h: int,
    depth_w: int,
    depth_h: int,
):
    grid_y_d, grid_x_d = np.meshgrid(
        np.arange(depth_h, dtype=np.float32),
        np.arange(depth_w, dtype=np.float32),
        indexing="ij",
    )
    depth_pts = np.stack([grid_x_d, grid_y_d], axis=-1).reshape((-1, 1, 2))
    rgb_pts = cv2.perspectiveTransform(depth_pts, h_depth_to_rgb).reshape(
        (depth_h, depth_w, 2)
    )

    map_depth_to_rgb_x = rgb_pts[:, :, 0].astype(np.float32)
    map_depth_to_rgb_y = rgb_pts[:, :, 1].astype(np.float32)
    valid_depth_to_rgb = (
        (map_depth_to_rgb_x >= 0.0)
        & (map_depth_to_rgb_x < rgb_w)
        & (map_depth_to_rgb_y >= 0.0)
        & (map_depth_to_rgb_y < rgb_h)
    ).astype(np.uint8)

    h_rgb_to_depth = np.linalg.inv(h_depth_to_rgb)
    grid_y_r, grid_x_r = np.meshgrid(
        np.arange(rgb_h, dtype=np.float32),
        np.arange(rgb_w, dtype=np.float32),
        indexing="ij",
    )
    rgb_grid = np.stack([grid_x_r, grid_y_r], axis=-1).reshape((-1, 1, 2))
    depth_from_rgb = cv2.perspectiveTransform(rgb_grid, h_rgb_to_depth).reshape(
        (rgb_h, rgb_w, 2)
    )

    map_rgb_to_depth_x = depth_from_rgb[:, :, 0].astype(np.float32)
    map_rgb_to_depth_y = depth_from_rgb[:, :, 1].astype(np.float32)
    valid_rgb_to_depth = (
        (map_rgb_to_depth_x >= 0.0)
        & (map_rgb_to_depth_x < depth_w)
        & (map_rgb_to_depth_y >= 0.0)
        & (map_rgb_to_depth_y < depth_h)
    ).astype(np.uint8)

    return (
        map_depth_to_rgb_x,
        map_depth_to_rgb_y,
        valid_depth_to_rgb,
        map_rgb_to_depth_x,
        map_rgb_to_depth_y,
        valid_rgb_to_depth,
        h_rgb_to_depth,
    )


def render_preview(
    rgb: np.ndarray,
    depth_vis: np.ndarray,
    map_depth_to_rgb_x: np.ndarray,
    map_depth_to_rgb_y: np.ndarray,
    valid_depth_to_rgb: np.ndarray,
) -> np.ndarray:
    overlay = rgb.copy()
    alpha = 0.45
    h, w = valid_depth_to_rgb.shape
    for y in range(h):
        for x in range(w):
            if valid_depth_to_rgb[y, x] == 0:
                continue
            rx = int(round(float(map_depth_to_rgb_x[y, x])))
            ry = int(round(float(map_depth_to_rgb_y[y, x])))
            if rx < 0 or ry < 0 or rx >= overlay.shape[1] or ry >= overlay.shape[0]:
                continue
            c = depth_vis[y, x]
            overlay[ry, rx] = np.uint8((1.0 - alpha) * overlay[ry, rx] + alpha * c)
    return overlay


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual plane alignment: depth -> rgb")
    parser.add_argument("--pair-dir", default="backend/calib_capture/pair_000120")
    parser.add_argument("--out", default="backend/calib_capture/manual_plane_lut.npz")
    parser.add_argument("--min-points", type=int, default=8)
    args = parser.parse_args()

    pair_dir = Path(args.pair_dir).resolve()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rgb, depth, depth_vis, meta = load_pair(pair_dir)
    rgb_h, rgb_w = rgb.shape[:2]
    depth_h, depth_w = depth.shape[:2]

    print("Select points on DEPTH image first, then RGB image in exactly same order.")
    depth_pts = PointCollector("Select DEPTH points", depth_vis).run(args.min_points)
    rgb_pts = PointCollector("Select RGB points", rgb).run(len(depth_pts))

    if len(depth_pts) < 4 or len(rgb_pts) < 4:
        raise RuntimeError("need at least 4 matched points")

    if len(depth_pts) != len(rgb_pts):
        n = min(len(depth_pts), len(rgb_pts))
        print(
            f"warning: point count mismatch depth={len(depth_pts)} rgb={len(rgb_pts)}; using first {n}"
        )
        depth_pts = depth_pts[:n]
        rgb_pts = rgb_pts[:n]

    depth_np = np.array(depth_pts, dtype=np.float32)
    rgb_np = np.array(rgb_pts, dtype=np.float32)

    h_depth_to_rgb, inliers = cv2.findHomography(
        depth_np, rgb_np, cv2.RANSAC, ransacReprojThreshold=3.0
    )
    if h_depth_to_rgb is None:
        raise RuntimeError("findHomography failed")

    (
        map_depth_to_rgb_x,
        map_depth_to_rgb_y,
        valid_depth_to_rgb,
        map_rgb_to_depth_x,
        map_rgb_to_depth_y,
        valid_rgb_to_depth,
        h_rgb_to_depth,
    ) = build_lut_from_homography(
        h_depth_to_rgb,
        rgb_w=rgb_w,
        rgb_h=rgb_h,
        depth_w=depth_w,
        depth_h=depth_h,
    )

    np.savez_compressed(
        out_path,
        method=np.array("manual_plane_homography"),
        map_depth_to_rgb_x=map_depth_to_rgb_x,
        map_depth_to_rgb_y=map_depth_to_rgb_y,
        valid_depth_to_rgb=valid_depth_to_rgb,
        map_rgb_to_depth_x=map_rgb_to_depth_x,
        map_rgb_to_depth_y=map_rgb_to_depth_y,
        valid_rgb_to_depth=valid_rgb_to_depth,
        H_depth_to_rgb=h_depth_to_rgb.astype(np.float32),
        H_rgb_to_depth=h_rgb_to_depth.astype(np.float32),
        depth_w=np.int32(depth_w),
        depth_h=np.int32(depth_h),
        rgb_w=np.int32(rgb_w),
        rgb_h=np.int32(rgb_h),
    )

    report = {
        "pair_dir": str(pair_dir),
        "points_used": int(len(depth_pts)),
        "inliers": int(np.count_nonzero(inliers)) if inliers is not None else None,
        "rgb_size": [rgb_w, rgb_h],
        "depth_size": [depth_w, depth_h],
        "valid_depth_to_rgb_ratio": float(np.mean(valid_depth_to_rgb)),
        "valid_rgb_to_depth_ratio": float(np.mean(valid_rgb_to_depth)),
        "lut_path": str(out_path),
    }
    out_json = out_path.with_suffix(".json")
    out_json.write_text(
        json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8"
    )

    preview = render_preview(
        rgb=rgb,
        depth_vis=depth_vis,
        map_depth_to_rgb_x=map_depth_to_rgb_x,
        map_depth_to_rgb_y=map_depth_to_rgb_y,
        valid_depth_to_rgb=valid_depth_to_rgb,
    )
    preview_path = out_path.with_name(out_path.stem + "_preview.png")
    cv2.imwrite(str(preview_path), preview)

    print(json.dumps(report, ensure_ascii=True, indent=2))
    print(f"preview saved: {preview_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
