from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk


DEFAULT_DATA_DIR = Path("E:/test/rgbd-v10/rgbd_test")


@dataclass
class FrameData:
    frame_dir: Path
    meta: dict
    rgb: np.ndarray
    depth_ndc: np.ndarray
    depth_m: np.ndarray
    aligned_depth: np.ndarray
    overlay_rgb: np.ndarray
    any2full_depth: np.ndarray | None
    any2full_overlay_rgb: np.ndarray | None
    any2full_path: Path | None
    cloud_points: np.ndarray
    cloud_colors: np.ndarray
    projected_depth_count: int
    any2full_depth_count: int
    alignment_mode: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quest 3 RGB-D alignment viewer for DepthPoseSaturationTest captures.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Capture root containing capture_0000.. directories. Default: {DEFAULT_DATA_DIR}",
    )
    parser.add_argument("--frame", type=int, default=0, help="Initial capture index.")
    parser.add_argument("--min-depth", type=float, default=0.2, help="Minimum valid depth in metres.")
    parser.add_argument("--max-depth", type=float, default=8.0, help="Maximum valid depth in metres.")
    parser.add_argument("--view-size", type=int, default=960, help="Square RGB display size in pixels.")
    parser.add_argument("--cloud-width", type=int, default=980, help="Point-cloud canvas width.")
    parser.add_argument("--cloud-height", type=int, default=760, help="Point-cloud canvas height.")
    parser.add_argument(
        "--mode",
        choices=("sdk_reprojection", "legacy_pinhole", "screen_space"),
        default="sdk_reprojection",
        help="Alignment model. sdk_reprojection reproduces Meta EnvironmentDepthUtils.",
    )
    parser.add_argument(
        "--depth-origin",
        choices=("raw", "flip_y"),
        default="raw",
        help="Depth row order. Use flip_y if the saved RenderTexture readback is vertically inverted.",
    )
    parser.add_argument("--no-ui", action="store_true", help="Load and align all frames, then print stats.")
    parser.add_argument("--export-dir", type=Path, default=None, help="Optional directory for overlay PNG exports.")
    parser.add_argument("--export-ply-dir", type=Path, default=None, help="Optional directory for RGB-colored PLY point-cloud exports.")
    parser.add_argument(
        "--preview-depth-source",
        choices=("auto", "sparse", "any2full"),
        default="auto",
        help="RGB-depth preview source. auto prefers Any2Full when available, otherwise sparse aligned depth.",
    )
    parser.add_argument(
        "--origin-anchor",
        choices=("bottom_right", "bottom_left", "top_left", "top_right", "center", "custom"),
        default="bottom_right",
        help="Initial x/y origin anchor for reported viewer coordinates.",
    )
    parser.add_argument("--origin-x", type=int, default=None, help="Custom x-origin in RGB pixel coordinates.")
    parser.add_argument("--origin-y", type=int, default=None, help="Custom y-origin in RGB pixel coordinates.")
    return parser.parse_args()


def load_meta(frame_dir: Path) -> dict:
    with (frame_dir / "meta.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def load_rgb(frame_dir: Path) -> np.ndarray:
    bgr = cv2.imread(str(frame_dir / "rgb.jpg"), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(frame_dir / "rgb.jpg")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def read_depth_ndc(frame_dir: Path, width: int, height: int) -> np.ndarray:
    depth_path = frame_dir / "depth.raw"
    raw = np.fromfile(depth_path, dtype=np.float32)
    expected = width * height
    if raw.size != expected:
        raise ValueError(f"{depth_path} has {raw.size} floats, expected {expected}")
    return raw.reshape(height, width)


def ndc_to_linear_depth(depth_ndc: np.ndarray, near: float, far: float) -> np.ndarray:
    if math.isinf(far) or far < near:
        x, y = -2.0 * near, -1.0
    else:
        x = -2.0 * far * near / (far - near)
        y = -(far + near) / (far - near)
    ndc = depth_ndc.astype(np.float32) * 2.0 - 1.0
    denom = ndc + y
    return np.divide(
        np.float32(x),
        denom,
        out=np.zeros_like(depth_ndc, dtype=np.float32),
        where=np.abs(denom) > 1e-8,
    )


def depth_intrinsics_from_fov(depth_meta: dict) -> np.ndarray:
    width = float(depth_meta["resolution_w"])
    height = float(depth_meta["resolution_h"])
    left = float(depth_meta["fov_left"])
    right = float(depth_meta["fov_right"])
    top = float(depth_meta["fov_top"])
    bottom = float(depth_meta["fov_bottom"])
    fx = width / (left + right)
    fy = height / (top + bottom)
    cx = width * right / (left + right)
    cy = height * top / (top + bottom)
    return np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]], dtype=np.float32)


def rgb_intrinsics(rgb_meta: dict) -> np.ndarray:
    return np.array(
        [
            [float(rgb_meta["focal_length_x"]), 0.0, float(rgb_meta["principal_point_x"])],
            [0.0, float(rgb_meta["focal_length_y"]), float(rgb_meta["principal_point_y"])],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float32,
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


def pose_matrix(section: dict, prefix: str) -> np.ndarray:
    pos = np.array(
        [
            float(section[f"{prefix}_position_x"]),
            float(section[f"{prefix}_position_y"]),
            float(section[f"{prefix}_position_z"]),
        ],
        dtype=np.float32,
    )
    quat = np.array(
        [
            float(section[f"{prefix}_rotation_x"]),
            float(section[f"{prefix}_rotation_y"]),
            float(section[f"{prefix}_rotation_z"]),
            float(section[f"{prefix}_rotation_w"]),
        ],
        dtype=np.float32,
    )
    mat = np.eye(4, dtype=np.float32)
    mat[:3, :3] = quaternion_to_matrix(quat)
    mat[:3, 3] = pos
    return mat


def depth_to_rgb_transform(meta: dict) -> np.ndarray:
    world_rgb = pose_matrix(meta["rgb"], "pose")
    world_depth = pose_matrix(meta["depth"], "pose")
    return np.linalg.inv(world_rgb) @ world_depth


def projection_from_depth_fov(depth_meta: dict) -> np.ndarray:
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
        [
            [x, 0.0, a, 0.0],
            [0.0, y, b, 0.0],
            [0.0, 0.0, c, d],
            [0.0, 0.0, -1.0, 0.0],
        ],
        dtype=np.float32,
    )


def unity_trs(position: np.ndarray, rotation: np.ndarray, scale: tuple[float, float, float]) -> np.ndarray:
    mat = np.eye(4, dtype=np.float32)
    mat[:3, :3] = rotation @ np.diag(np.array(scale, dtype=np.float32))
    mat[:3, 3] = position
    return mat


def matrix_from_meta_field(section: dict, field_name: str) -> np.ndarray | None:
    values = section.get(field_name)
    if not isinstance(values, list) or len(values) != 16:
        return None
    return np.array(values, dtype=np.float32).reshape(4, 4)


def depth_reprojection_matrix(depth_meta: dict) -> np.ndarray:
    # Prefer the exact matrix saved by the capture script. The descriptor matrix
    # matches Meta's CalculateReprojection(frameDesc) and stays in the same
    # tracking-local coordinate space as PassthroughCameraAccess.GetCameraPose().
    for field_name in ("descriptor_reprojection_matrix", "reprojection_matrix"):
        saved = matrix_from_meta_field(depth_meta, field_name)
        if saved is not None:
            return saved

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
    depth_camera_to_world = unity_trs(pos, quaternion_to_matrix(quat), (1.0, 1.0, -1.0))
    view = np.linalg.inv(depth_camera_to_world)
    return projection @ view


def unproject_environment_depth(
    depth_m: np.ndarray,
    depth_meta: dict,
    valid: np.ndarray,
) -> np.ndarray:
    ys, xs = np.where(valid)
    if xs.size == 0:
        return np.empty((0, 3), dtype=np.float32)

    h, w = depth_m.shape
    z = depth_m[ys, xs].astype(np.float32)
    zbp_x = float(depth_meta["zbuffer_x"])
    zbp_y = float(depth_meta["zbuffer_y"])
    ndc_x = (xs.astype(np.float32) + 0.5) / max(w, 1) * 2.0 - 1.0
    ndc_y = (ys.astype(np.float32) + 0.5) / max(h, 1) * 2.0 - 1.0
    ndc_z = zbp_x / np.maximum(z, 1e-6) - zbp_y
    clip = np.stack([ndc_x, ndc_y, ndc_z, np.ones_like(ndc_z)], axis=0)
    depth_to_world = np.linalg.inv(depth_reprojection_matrix(depth_meta))
    world_h = depth_to_world @ clip
    world_w = np.where(np.abs(world_h[3]) < 1e-8, 1e-8, world_h[3])
    return (world_h[:3] / world_w).T.astype(np.float32)


def world_to_rgb_camera(points_world: np.ndarray, rgb_meta: dict) -> np.ndarray:
    rgb_pos = np.array(
        [
            float(rgb_meta["pose_position_x"]),
            float(rgb_meta["pose_position_y"]),
            float(rgb_meta["pose_position_z"]),
        ],
        dtype=np.float32,
    )
    rgb_quat = np.array(
        [
            float(rgb_meta["pose_rotation_x"]),
            float(rgb_meta["pose_rotation_y"]),
            float(rgb_meta["pose_rotation_z"]),
            float(rgb_meta["pose_rotation_w"]),
        ],
        dtype=np.float32,
    )
    rgb_rot = quaternion_to_matrix(rgb_quat)
    return (points_world - rgb_pos[None, :]) @ rgb_rot


def unproject_depth(depth_m: np.ndarray, k_depth: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ys, xs = np.where(valid)
    z = depth_m[ys, xs].astype(np.float32)
    x = (xs.astype(np.float32) - k_depth[0, 2]) * z / k_depth[0, 0]
    y = (ys.astype(np.float32) - k_depth[1, 2]) * z / k_depth[1, 1]
    return np.stack([x, y, z], axis=1), xs, ys


def transform_points(points: np.ndarray, transform: np.ndarray) -> np.ndarray:
    points_h = np.concatenate([points, np.ones((points.shape[0], 1), dtype=np.float32)], axis=1)
    return (points_h @ transform.T)[:, :3]


def project_points(points_rgb: np.ndarray, k_rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    z = np.maximum(points_rgb[:, 2], 1e-6)
    u = points_rgb[:, 0] * k_rgb[0, 0] / z + k_rgb[0, 2]
    v = points_rgb[:, 1] * k_rgb[1, 1] / z + k_rgb[1, 2]
    return np.rint(u).astype(np.int32), np.rint(v).astype(np.int32)


def project_rgb_camera_points(points_rgb: np.ndarray, rgb_meta: dict) -> tuple[np.ndarray, np.ndarray]:
    k_rgb = rgb_intrinsics(rgb_meta)
    z = np.maximum(points_rgb[:, 2], 1e-6)
    u = points_rgb[:, 0] * k_rgb[0, 0] / z + k_rgb[0, 2]
    sensor_y = points_rgb[:, 1] * k_rgb[1, 1] / z + k_rgb[1, 2]
    height = int(rgb_meta["resolution_h"])
    v = (height - 1) - sensor_y
    return np.rint(u).astype(np.int32), np.rint(v).astype(np.int32)


def align_depth_to_rgb(
    depth_m: np.ndarray,
    rgb_shape: tuple[int, int],
    k_depth: np.ndarray,
    k_rgb: np.ndarray,
    t_depth_to_rgb: np.ndarray,
    min_depth: float,
    max_depth: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rgb_h, rgb_w = rgb_shape
    valid = np.isfinite(depth_m) & (depth_m >= min_depth) & (depth_m <= max_depth)
    depth_points, _, _ = unproject_depth(depth_m, k_depth, valid)
    if depth_points.size == 0:
        empty = np.zeros((rgb_h, rgb_w), dtype=np.float32)
        return empty, np.empty((0, 3), dtype=np.float32), np.empty((0,), dtype=np.int32), np.empty((0,), dtype=np.int32), valid

    points_rgb = transform_points(depth_points, t_depth_to_rgb)
    in_front = points_rgb[:, 2] > 0.01
    points_rgb = points_rgb[in_front]
    u, v = project_points(points_rgb, k_rgb)
    in_bounds = (u >= 0) & (u < rgb_w) & (v >= 0) & (v < rgb_h)
    points_rgb = points_rgb[in_bounds]
    u = u[in_bounds]
    v = v[in_bounds]

    aligned = np.full((rgb_h, rgb_w), np.inf, dtype=np.float32)
    np.minimum.at(aligned, (v, u), points_rgb[:, 2].astype(np.float32))
    aligned[~np.isfinite(aligned)] = 0.0
    return aligned, points_rgb.astype(np.float32), u, v, valid


def align_depth_to_rgb_sdk(
    depth_m: np.ndarray,
    meta: dict,
    min_depth: float,
    max_depth: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rgb_meta = meta["rgb"]
    depth_meta = meta["depth"]
    rgb_h = int(rgb_meta["resolution_h"])
    rgb_w = int(rgb_meta["resolution_w"])
    valid = np.isfinite(depth_m) & (depth_m >= min_depth) & (depth_m <= max_depth)
    points_world = unproject_environment_depth(depth_m, depth_meta, valid)
    if points_world.size == 0:
        return np.zeros((rgb_h, rgb_w), dtype=np.float32), np.empty((0, 3), dtype=np.float32), valid

    points_rgb = world_to_rgb_camera(points_world, rgb_meta)
    in_front = points_rgb[:, 2] > 0.01
    points_rgb = points_rgb[in_front]
    u, v = project_rgb_camera_points(points_rgb, rgb_meta)
    in_bounds = (u >= 0) & (u < rgb_w) & (v >= 0) & (v < rgb_h)
    points_rgb = points_rgb[in_bounds]
    u = u[in_bounds]
    v = v[in_bounds]

    aligned = np.full((rgb_h, rgb_w), np.inf, dtype=np.float32)
    np.minimum.at(aligned, (v, u), points_rgb[:, 2].astype(np.float32))
    aligned[~np.isfinite(aligned)] = 0.0
    return aligned, points_rgb.astype(np.float32), valid


def align_depth_to_rgb_screen_space(
    depth_m: np.ndarray,
    meta: dict,
    min_depth: float,
    max_depth: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rgb_meta = meta["rgb"]
    rgb_h = int(rgb_meta["resolution_h"])
    rgb_w = int(rgb_meta["resolution_w"])
    filtered = np.where(
        np.isfinite(depth_m) & (depth_m >= min_depth) & (depth_m <= max_depth),
        depth_m,
        0.0,
    ).astype(np.float32)
    aligned = cv2.resize(filtered, (rgb_w, rgb_h), interpolation=cv2.INTER_NEAREST)
    k_rgb = rgb_intrinsics(rgb_meta)
    ys, xs = np.where(aligned > 0)
    z = aligned[ys, xs]
    x = (xs.astype(np.float32) - k_rgb[0, 2]) * z / k_rgb[0, 0]
    sensor_y = (rgb_h - 1 - ys).astype(np.float32)
    y = (sensor_y - k_rgb[1, 2]) * z / k_rgb[1, 1]
    points_rgb = np.stack([x, y, z], axis=1).astype(np.float32)
    return aligned, points_rgb, filtered > 0


def build_nearest_index(aligned_depth: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    valid = aligned_depth > 0
    h, w = aligned_depth.shape
    if not valid.any():
        return (
            np.full((h, w), -1, dtype=np.int32),
            np.full((h, w), -1, dtype=np.int32),
            np.full((h, w), np.inf, dtype=np.float32),
        )

    mask = np.where(valid, 0, 255).astype(np.uint8)
    dist, labels = cv2.distanceTransformWithLabels(
        mask,
        cv2.DIST_L2,
        cv2.DIST_MASK_3,
        labelType=cv2.DIST_LABEL_PIXEL,
    )
    valid_y, valid_x = np.where(valid)
    max_label = int(labels.max())
    label_x = np.full(max_label + 1, -1, dtype=np.int32)
    label_y = np.full(max_label + 1, -1, dtype=np.int32)
    valid_labels = labels[valid_y, valid_x]
    label_x[valid_labels] = valid_x
    label_y[valid_labels] = valid_y
    nearest_x = label_x[labels]
    nearest_y = label_y[labels]
    return nearest_x, nearest_y, dist.astype(np.float32)


def depth_colors(depth_values: np.ndarray, min_depth: float, max_depth: float) -> np.ndarray:
    norm = np.clip((depth_values - min_depth) / max(max_depth - min_depth, 1e-6), 0.0, 1.0)
    colors = np.zeros((depth_values.shape[0], 3), dtype=np.uint8)
    colors[:, 0] = (255.0 * (1.0 - norm)).astype(np.uint8)
    colors[:, 1] = (70.0 * (1.0 - np.abs(norm - 0.5) * 2.0)).astype(np.uint8)
    colors[:, 2] = (255.0 * norm).astype(np.uint8)
    return colors


def make_depth_overlay(
    rgb: np.ndarray,
    aligned_depth: np.ndarray,
    min_depth: float,
    max_depth: float,
    alpha: float = 0.38,
    radius: int = 2,
    reverse_colors: bool = False,
) -> np.ndarray:
    valid_y, valid_x = np.where(aligned_depth > 0)
    if valid_x.size == 0:
        return rgb.copy()

    depths = aligned_depth[valid_y, valid_x]
    lo = max(min_depth, float(np.percentile(depths, 1)))
    hi = min(max_depth, float(np.percentile(depths, 99)))
    colors = depth_colors(depths, lo, hi)
    if reverse_colors:
        colors = colors[:, ::-1].copy()
    overlay = rgb.copy()

    offsets = [(0, 0)]
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            if dx * dx + dy * dy <= radius * radius:
                offsets.append((dy, dx))

    h, w = aligned_depth.shape
    for dy, dx in offsets:
        yy = valid_y + dy
        xx = valid_x + dx
        inside = (xx >= 0) & (xx < w) & (yy >= 0) & (yy < h)
        overlay[yy[inside], xx[inside]] = colors[inside]

    blended = cv2.addWeighted(overlay, alpha, rgb, 1.0 - alpha, 0.0)
    return blended


def sample_cloud_colors(
    rgb: np.ndarray,
    points_rgb: np.ndarray,
    rgb_meta: dict,
) -> tuple[np.ndarray, np.ndarray]:
    rgb_h, rgb_w = rgb.shape[:2]
    u, v = project_rgb_camera_points(points_rgb, rgb_meta)
    valid = (
        (points_rgb[:, 2] > 0.01)
        & (u >= 0)
        & (u < rgb_w)
        & (v >= 0)
        & (v < rgb_h)
    )
    return points_rgb[valid], rgb[v[valid], u[valid]].astype(np.uint8)


def load_saved_depth_map(path: Path, rgb_shape: tuple[int, int]) -> np.ndarray | None:
    if not path.exists():
        return None
    arr = np.load(path)
    if arr.ndim != 2:
        raise ValueError(f"{path} must be a 2D array, got shape {arr.shape}")
    rgb_h, rgb_w = rgb_shape
    if arr.shape != (rgb_h, rgb_w):
        raise ValueError(f"{path} has shape {arr.shape}, expected {(rgb_h, rgb_w)}")
    arr = arr.astype(np.float32, copy=False)
    arr[~np.isfinite(arr)] = 0.0
    arr[arr < 0] = 0.0
    return arr


def find_any2full_depth_path(frame_dir: Path) -> Path | None:
    candidates = (
        frame_dir / "aligned" / "dense_depth_any2full.npy",
        frame_dir / "aligned_final" / "dense_depth_any2full.npy",
        frame_dir / "dense_depth_any2full.npy",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_frame(
    frame_dir: Path,
    min_depth: float,
    max_depth: float,
    mode: str = "sdk_reprojection",
    depth_origin: str = "raw",
) -> FrameData:
    meta = load_meta(frame_dir)
    rgb = load_rgb(frame_dir)
    depth_meta = meta["depth"]
    depth_w = int(depth_meta["resolution_w"])
    depth_h = int(depth_meta["resolution_h"])
    depth_ndc = read_depth_ndc(frame_dir, depth_w, depth_h)
    if depth_origin == "flip_y":
        depth_ndc = np.flipud(depth_ndc)
    depth_m = ndc_to_linear_depth(
        depth_ndc,
        float(depth_meta["near_z"]),
        float(depth_meta.get("far_z", math.inf)),
    )

    if mode == "sdk_reprojection":
        aligned, points_rgb_all, _ = align_depth_to_rgb_sdk(depth_m, meta, min_depth, max_depth)
    elif mode == "screen_space":
        aligned, points_rgb_all, _ = align_depth_to_rgb_screen_space(depth_m, meta, min_depth, max_depth)
    else:
        k_depth = depth_intrinsics_from_fov(depth_meta)
        k_rgb = rgb_intrinsics(meta["rgb"])
        t_d2r = depth_to_rgb_transform(meta)
        aligned, points_rgb_all, _, _, _ = align_depth_to_rgb(
            depth_m,
            rgb.shape[:2],
            k_depth,
            k_rgb,
            t_d2r,
            min_depth,
            max_depth,
        )
    overlay_rgb = make_depth_overlay(rgb, aligned, min_depth, max_depth)
    any2full_path = find_any2full_depth_path(frame_dir)
    any2full_depth = load_saved_depth_map(any2full_path, rgb.shape[:2]) if any2full_path is not None else None
    any2full_overlay_rgb = (
        make_depth_overlay(rgb, any2full_depth, min_depth, max_depth, reverse_colors=True)
        if any2full_depth is not None
        else None
    )
    cloud_points, cloud_colors = sample_cloud_colors(rgb, points_rgb_all, meta["rgb"])

    return FrameData(
        frame_dir=frame_dir,
        meta=meta,
        rgb=rgb,
        depth_ndc=depth_ndc,
        depth_m=depth_m,
        aligned_depth=aligned,
        overlay_rgb=overlay_rgb,
        any2full_depth=any2full_depth,
        any2full_overlay_rgb=any2full_overlay_rgb,
        any2full_path=any2full_path,
        cloud_points=cloud_points,
        cloud_colors=cloud_colors,
        projected_depth_count=int(np.count_nonzero(aligned > 0)),
        any2full_depth_count=int(np.count_nonzero(any2full_depth > 0)) if any2full_depth is not None else 0,
        alignment_mode=mode,
    )


def discover_frames(root: Path) -> list[Path]:
    frames = sorted(p for p in root.glob("capture_*") if (p / "meta.json").exists())
    if not frames:
        raise FileNotFoundError(f"No capture_* frames with meta.json under {root}")
    return frames


def resize_for_display(image: np.ndarray, max_size: int) -> tuple[Image.Image, float]:
    h, w = image.shape[:2]
    scale = min(max_size / w, max_size / h, 1.0)
    out_w = max(1, int(round(w * scale)))
    out_h = max(1, int(round(h * scale)))
    resized = cv2.resize(image, (out_w, out_h), interpolation=cv2.INTER_AREA)
    return Image.fromarray(resized), scale


class RgbdViewer:
    def __init__(self, args: argparse.Namespace, frames: list[Path]) -> None:
        self.args = args
        self.frames = frames
        self.frame_index = int(np.clip(args.frame, 0, len(frames) - 1))
        self.frame: FrameData | None = None
        self.rgb_scale = 1.0
        self.rgb_photo: ImageTk.PhotoImage | None = None
        self.cloud_photo: ImageTk.PhotoImage | None = None
        self.yaw = 0.0
        self.pitch = -0.2
        self.zoom = 1.0
        self._drag_last: tuple[int, int] | None = None
        self._nearest_cache: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._last_hover_display_xy: tuple[int, int] | None = None

        self.root = tk.Tk()
        self.root.title("Quest 3 RGB-D Alignment Viewer")
        self.root.geometry(f"{max(args.view_size, args.cloud_width) + 80}x{args.cloud_height + 150}")
        self._build_ui()
        self.load_current_frame()

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(top, text="Prev", command=self.prev_frame).pack(side=tk.LEFT)
        ttk.Button(top, text="Next", command=self.next_frame).pack(side=tk.LEFT, padx=(6, 12))
        self.frame_var = tk.StringVar()
        self.frame_combo = ttk.Combobox(
            top,
            textvariable=self.frame_var,
            values=[p.name for p in self.frames],
            state="readonly",
            width=18,
        )
        self.frame_combo.pack(side=tk.LEFT)
        self.frame_combo.bind("<<ComboboxSelected>>", self.on_frame_selected)
        self.status_var = tk.StringVar()
        ttk.Label(top, textvariable=self.status_var).pack(side=tk.LEFT, padx=14)

        ttk.Label(top, text="Preview").pack(side=tk.LEFT, padx=(12, 4))
        self.preview_depth_var = tk.StringVar(value=self.args.preview_depth_source)
        self.preview_combo = ttk.Combobox(
            top,
            textvariable=self.preview_depth_var,
            values=("auto", "sparse", "any2full"),
            state="readonly",
            width=10,
        )
        self.preview_combo.pack(side=tk.LEFT)
        self.preview_combo.bind("<<ComboboxSelected>>", self.on_preview_mode_changed)

        ttk.Label(top, text="Origin").pack(side=tk.LEFT, padx=(12, 4))
        self.origin_anchor_var = tk.StringVar(value=self.args.origin_anchor)
        self.origin_combo = ttk.Combobox(
            top,
            textvariable=self.origin_anchor_var,
            values=("bottom_right", "bottom_left", "top_left", "top_right", "center", "custom"),
            state="readonly",
            width=13,
        )
        self.origin_combo.pack(side=tk.LEFT)
        self.origin_combo.bind("<<ComboboxSelected>>", self.on_origin_changed)

        ttk.Label(top, text="x0").pack(side=tk.LEFT, padx=(12, 2))
        self.origin_x_var = tk.StringVar(value="" if self.args.origin_x is None else str(self.args.origin_x))
        self.origin_x_entry = ttk.Entry(top, textvariable=self.origin_x_var, width=6)
        self.origin_x_entry.pack(side=tk.LEFT)
        self.origin_x_entry.bind("<Return>", self.on_origin_changed)

        ttk.Label(top, text="y0").pack(side=tk.LEFT, padx=(8, 2))
        self.origin_y_var = tk.StringVar(value="" if self.args.origin_y is None else str(self.args.origin_y))
        self.origin_y_entry = ttk.Entry(top, textvariable=self.origin_y_var, width=6)
        self.origin_y_entry.pack(side=tk.LEFT)
        self.origin_y_entry.bind("<Return>", self.on_origin_changed)

        notebook = ttk.Notebook(self.root)
        notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        rgb_tab = ttk.Frame(notebook, padding=8)
        cloud_tab = ttk.Frame(notebook, padding=8)
        notebook.add(rgb_tab, text="RGB depth")
        notebook.add(cloud_tab, text="Point cloud")

        rgb_main = ttk.Frame(rgb_tab)
        rgb_main.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        rgb_left = ttk.Frame(rgb_main)
        rgb_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        rgb_right = ttk.Frame(rgb_main, padding=(12, 0, 0, 0))
        rgb_right.pack(side=tk.RIGHT, fill=tk.Y)

        self.rgb_canvas = tk.Canvas(
            rgb_left,
            width=self.args.view_size,
            height=self.args.view_size,
            background="#151515",
            highlightthickness=0,
        )
        self.rgb_canvas.pack(side=tk.TOP, anchor=tk.CENTER)
        self.rgb_canvas.bind("<Motion>", self.on_rgb_motion)
        self.rgb_canvas.bind("<Leave>", self.on_rgb_leave)
        self.depth_var = tk.StringVar(value="Move mouse over the RGB-D image to inspect RGB-camera XYZ and origin-relative xyz in metres.")
        ttk.Label(rgb_left, textvariable=self.depth_var).pack(side=tk.TOP, fill=tk.X, pady=(8, 0))

        hover_group = ttk.LabelFrame(rgb_right, text="Hover Probe", padding=10)
        hover_group.pack(side=tk.TOP, fill=tk.X)
        self.hover_summary_var = tk.StringVar(value="No active sample")
        self.hover_rgb_xyz_var = tk.StringVar(value="RGB cam XYZ: -")
        self.hover_origin_xyz_var = tk.StringVar(value="Origin-rel xyz: -")
        self.hover_sample_var = tk.StringVar(value="Sample: -")
        for var in (
            self.hover_summary_var,
            self.hover_rgb_xyz_var,
            self.hover_origin_xyz_var,
            self.hover_sample_var,
        ):
            ttk.Label(hover_group, textvariable=var, justify=tk.LEFT, anchor=tk.W).pack(side=tk.TOP, fill=tk.X, pady=2)

        self.cloud_canvas = tk.Canvas(
            cloud_tab,
            width=self.args.cloud_width,
            height=self.args.cloud_height,
            background="#111111",
            highlightthickness=0,
        )
        self.cloud_canvas.pack(side=tk.TOP, anchor=tk.CENTER)
        self.cloud_canvas.bind("<ButtonPress-1>", self.on_cloud_press)
        self.cloud_canvas.bind("<B1-Motion>", self.on_cloud_drag)
        self.cloud_canvas.bind("<MouseWheel>", self.on_cloud_wheel)
        self.cloud_var = tk.StringVar(
            value="Drag to rotate. Mouse wheel zooms. Points are raw valid depth samples colored by projected RGB pixels.",
        )
        ttk.Label(cloud_tab, textvariable=self.cloud_var).pack(side=tk.TOP, fill=tk.X, pady=(8, 0))

    def load_current_frame(self) -> None:
        self.frame = load_frame(
            self.frames[self.frame_index],
            self.args.min_depth,
            self.args.max_depth,
            self.args.mode,
            self.args.depth_origin,
        )
        self._nearest_cache = {}
        self._last_hover_display_xy = None
        self.frame_var.set(self.frames[self.frame_index].name)
        self.yaw = -0.65
        self.pitch = -0.28
        self.zoom = 1.8
        self.update_rgb_image()
        self.render_cloud()
        self.update_status()

    def update_status(self) -> None:
        assert self.frame is not None
        rgb = self.frame.meta["rgb"]
        depth = self.frame.meta["depth"]
        active_name = self.get_active_depth_source_name()
        active_depth = self.get_active_depth_map()
        exact = int(np.count_nonzero(active_depth > 0))
        cloud = self.frame.cloud_points.shape[0]
        origin_x, origin_y = self.resolve_origin_xy()
        any2full_note = self.frame.any2full_path.name if self.frame.any2full_path is not None else "missing"
        self.status_var.set(
            f"{self.frames[self.frame_index].name} | RGB {rgb['resolution_w']}x{rgb['resolution_h']} | "
            f"depth {depth['resolution_w']}x{depth['resolution_h']} | mode {self.frame.alignment_mode} | "
            f"row-order {self.args.depth_origin} | preview {active_name} | origin ({origin_x}, {origin_y}) | "
            f"projected {exact} | cloud {cloud} | any2full {any2full_note}"
        )
        self.cloud_var.set(
            f"Cloud points: {cloud}. 3D perspective view; drag to orbit, wheel to zoom."
        )
        self.clear_hover_panel()

    def get_active_depth_source_name(self) -> str:
        assert self.frame is not None
        requested = self.preview_depth_var.get().strip() or "auto"
        if requested == "any2full":
            return "any2full" if self.frame.any2full_depth is not None else "sparse"
        if requested == "auto":
            return "any2full" if self.frame.any2full_depth is not None else "sparse"
        return "sparse"

    def get_active_depth_map(self) -> np.ndarray:
        assert self.frame is not None
        return self.frame.any2full_depth if self.get_active_depth_source_name() == "any2full" and self.frame.any2full_depth is not None else self.frame.aligned_depth

    def get_active_overlay_image(self) -> np.ndarray:
        assert self.frame is not None
        if self.get_active_depth_source_name() == "any2full" and self.frame.any2full_overlay_rgb is not None:
            return self.frame.any2full_overlay_rgb
        return self.frame.overlay_rgb

    def get_active_nearest_index(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        source_name = self.get_active_depth_source_name()
        cached = self._nearest_cache.get(source_name)
        if cached is not None:
            return cached
        aligned_depth = self.get_active_depth_map()
        cached = build_nearest_index(aligned_depth)
        self._nearest_cache[source_name] = cached
        return cached

    def resolve_origin_xy(self) -> tuple[int, int]:
        assert self.frame is not None
        h, w = self.frame.rgb.shape[:2]
        anchor = self.origin_anchor_var.get().strip() or "bottom_right"
        anchors = {
            "bottom_right": (w - 1, h - 1),
            "bottom_left": (0, h - 1),
            "top_left": (0, 0),
            "top_right": (w - 1, 0),
            "center": ((w - 1) // 2, (h - 1) // 2),
        }
        if anchor != "custom":
            return anchors.get(anchor, anchors["bottom_right"])

        def _parse_or_default(raw: str, default: int, max_value: int) -> int:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                value = default
            return int(np.clip(value, 0, max_value))

        x0 = _parse_or_default(self.origin_x_var.get(), w - 1, w - 1)
        y0 = _parse_or_default(self.origin_y_var.get(), h - 1, h - 1)
        return x0, y0

    def pixel_to_viewer_xyz(self, pixel_x: int, pixel_y: int, depth_m: float) -> tuple[float, float, float]:
        assert self.frame is not None
        origin_x, origin_y = self.resolve_origin_xy()
        k_rgb = rgb_intrinsics(self.frame.meta["rgb"])
        fx = float(k_rgb[0, 0])
        fy = float(k_rgb[1, 1])
        x_view = float(pixel_x - origin_x) * float(depth_m) / max(fx, 1e-6)
        y_view = float(origin_y - pixel_y) * float(depth_m) / max(fy, 1e-6)
        return x_view, y_view, float(depth_m)

    def pixel_to_rgb_camera_xyz(self, pixel_x: int, pixel_y: int, depth_m: float) -> tuple[float, float, float]:
        assert self.frame is not None
        rgb_meta = self.frame.meta["rgb"]
        k_rgb = rgb_intrinsics(rgb_meta)
        fx = float(k_rgb[0, 0])
        fy = float(k_rgb[1, 1])
        cx = float(k_rgb[0, 2])
        cy = float(k_rgb[1, 2])
        sensor_y = (int(rgb_meta["resolution_h"]) - 1) - float(pixel_y)
        x_cam = (float(pixel_x) - cx) * float(depth_m) / max(fx, 1e-6)
        y_cam = (sensor_y - cy) * float(depth_m) / max(fy, 1e-6)
        return x_cam, y_cam, float(depth_m)

    def update_rgb_image(
        self,
        marker: tuple[int, int] | None = None,
        tooltip: str | None = None,
        tooltip_pos: tuple[int, int] | None = None,
    ) -> None:
        assert self.frame is not None
        image = self.get_active_overlay_image().copy()
        if marker is not None:
            x, y = marker
            cv2.drawMarker(
                image,
                (x, y),
                (255, 255, 255),
                markerType=cv2.MARKER_CROSS,
                markerSize=28,
                thickness=2,
            )
            cv2.circle(image, (x, y), 9, (0, 0, 0), 2, lineType=cv2.LINE_AA)

        pil, self.rgb_scale = resize_for_display(image, self.args.view_size)
        self.rgb_photo = ImageTk.PhotoImage(pil)
        self.rgb_canvas.config(width=pil.width, height=pil.height)
        self.rgb_canvas.delete("all")
        self.rgb_canvas.create_image(0, 0, image=self.rgb_photo, anchor=tk.NW)
        if tooltip and tooltip_pos is not None:
            tx = min(max(int(tooltip_pos[0]) + 14, 8), max(8, pil.width - 330))
            lines = tooltip.count("\n") + 1
            box_h = max(44, 22 * lines + 10)
            ty = min(max(int(tooltip_pos[1]) + 14, 8), max(8, pil.height - box_h - 8))
            self.rgb_canvas.create_rectangle(
                tx - 6,
                ty - 5,
                tx + 322,
                ty + box_h,
                fill="#101010",
                outline="#ffffff",
                stipple="gray75",
            )
            self.rgb_canvas.create_text(
                tx,
                ty,
                text=tooltip,
                fill="#ffffff",
                anchor=tk.NW,
                font=("Consolas", 11),
            )

    def clear_hover_panel(self) -> None:
        self.hover_summary_var.set("No active sample")
        self.hover_rgb_xyz_var.set("RGB cam XYZ: -")
        self.hover_origin_xyz_var.set("Origin-rel xyz: -")
        self.hover_sample_var.set("Sample: -")

    def update_hover_panel(
        self,
        *,
        query_xy: tuple[int, int] | None = None,
        sample_xy: tuple[int, int] | None = None,
        source_name: str | None = None,
        label: str | None = None,
        distance_px: float | None = None,
        rgb_cam_xyz: tuple[float, float, float] | None = None,
        origin_xyz: tuple[float, float, float] | None = None,
        origin_xy: tuple[int, int] | None = None,
    ) -> None:
        if (
            query_xy is None
            or sample_xy is None
            or source_name is None
            or label is None
            or distance_px is None
            or rgb_cam_xyz is None
            or origin_xyz is None
            or origin_xy is None
        ):
            self.clear_hover_panel()
            return
        self.hover_summary_var.set(
            f"Query ({query_xy[0]}, {query_xy[1]}) | Sample ({sample_xy[0]}, {sample_xy[1]}) | {source_name} {label} | d={distance_px:.1f}px"
        )
        self.hover_rgb_xyz_var.set(
            f"RGB cam XYZ: X={rgb_cam_xyz[0]:.3f} m  Y={rgb_cam_xyz[1]:.3f} m  Z={rgb_cam_xyz[2]:.3f} m"
        )
        self.hover_origin_xyz_var.set(
            f"Origin-rel xyz: x={origin_xyz[0]:.3f} m  y={origin_xyz[1]:.3f} m  z={origin_xyz[2]:.3f} m"
        )
        self.hover_sample_var.set(
            f"Origin pixel: ({origin_xy[0]}, {origin_xy[1]})"
        )

    def on_rgb_motion(self, event: tk.Event) -> None:
        assert self.frame is not None
        x = int(event.x / self.rgb_scale)
        y = int(event.y / self.rgb_scale)
        active_depth = self.get_active_depth_map()
        nearest_x_map, nearest_y_map, nearest_distance_map = self.get_active_nearest_index()
        h, w = active_depth.shape
        if x < 0 or x >= w or y < 0 or y >= h:
            return

        self._last_hover_display_xy = (event.x, event.y)
        source_name = self.get_active_depth_source_name()
        exact_depth = float(active_depth[y, x])
        nearest_x = int(nearest_x_map[y, x])
        nearest_y = int(nearest_y_map[y, x])
        if exact_depth > 0:
            depth = exact_depth
            label = "exact"
            marker = (x, y)
            distance = 0.0
        elif nearest_x >= 0 and nearest_y >= 0:
            depth = float(active_depth[nearest_y, nearest_x])
            label = "nearest"
            marker = (nearest_x, nearest_y)
            distance = float(nearest_distance_map[y, x])
        else:
            text = f"RGB ({x}, {y}): no depth in current {source_name} preview"
            self.depth_var.set(text)
            self.clear_hover_panel()
            self.update_rgb_image(tooltip=text, tooltip_pos=(event.x, event.y))
            return

        cam_xyz = self.pixel_to_rgb_camera_xyz(marker[0], marker[1], depth)
        view_xyz = self.pixel_to_viewer_xyz(marker[0], marker[1], depth)
        origin_x, origin_y = self.resolve_origin_xy()
        text = (
            f"query ({x}, {y}) | sample ({marker[0]}, {marker[1]}) | {source_name} {label} | z {depth:.3f} m\n"
            f"RGB cam X {cam_xyz[0]:.3f} m | Y {cam_xyz[1]:.3f} m | Z {cam_xyz[2]:.3f} m\n"
            f"origin-rel x {view_xyz[0]:.3f} m | y {view_xyz[1]:.3f} m | z {view_xyz[2]:.3f} m | origin ({origin_x}, {origin_y}) | d {distance:.1f}px"
        )
        self.depth_var.set(text.replace("\n", "    "))
        self.update_hover_panel(
            query_xy=(x, y),
            sample_xy=marker,
            source_name=source_name,
            label=label,
            distance_px=distance,
            rgb_cam_xyz=cam_xyz,
            origin_xyz=view_xyz,
            origin_xy=(origin_x, origin_y),
        )
        self.update_rgb_image(marker, text, (event.x, event.y))

    def on_rgb_leave(self, _event: tk.Event) -> None:
        self._last_hover_display_xy = None
        self.depth_var.set("Move mouse over the RGB-D image to inspect RGB-camera XYZ and origin-relative xyz in metres.")
        self.clear_hover_panel()
        self.update_rgb_image()

    def on_preview_mode_changed(self, _event: tk.Event) -> None:
        self.update_rgb_image()
        self.update_status()

    def on_origin_changed(self, _event: tk.Event | None = None) -> None:
        self.update_status()
        if self._last_hover_display_xy is not None:
            class _Event:
                def __init__(self, x: int, y: int) -> None:
                    self.x = x
                    self.y = y

            self.on_rgb_motion(_Event(*self._last_hover_display_xy))

    def render_cloud(self) -> None:
        assert self.frame is not None
        img = render_cloud_image(
            self.frame.cloud_points,
            self.frame.cloud_colors,
            self.frame.meta["rgb"],
            self.args.cloud_width,
            self.args.cloud_height,
            self.yaw,
            self.pitch,
            self.zoom,
        )
        self.cloud_photo = ImageTk.PhotoImage(Image.fromarray(img))
        self.cloud_canvas.config(width=self.args.cloud_width, height=self.args.cloud_height)
        self.cloud_canvas.delete("all")
        self.cloud_canvas.create_image(0, 0, image=self.cloud_photo, anchor=tk.NW)

    def on_cloud_press(self, event: tk.Event) -> None:
        self._drag_last = (event.x, event.y)

    def on_cloud_drag(self, event: tk.Event) -> None:
        if self._drag_last is None:
            self._drag_last = (event.x, event.y)
            return
        last_x, last_y = self._drag_last
        self.yaw += (event.x - last_x) * 0.008
        self.pitch += (event.y - last_y) * 0.008
        self.pitch = float(np.clip(self.pitch, -1.45, 1.45))
        self._drag_last = (event.x, event.y)
        self.render_cloud()

    def on_cloud_wheel(self, event: tk.Event) -> None:
        self.zoom *= 1.12 if event.delta > 0 else 1.0 / 1.12
        self.zoom = float(np.clip(self.zoom, 0.25, 8.0))
        self.render_cloud()

    def on_frame_selected(self, _event: tk.Event) -> None:
        selected = self.frame_combo.current()
        if selected >= 0:
            self.frame_index = selected
            self.load_current_frame()

    def prev_frame(self) -> None:
        self.frame_index = (self.frame_index - 1) % len(self.frames)
        self.load_current_frame()

    def next_frame(self) -> None:
        self.frame_index = (self.frame_index + 1) % len(self.frames)
        self.load_current_frame()

    def run(self) -> None:
        self.root.mainloop()


def rotation_matrix(yaw: float, pitch: float) -> np.ndarray:
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    ry = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]], dtype=np.float32)
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cp, -sp], [0.0, sp, cp]], dtype=np.float32)
    return rx @ ry


def render_cloud_image(
    points: np.ndarray,
    colors: np.ndarray,
    rgb_meta: dict,
    width: int,
    height: int,
    yaw: float,
    pitch: float,
    zoom: float,
) -> np.ndarray:
    canvas = np.full((height, width, 3), 18, dtype=np.uint8)
    if points.size == 0:
        cv2.putText(canvas, "No point cloud", (30, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (220, 220, 220), 2)
        return canvas

    _ = rgb_meta
    center = np.median(points, axis=0)
    centered = points - center
    view = centered @ rotation_matrix(yaw, pitch).T

    radius = float(np.percentile(np.linalg.norm(centered, axis=1), 85))
    camera_distance = max(radius * 1.35 / max(zoom, 1e-3), 0.25)
    z_camera = view[:, 2] + camera_distance
    focal = min(width, height) * 0.95
    px = np.rint(width * 0.5 + (view[:, 0] / np.maximum(z_camera, 1e-3)) * focal).astype(np.int32)
    py = np.rint(height * 0.54 - (view[:, 1] / np.maximum(z_camera, 1e-3)) * focal).astype(np.int32)
    label = f"3D perspective yaw={math.degrees(yaw):.0f} pitch={math.degrees(pitch):.0f}"

    inside = (z_camera > 0.02) & (px >= 1) & (px < width - 1) & (py >= 1) & (py < height - 1)
    if not inside.any():
        return canvas

    px = px[inside]
    py = py[inside]
    z = z_camera[inside]
    col = colors[inside]
    # Draw far-to-near so closer points remain visible when splats overlap.
    order = np.argsort(z)[::-1]
    px = px[order]
    py = py[order]
    col = col[order]

    # Draw small square splats for visibility while preserving one source point per depth sample.
    for dy, dx in ((0, 0), (1, 0), (0, 1), (-1, 0), (0, -1)):
        canvas[py + dy, px + dx] = col

    cv2.putText(canvas, f"points {points.shape[0]} | {label}", (18, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (220, 220, 220), 1)
    cv2.putText(canvas, "drag rotate | wheel zoom", (18, height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1)
    return canvas


def export_overlays(frames: list[Path], args: argparse.Namespace) -> None:
    assert args.export_dir is not None
    args.export_dir.mkdir(parents=True, exist_ok=True)
    for frame_dir in frames:
        frame = load_frame(frame_dir, args.min_depth, args.max_depth, args.mode, args.depth_origin)
        out = args.export_dir / f"{frame_dir.name}_aligned_overlay.png"
        cv2.imwrite(str(out), cv2.cvtColor(frame.overlay_rgb, cv2.COLOR_RGB2BGR))


def write_ascii_ply(path: Path, points: np.ndarray, colors: np.ndarray) -> None:
    with path.open("w", encoding="ascii", newline="\n") as f:
        f.write("ply\n")
        f.write("format ascii 1.0\n")
        f.write("comment coordinates are RGB camera space: x right, y up, z forward, units metres\n")
        f.write(f"element vertex {points.shape[0]}\n")
        f.write("property float x\n")
        f.write("property float y\n")
        f.write("property float z\n")
        f.write("property uchar red\n")
        f.write("property uchar green\n")
        f.write("property uchar blue\n")
        f.write("end_header\n")
        for p, c in zip(points, colors, strict=True):
            f.write(f"{p[0]:.6f} {p[1]:.6f} {p[2]:.6f} {int(c[0])} {int(c[1])} {int(c[2])}\n")


def export_point_clouds(frames: list[Path], args: argparse.Namespace) -> None:
    assert args.export_ply_dir is not None
    args.export_ply_dir.mkdir(parents=True, exist_ok=True)
    for frame_dir in frames:
        frame = load_frame(frame_dir, args.min_depth, args.max_depth, args.mode, args.depth_origin)
        out = args.export_ply_dir / f"{frame_dir.name}_cloud_rgb_camera.ply"
        write_ascii_ply(out, frame.cloud_points, frame.cloud_colors)


def print_stats(frames: list[Path], args: argparse.Namespace) -> None:
    for frame_dir in frames:
        frame = load_frame(frame_dir, args.min_depth, args.max_depth, args.mode, args.depth_origin)
        rgb = frame.meta["rgb"]
        depth = frame.meta["depth"]
        exact = int(np.count_nonzero(frame.aligned_depth > 0))
        depth_valid = int(np.count_nonzero((frame.depth_m >= args.min_depth) & (frame.depth_m <= args.max_depth)))
        coverage = exact / float(rgb["resolution_w"] * rgb["resolution_h"])
        print(
            f"{frame_dir.name}: rgb={rgb['resolution_w']}x{rgb['resolution_h']} "
            f"depth={depth['resolution_w']}x{depth['resolution_h']} "
            f"valid_depth={depth_valid} projected_pixels={exact} "
            f"coverage={coverage:.3%} cloud_points={frame.cloud_points.shape[0]} "
            f"mode={frame.alignment_mode} depth_origin={args.depth_origin} "
            f"pose_source={depth.get('pose_source', 'unknown')}"
        )


def main() -> None:
    args = parse_args()
    frames = discover_frames(args.data)
    if args.export_dir is not None:
        export_overlays(frames, args)
    if args.export_ply_dir is not None:
        export_point_clouds(frames, args)
    if args.no_ui:
        print_stats(frames, args)
        return
    viewer = RgbdViewer(args, frames)
    viewer.run()


if __name__ == "__main__":
    main()
