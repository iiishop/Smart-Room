from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


@dataclass(slots=True)
class FinalRgbdAlignment:
    """Runtime output from Quest3RgbdCaptureFinal alignment."""

    rgb_bgr: np.ndarray
    rgb_rgb: np.ndarray
    aligned_depth_m: np.ndarray
    valid_mask: np.ndarray
    point_cloud_rgb_camera_m: np.ndarray
    point_colors_rgb: np.ndarray
    rgb_intrinsics: np.ndarray
    summary: dict[str, Any]


def decode_rgb_jpeg(jpeg_bytes: bytes) -> np.ndarray:
    bgr = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("failed to decode rgb_jpeg_b64")
    return bgr


def rgb_intrinsics_from_meta(rgb_meta: dict[str, Any]) -> np.ndarray:
    return np.array(
        [
            [float(rgb_meta["focal_length_x"]), 0.0, float(rgb_meta["principal_point_x"])],
            [0.0, float(rgb_meta["focal_length_y"]), float(rgb_meta["principal_point_y"])],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float32,
    )


def load_final_capture_meta(capture_dir: str | Path) -> dict[str, Any]:
    path = Path(capture_dir) / "meta.json"
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_final_capture_rgb(capture_dir: str | Path) -> np.ndarray:
    path = Path(capture_dir) / "rgb.jpg"
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(path)
    return bgr


def load_final_capture_depth_raw(
    capture_dir: str | Path,
    width: int,
    height: int,
) -> np.ndarray:
    path = Path(capture_dir) / "depth.raw"
    raw = np.fromfile(path, dtype="<f4")
    expected = width * height
    if raw.size != expected:
        raise ValueError(f"{path} has {raw.size} floats, expected {expected}")
    return raw.reshape(height, width)


def align_final_rgbd_capture_dir(
    capture_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    min_depth: float = 0.2,
    max_depth: float = 8.0,
    write_outputs: bool = True,
) -> FinalRgbdAlignment:
    capture_dir = Path(capture_dir)
    meta = load_final_capture_meta(capture_dir)
    depth_meta = meta["depth"]
    depth_w = int(depth_meta["resolution_w"])
    depth_h = int(depth_meta["resolution_h"])
    rgb_bgr = load_final_capture_rgb(capture_dir)
    raw_depth = load_final_capture_depth_raw(capture_dir, depth_w, depth_h)

    alignment = align_final_rgbd_payload(
        rgb_bgr=rgb_bgr,
        raw_depth=raw_depth,
        meta=meta,
        min_depth=min_depth,
        max_depth=max_depth,
    )
    alignment.summary["capture_dir"] = str(capture_dir)

    if write_outputs:
        out = Path(output_dir) if output_dir is not None else capture_dir / "aligned"
        alignment.summary["output_dir"] = str(out)
        write_final_alignment_outputs(alignment, out, min_depth=min_depth, max_depth=max_depth)

    return alignment


def align_final_rgbd_payload(
    *,
    rgb_bgr: np.ndarray,
    raw_depth: np.ndarray,
    meta: dict[str, Any],
    min_depth: float = 0.2,
    max_depth: float = 8.0,
) -> FinalRgbdAlignment:
    """Align Quest3RgbdCaptureFinal rgb/depth/meta payload in memory.

    This is the backend-runtime mirror of backend/tools/quest3_rgbd_align_final.py.
    Keep the projection math in sync with that script.
    """

    if rgb_bgr is None or rgb_bgr.ndim != 3:
        raise ValueError("rgb_bgr must be a color image")
    rgb_rgb = cv2.cvtColor(rgb_bgr, cv2.COLOR_BGR2RGB)
    rgb_h, rgb_w = rgb_rgb.shape[:2]

    rgb_meta = meta["rgb"]
    depth_meta = meta["depth"]
    depth_w = int(depth_meta["resolution_w"])
    depth_h = int(depth_meta["resolution_h"])
    raw = np.asarray(raw_depth, dtype=np.float32)
    if raw.shape != (depth_h, depth_w):
        raise ValueError(f"raw_depth shape {raw.shape} does not match meta depth {depth_h}x{depth_w}")

    depth_m = raw_depth_to_linear_m(raw, depth_meta)
    valid = np.isfinite(depth_m) & (depth_m >= min_depth) & (depth_m <= max_depth)

    points_world = unproject_depth_to_world(depth_m, depth_meta, valid)
    points_rgb = world_to_rgb_camera(points_world, rgb_meta)
    if points_rgb.size:
        points_rgb = points_rgb[points_rgb[:, 2] > 0.01]

    if points_rgb.size:
        u, v = project_rgb(points_rgb, rgb_meta)
        in_bounds = (u >= 0) & (u < rgb_w) & (v >= 0) & (v < rgb_h)
        points_rgb = points_rgb[in_bounds]
        u = u[in_bounds]
        v = v[in_bounds]
    else:
        u = np.empty((0,), dtype=np.int32)
        v = np.empty((0,), dtype=np.int32)

    aligned_depth = np.full((rgb_h, rgb_w), np.inf, dtype=np.float32)
    if u.size:
        np.minimum.at(aligned_depth, (v, u), points_rgb[:, 2].astype(np.float32))
    aligned_depth[~np.isfinite(aligned_depth)] = 0.0
    valid_mask = (aligned_depth > 0).astype(np.uint8)
    point_colors = rgb_rgb[v, u].astype(np.uint8) if u.size else np.empty((0, 3), dtype=np.uint8)

    projected = int(np.count_nonzero(valid_mask))
    if projected == 0:
        logger.warning(
            "align_final_rgbd_payload: zero projected pixels. "
            "valid_depth_samples=%d, world_points=%d, rgb_points_in_front=%d, "
            "in_bounds=%d",
            int(np.count_nonzero(valid)),
            int(np.count_nonzero(valid)),
            int(points_rgb.shape[0]),
            int(u.size) if u.size else 0,
        )

    intrinsics = rgb_intrinsics_from_meta(rgb_meta)
    summary = {
        "source": "quest3_rgbd_capture_final",
        "alignment": "quest3_rgbd_align_final_runtime",
        "rgb_resolution": [int(rgb_w), int(rgb_h)],
        "depth_resolution": [int(depth_w), int(depth_h)],
        "valid_depth_samples": int(np.count_nonzero(valid)),
        "projected_pixels": int(np.count_nonzero(valid_mask)),
        "valid_ratio": float(np.count_nonzero(valid_mask) / max(rgb_w * rgb_h, 1)),
        "point_cloud_points": int(points_rgb.shape[0]),
        "depth_units": "metres",
        "point_cloud_coordinates": "RGB camera space: x right, y up, z forward, units metres",
        "selected_depth_eye": depth_meta.get("selected_eye"),
        "rgb_camera_position": rgb_meta.get("camera_position"),
    }

    return FinalRgbdAlignment(
        rgb_bgr=rgb_bgr,
        rgb_rgb=rgb_rgb,
        aligned_depth_m=aligned_depth,
        valid_mask=valid_mask,
        point_cloud_rgb_camera_m=points_rgb.astype(np.float32),
        point_colors_rgb=point_colors,
        rgb_intrinsics=intrinsics,
        summary=summary,
    )


def write_final_alignment_outputs(
    alignment: FinalRgbdAlignment,
    output_dir: str | Path,
    *,
    min_depth: float = 0.2,
    max_depth: float = 8.0,
) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rgb = alignment.rgb_rgb
    aligned_depth = alignment.aligned_depth_m
    points_rgb = alignment.point_cloud_rgb_camera_m
    point_colors = alignment.point_colors_rgb

    overlay = make_overlay(rgb, aligned_depth, min_depth, max_depth)
    np.save(output_dir / "aligned_depth_m.npy", aligned_depth)
    np.save(output_dir / "point_cloud_rgb_camera_m.npy", points_rgb)
    cv2.imwrite(str(output_dir / "aligned_overlay.png"), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))

    depth_vis = np.zeros_like(aligned_depth, dtype=np.uint8)
    valid = aligned_depth > 0
    if valid.any():
        lo = max(min_depth, float(np.percentile(aligned_depth[valid], 1)))
        hi = min(max_depth, float(np.percentile(aligned_depth[valid], 99)))
        depth_vis[valid] = np.clip(
            (aligned_depth[valid] - lo) / max(hi - lo, 1e-6) * 255,
            0,
            255,
        ).astype(np.uint8)
    depth_png = cv2.applyColorMap(depth_vis, cv2.COLORMAP_TURBO)
    depth_png[~valid] = (0, 0, 0)
    cv2.imwrite(str(output_dir / "aligned_depth_colormap.png"), depth_png)
    write_ascii_ply(output_dir / "point_cloud_rgb_camera.ply", points_rgb, point_colors)
    (output_dir / "summary.json").write_text(
        json.dumps(alignment.summary, indent=2),
        encoding="utf-8",
    )


def quaternion_to_matrix(q_xyzw: np.ndarray) -> np.ndarray:
    q = q_xyzw.astype(np.float64)
    norm = np.linalg.norm(q)
    if norm == 0:
        return np.eye(3, dtype=np.float32)
    x, y, z, w = q / norm
    return np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
            [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
            [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
        ],
        dtype=np.float32,
    )


def projection_from_depth_fov(depth_meta: dict[str, Any]) -> np.ndarray:
    left = float(depth_meta["fov_left"])
    right = float(depth_meta["fov_right"])
    top = float(depth_meta["fov_top"])
    bottom = float(depth_meta["fov_bottom"])
    near = float(depth_meta["near_z"])
    far = float(depth_meta.get("far_z", math.inf))

    x = 2.0 / (right + left)
    y = 2.0 / (top + bottom)
    a = (right - left) / (right + left)
    b = (top - bottom) / (top + bottom)
    if math.isinf(far) or far < near:
        c = -1.0
        d = -2.0 * near
    else:
        c = -(far + near) / (far - near)
        d = -(2.0 * far * near) / (far - near)

    return np.array(
        [[x, 0.0, a, 0.0], [0.0, y, b, 0.0], [0.0, 0.0, c, d], [0.0, 0.0, -1.0, 0.0]],
        dtype=np.float32,
    )


def unity_trs(
    position: np.ndarray,
    rotation: np.ndarray,
    scale: tuple[float, float, float],
) -> np.ndarray:
    mat = np.eye(4, dtype=np.float32)
    mat[:3, :3] = rotation @ np.diag(np.array(scale, dtype=np.float32))
    mat[:3, 3] = position
    return mat


def matrix_from_meta(depth_meta: dict[str, Any]) -> np.ndarray:
    for field in ("descriptor_reprojection_matrix", "reprojection_matrix"):
        values = depth_meta.get(field)
        if isinstance(values, list) and len(values) == 16:
            return np.array(values, dtype=np.float32).reshape(4, 4)

    pos = np.array(
        [
            float(depth_meta["pose_position_x"]),
            float(depth_meta["pose_position_y"]),
            float(depth_meta["pose_position_z"]),
        ],
        dtype=np.float32,
    )
    quat = np.array(
        [
            float(depth_meta["pose_rotation_x"]),
            float(depth_meta["pose_rotation_y"]),
            float(depth_meta["pose_rotation_z"]),
            float(depth_meta["pose_rotation_w"]),
        ],
        dtype=np.float32,
    )
    projection = projection_from_depth_fov(depth_meta)
    view = np.linalg.inv(unity_trs(pos, quaternion_to_matrix(quat), (1.0, 1.0, -1.0)))
    return projection @ view


def raw_depth_to_linear_m(raw_depth: np.ndarray, depth_meta: dict[str, Any]) -> np.ndarray:
    zbuffer_x = float(depth_meta["zbuffer_x"])
    zbuffer_y = float(depth_meta["zbuffer_y"])
    ndc = raw_depth.astype(np.float32) * 2.0 - 1.0
    return np.divide(
        np.float32(zbuffer_x),
        ndc + np.float32(zbuffer_y),
        out=np.zeros_like(raw_depth, dtype=np.float32),
        where=np.abs(ndc + np.float32(zbuffer_y)) > 1e-8,
    )


def unproject_depth_to_world(
    depth_m: np.ndarray,
    depth_meta: dict[str, Any],
    valid: np.ndarray,
) -> np.ndarray:
    ys, xs = np.where(valid)
    if xs.size == 0:
        return np.empty((0, 3), dtype=np.float32)

    h, w = depth_m.shape
    z = depth_m[ys, xs].astype(np.float32)
    zbuffer_x = float(depth_meta["zbuffer_x"])
    zbuffer_y = float(depth_meta["zbuffer_y"])
    ndc_x = (xs.astype(np.float32) + 0.5) / max(w, 1) * 2.0 - 1.0
    ndc_y = (ys.astype(np.float32) + 0.5) / max(h, 1) * 2.0 - 1.0
    ndc_z = zbuffer_x / np.maximum(z, 1e-6) - zbuffer_y

    clip = np.stack([ndc_x, ndc_y, ndc_z, np.ones_like(ndc_z)], axis=0)
    depth_to_world = np.linalg.inv(matrix_from_meta(depth_meta))
    world_h = depth_to_world @ clip
    world_w = np.where(np.abs(world_h[3]) < 1e-8, 1e-8, world_h[3])
    return (world_h[:3] / world_w).T.astype(np.float32)


def world_to_rgb_camera(points_world: np.ndarray, rgb_meta: dict[str, Any]) -> np.ndarray:
    if points_world.size == 0:
        return np.empty((0, 3), dtype=np.float32)
    pos = np.array(
        [
            float(rgb_meta["pose_position_x"]),
            float(rgb_meta["pose_position_y"]),
            float(rgb_meta["pose_position_z"]),
        ],
        dtype=np.float32,
    )
    quat = np.array(
        [
            float(rgb_meta["pose_rotation_x"]),
            float(rgb_meta["pose_rotation_y"]),
            float(rgb_meta["pose_rotation_z"]),
            float(rgb_meta["pose_rotation_w"]),
        ],
        dtype=np.float32,
    )
    rot = quaternion_to_matrix(quat)
    return (points_world - pos[None, :]) @ rot


def project_rgb(points_rgb: np.ndarray, rgb_meta: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    fx = float(rgb_meta["focal_length_x"])
    fy = float(rgb_meta["focal_length_y"])
    cx = float(rgb_meta["principal_point_x"])
    cy = float(rgb_meta["principal_point_y"])
    height = int(rgb_meta["resolution_h"])
    z = np.maximum(points_rgb[:, 2], 1e-6)
    u = points_rgb[:, 0] * fx / z + cx
    sensor_y = points_rgb[:, 1] * fy / z + cy
    v = (height - 1) - sensor_y
    return np.rint(u).astype(np.int32), np.rint(v).astype(np.int32)


def depth_colors(depth_values: np.ndarray, min_depth: float, max_depth: float) -> np.ndarray:
    norm = np.clip((depth_values - min_depth) / max(max_depth - min_depth, 1e-6), 0.0, 1.0)
    colors = np.zeros((depth_values.shape[0], 3), dtype=np.uint8)
    colors[:, 0] = (255.0 * (1.0 - norm)).astype(np.uint8)
    colors[:, 1] = (70.0 * (1.0 - np.abs(norm - 0.5) * 2.0)).astype(np.uint8)
    colors[:, 2] = (255.0 * norm).astype(np.uint8)
    return colors


def make_overlay(
    rgb: np.ndarray,
    aligned_depth: np.ndarray,
    min_depth: float,
    max_depth: float,
) -> np.ndarray:
    yy, xx = np.where(aligned_depth > 0)
    if xx.size == 0:
        return rgb.copy()
    depths = aligned_depth[yy, xx]
    lo = max(min_depth, float(np.percentile(depths, 1)))
    hi = min(max_depth, float(np.percentile(depths, 99)))
    colors = depth_colors(depths, lo, hi)
    overlay = rgb.copy()
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            if dx * dx + dy * dy > 4:
                continue
            y2 = yy + dy
            x2 = xx + dx
            inside = (x2 >= 0) & (x2 < aligned_depth.shape[1]) & (y2 >= 0) & (y2 < aligned_depth.shape[0])
            overlay[y2[inside], x2[inside]] = colors[inside]
    return cv2.addWeighted(overlay, 0.38, rgb, 0.62, 0.0)


def write_ascii_ply(path: Path, points: np.ndarray, colors: np.ndarray) -> None:
    with path.open("w", encoding="ascii", newline="\n") as f:
        f.write("ply\nformat ascii 1.0\n")
        f.write("comment coordinates are RGB camera space: x right, y up, z forward, units metres\n")
        f.write(f"element vertex {points.shape[0]}\n")
        f.write("property float x\nproperty float y\nproperty float z\n")
        f.write("property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
        for p, c in zip(points, colors, strict=False):
            f.write(f"{p[0]:.6f} {p[1]:.6f} {p[2]:.6f} {int(c[0])} {int(c[1])} {int(c[2])}\n")
