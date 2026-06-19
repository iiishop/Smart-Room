from __future__ import annotations

import argparse
import http.server
import json
import math
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk

from cursor_prompt_projector import CursorPromptConfig, build_cursor_prompt
from rgbd_device_mask_refine import (
    RgbdMaskRefineConfig,
    overlay_mask as overlay_device_mask,
    refine_device_mask,
    write_refine_outputs,
)
from rgbd_device_prompt_builder import RgbdPromptConfig, build_rgbd_device_prompt
from sam2_device_segment import (
    DEFAULT_SAM2_MODEL_ID,
    Sam2DeviceSegmenter,
    Sam2PromptConfig,
    Sam2RuntimeConfig,
    mask_overlay as overlay_sam2_mask,
)
from pose_projection import extract_world_contour, world_to_rgb_pixel
from rgb_guided_depth_postprocess import RgbGuidedPostprocessConfig, confidence_overlay, postprocess_depth
from rgb_edge_depth_refine import EdgeDepthRefineConfig, refine_depth_anchors


DEFAULT_DATA_DIR = Path("E:/test/rgbd-v10/rgbd_test")
DEFAULT_ANY2FULL_ROOT = Path("D:/FromGithub/Any2Full")


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
    device_mask: np.ndarray | None
    device_overlay_rgb: np.ndarray | None
    device_mask_path: Path | None
    device_info: dict | None
    device_contour_3d: list[list[float]] | None
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
        default=None,
        help=f"Capture root containing capture_0000.. directories. Default: {DEFAULT_DATA_DIR}. Not required in --server mode.",
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
    parser.add_argument("--depth-origin",
        choices=("raw", "flip_y"),
        default="raw",
        help="Depth row order. Use flip_y if the saved RenderTexture readback is vertically inverted.",
    )
    parser.add_argument("--no-ui", action="store_true", help="Load and align all frames, then print stats.")
    parser.add_argument(
        "--server",
        action="store_true",
        help="Start HTTP server on port 8500 to accept trigger payloads from Quest 3.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host in --server mode.")
    parser.add_argument("--port", type=int, default=8500, help="HTTP bind port in --server mode.")
    parser.add_argument(
        "--any2full-root",
        type=Path,
        default=DEFAULT_ANY2FULL_ROOT,
        help="Any2Full repository root. Used for dense depth completion in --server mode.",
    )
    parser.add_argument(
        "--any2full-python",
        type=Path,
        default=None,
        help="Python executable for Any2Full. Default: <any2full-root>/.venv-any2full/Scripts/python.exe if present.",
    )
    parser.add_argument(
        "--any2full-checkpoint",
        type=Path,
        default=None,
        help="Any2Full checkpoint path. Default: <any2full-root>/checkpoints/Any2Full_vitl.pth.tar.",
    )
    parser.add_argument("--any2full-encoder", default="vitl", choices=("vits", "vitb", "vitl"))
    parser.add_argument("--any2full-timeout", type=float, default=300.0, help="Any2Full subprocess timeout in seconds.")
    parser.add_argument(
        "--any2full-cache-dir",
        type=Path,
        default=Path("viewer_any2full_cache"),
        help="Directory for per-trigger Any2Full RGB/depth inputs and dense outputs.",
    )
    parser.add_argument(
        "--disable-any2full",
        action="store_true",
        help="Disable Any2Full completion and show sparse aligned depth only.",
    )
    parser.add_argument(
        "--disable-rgb-edge-refine",
        action="store_true",
        help="Do not clean sparse aligned-depth anchors with RGB/depth edges before Any2Full.",
    )
    parser.add_argument("--edge-rgb-percentile", type=float, default=90.0)
    parser.add_argument("--edge-dilate-px", type=int, default=4)
    parser.add_argument("--edge-depth-jump-abs-m", type=float, default=0.12)
    parser.add_argument("--edge-depth-jump-rel", type=float, default=0.06)
    parser.add_argument("--edge-min-keep-ratio", type=float, default=0.35)
    parser.add_argument("--edge-isolated-radius-px", type=int, default=2)
    parser.add_argument("--edge-isolated-min-neighbors", type=int, default=2)
    parser.add_argument(
        "--disable-rgb-guided-postprocess",
        action="store_true",
        help="Do not run RGB-guided edge-aware refinement after Any2Full.",
    )
    parser.add_argument("--post-color-sigma", type=float, default=18.0)
    parser.add_argument("--post-depth-sigma-m", type=float, default=0.20)
    parser.add_argument("--post-iterations", type=int, default=3)
    parser.add_argument("--post-anchor-radius-px", type=int, default=3)
    parser.add_argument("--post-edge-percentile", type=float, default=90.0)
    parser.add_argument("--post-edge-dilate-px", type=int, default=2)
    parser.add_argument("--post-sparse-trust-radius-px", type=int, default=12)
    parser.add_argument("--post-sparse-blend", type=float, default=0.85)
    parser.add_argument("--post-depth-edge-abs-m", type=float, default=0.12)
    parser.add_argument("--post-depth-edge-rel", type=float, default=0.06)
    parser.add_argument("--post-boundary-dilate-px", type=int, default=2)
    parser.add_argument("--post-plane-smooth-radius-px", type=int, default=5)
    parser.add_argument("--post-plane-smooth-weight", type=float, default=0.35)
    parser.add_argument(
        "--disable-device-segmentation",
        action="store_true",
        help="Disable cursor-prompted device segmentation.",
    )
    parser.add_argument("--sam2-model-id", default=DEFAULT_SAM2_MODEL_ID)
    parser.add_argument("--sam2-checkpoint", type=Path, default=None)
    parser.add_argument("--sam2-config", default=None)
    parser.add_argument("--sam2-device", default="cuda")
    parser.add_argument("--segment-cache-dir", type=Path, default=Path("viewer_device_segments"))
    parser.add_argument("--cursor-nearest-depth-radius-px", type=int, default=10)
    parser.add_argument("--seg-depth-abs-band-m", type=float, default=0.18)
    parser.add_argument("--seg-depth-rel-band", type=float, default=0.12)
    parser.add_argument("--seg-positive-window-px", type=int, default=90)
    parser.add_argument("--seg-negative-inner-radius-px", type=int, default=36)
    parser.add_argument("--seg-negative-outer-radius-px", type=int, default=150)
    parser.add_argument("--seg-depth-local-jump-m", type=float, default=0.06)
    parser.add_argument("--seg-depth-local-jump-rel", type=float, default=0.05)
    parser.add_argument("--seg-depth-global-span-m", type=float, default=0.55)
    parser.add_argument("--seg-depth-max-radius-px", type=int, default=170)
    parser.add_argument("--seg-depth-bbox-pad-px", type=int, default=10)
    parser.add_argument("--seg-depth-max-component-area-ratio", type=float, default=0.08)
    parser.add_argument(
        "--seg-depth-ignore-texture-edges",
        action="store_true",
        help="Allow RGB-D prompt growth across pure RGB texture edges when depth is continuous.",
    )
    parser.add_argument("--seg-refine-depth-span-m", type=float, default=1.20)
    parser.add_argument(
        "--seg-enable-depth-component-union",
        action="store_true",
        help="Union the depth-grown RGB-D component back into the SAM2 mask during refinement.",
    )
    parser.add_argument("--seg-refine-open-px", type=int, default=2)
    parser.add_argument("--seg-refine-close-px", type=int, default=5)
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


def ndc_to_linear_depth_legacy(depth_ndc: np.ndarray, near: float, far: float) -> np.ndarray:
    """DEPRECATED: Derives (x, y) from near/far — mathematically imprecise for Quest 3.

    The correct approach uses raw_depth_to_linear_m() with the actual GPU
    zbuffer_x / zbuffer_y from the capture meta, which is the direct inverse
    of Meta's _EnvironmentDepthZBufferParams formula.

    This function is kept only for diagnostic comparison in load_frame().
    """
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


def raw_depth_to_linear_m(raw_depth: np.ndarray, depth_meta: dict) -> np.ndarray:
    """Convert Quest 3 raw NDC depth [0, 1] to linear metres.

    Uses the actual GPU ZBufferParams from Meta's _EnvironmentDepthZBufferParams
    shader global — the exact inverse of the GPU-side formula:
        z_ndc = ZBP.x / linear_depth - ZBP.y
        → linear_depth = ZBP.x / (z_ndc + ZBP.y)

    This matches quest3server/tracking/rgbd_final_alignment.py.
    """
    zbuffer_x = float(depth_meta["zbuffer_x"])
    zbuffer_y = float(depth_meta["zbuffer_y"])
    ndc = raw_depth.astype(np.float32) * 2.0 - 1.0
    denom = ndc + np.float32(zbuffer_y)
    return np.divide(
        np.float32(zbuffer_x),
        denom,
        out=np.zeros_like(raw_depth, dtype=np.float32),
        where=np.abs(denom) > 1e-8,
    )


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


def project_rgb_camera_points(points_rgb: np.ndarray, rgb_meta: dict) -> tuple[np.ndarray, np.ndarray]:
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


def resolve_any2full_python(any2full_root: Path, configured_python: Path | None) -> Path:
    if configured_python is not None:
        return configured_python
    bundled = any2full_root / ".venv-any2full" / "Scripts" / "python.exe"
    if bundled.exists():
        return bundled
    return Path(sys.executable)


def resolve_any2full_checkpoint(any2full_root: Path, configured_checkpoint: Path | None) -> Path:
    return configured_checkpoint if configured_checkpoint is not None else any2full_root / "checkpoints" / "Any2Full_vitl.pth.tar"


class Any2FullService:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.process: subprocess.Popen[str] | None = None
        self.lock = threading.Lock()
        self.ready = False
        self.device = "unknown"

    def start(self) -> bool:
        if self.args.disable_any2full:
            return False
        any2full_root = self.args.any2full_root.resolve()
        script = any2full_root / "any2full_infer.py"
        worker_script = Path(__file__).resolve().parent / "any2full_worker.py"
        checkpoint = resolve_any2full_checkpoint(any2full_root, self.args.any2full_checkpoint).resolve()
        python_exe = resolve_any2full_python(any2full_root, self.args.any2full_python).resolve()
        if not script.exists():
            print(f"[any2full] disabled: script not found: {script}", flush=True)
            return False
        if not worker_script.exists():
            print(f"[any2full] disabled: worker not found: {worker_script}", flush=True)
            return False
        if not checkpoint.exists():
            print(f"[any2full] disabled: checkpoint not found: {checkpoint}", flush=True)
            return False
        if not python_exe.exists():
            print(f"[any2full] disabled: python not found: {python_exe}", flush=True)
            return False

        cmd = [
            str(python_exe),
            str(worker_script),
            "--any2full-root",
            str(any2full_root),
            "--checkpoint",
            str(checkpoint),
            "--encoder",
            str(self.args.any2full_encoder),
        ]
        print(f"[any2full] preloading worker: {' '.join(cmd)}", flush=True)
        self.process = subprocess.Popen(
            cmd,
            cwd=str(any2full_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self.process.stderr is not None:
            threading.Thread(target=self._drain_stderr, daemon=True).start()

        assert self.process.stdout is not None
        ready_line = self.process.stdout.readline()
        if not ready_line:
            print("[any2full] worker exited before ready", flush=True)
            self.stop()
            return False
        try:
            ready = json.loads(ready_line)
        except json.JSONDecodeError:
            print(f"[any2full] invalid worker ready line: {ready_line!r}", flush=True)
            self.stop()
            return False
        if not ready.get("ready"):
            print(f"[any2full] worker not ready: {ready}", flush=True)
            self.stop()
            return False
        self.ready = True
        self.device = str(ready.get("device", "unknown"))
        print(f"[any2full] worker ready on {self.device}", flush=True)
        return True

    def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        for line in self.process.stderr:
            print(f"[any2full] {line.rstrip()}", flush=True)

    def infer(self, rgb_path: Path, depth_path: Path, out_path: Path) -> bool:
        if not self.ready or self.process is None or self.process.stdin is None or self.process.stdout is None:
            return False
        request = {"rgb": str(rgb_path), "depth": str(depth_path), "out": str(out_path)}
        with self.lock:
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()
            response_line = self.process.stdout.readline()
        if not response_line:
            print("[any2full] worker returned no response", flush=True)
            self.ready = False
            return False
        try:
            response = json.loads(response_line)
        except json.JSONDecodeError:
            print(f"[any2full] invalid worker response: {response_line!r}", flush=True)
            return False
        if not response.get("ok"):
            print(f"[any2full] worker inference failed: {response.get('error')}", flush=True)
            return False
        print(
            f"[any2full] inference ok: {response.get('out')} "
            f"range=[{response.get('min'):.4f}, {response.get('max'):.4f}]",
            flush=True,
        )
        return True

    def stop(self) -> None:
        if self.process is None:
            return
        try:
            self.process.terminate()
        except Exception:
            pass
        self.process = None
        self.ready = False


def run_any2full_completion(
    frame: FrameData,
    args: argparse.Namespace,
    service: Any2FullService | None = None,
) -> FrameData:
    if args.disable_any2full:
        return frame
    if frame.aligned_depth is None or not np.any(frame.aligned_depth > 0):
        return frame

    any2full_root = args.any2full_root.resolve()
    script = any2full_root / "any2full_infer.py"
    checkpoint = resolve_any2full_checkpoint(any2full_root, args.any2full_checkpoint).resolve()
    python_exe = resolve_any2full_python(any2full_root, args.any2full_python).resolve()
    if not script.exists():
        print(f"[any2full] disabled: script not found: {script}", flush=True)
        return frame
    if not checkpoint.exists():
        print(f"[any2full] disabled: checkpoint not found: {checkpoint}", flush=True)
        return frame
    if not python_exe.exists():
        print(f"[any2full] disabled: python not found: {python_exe}", flush=True)
        return frame

    cache_root = args.any2full_cache_dir
    if not cache_root.is_absolute():
        cache_root = Path.cwd() / cache_root
    capture_id = f"network_{int(time.time() * 1000)}"
    work_dir = cache_root / capture_id
    work_dir.mkdir(parents=True, exist_ok=True)

    rgb_path = work_dir / "rgb.png"
    sparse_path = work_dir / "sparse_aligned_depth.npy"
    raw_sparse_path = work_dir / "sparse_aligned_depth_raw.npy"
    edge_mask_path = work_dir / "edge_unsafe_mask.png"
    dense_path = work_dir / "dense_depth_any2full.npy"
    refined_dense_path = work_dir / "dense_depth_rgb_guided.npy"
    confidence_path = work_dir / "confidence_rgb_guided.npy"
    confidence_overlay_path = work_dir / "confidence_rgb_guided_overlay.png"
    post_edge_mask_path = work_dir / "post_rgb_edge_mask.png"
    post_depth_edge_mask_path = work_dir / "post_depth_edge_mask.png"
    post_corrected_mask_path = work_dir / "post_boundary_corrected_mask.png"
    Image.fromarray(frame.rgb.astype(np.uint8)).save(rgb_path)
    np.save(raw_sparse_path, frame.aligned_depth.astype(np.float32))

    any2full_input_depth = frame.aligned_depth.astype(np.float32)
    if not args.disable_rgb_edge_refine:
        refined = refine_depth_anchors(
            frame.rgb,
            frame.aligned_depth,
            EdgeDepthRefineConfig(
                rgb_edge_percentile=args.edge_rgb_percentile,
                edge_dilate_px=args.edge_dilate_px,
                depth_jump_abs_m=args.edge_depth_jump_abs_m,
                depth_jump_rel=args.edge_depth_jump_rel,
                min_keep_ratio=args.edge_min_keep_ratio,
                isolated_radius_px=args.edge_isolated_radius_px,
                isolated_min_neighbors=args.edge_isolated_min_neighbors,
            ),
        )
        any2full_input_depth = refined.depth
        Image.fromarray((refined.unsafe_mask.astype(np.uint8) * 255)).save(edge_mask_path)
        print(
            "[edge-refine] "
            f"valid={refined.original_valid_count} kept={refined.kept_count} "
            f"removed={refined.removed_count} mask={edge_mask_path}",
            flush=True,
        )
    np.save(sparse_path, any2full_input_depth.astype(np.float32))

    if service is not None and service.ready:
        if not service.infer(rgb_path, sparse_path, dense_path):
            return frame
    else:
        cmd = [
            str(python_exe),
            str(script),
            "--rgb",
            str(rgb_path),
            "--depth",
            str(sparse_path),
            "--out",
            str(dense_path),
            "--checkpoint",
            str(checkpoint),
            "--encoder",
            str(args.any2full_encoder),
        ]
        print(f"[any2full] running: {' '.join(cmd)}", flush=True)
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(any2full_root),
                check=False,
                capture_output=True,
                text=True,
                timeout=float(args.any2full_timeout),
            )
        except subprocess.TimeoutExpired:
            print(f"[any2full] timeout after {args.any2full_timeout:.1f}s", flush=True)
            return frame

        if completed.stdout:
            print(completed.stdout.rstrip(), flush=True)
        if completed.stderr:
            print(completed.stderr.rstrip(), flush=True)
        if completed.returncode != 0:
            print(f"[any2full] failed with exit code {completed.returncode}", flush=True)
            return frame
    if not dense_path.exists():
        print(f"[any2full] failed: output missing: {dense_path}", flush=True)
        return frame

    try:
        dense = load_saved_depth_map(dense_path, frame.rgb.shape[:2])
    except Exception as exc:
        print(f"[any2full] failed to load dense output: {exc}", flush=True)
        return frame
    if dense is None:
        return frame

    preview_depth = dense
    preview_path = dense_path
    if not args.disable_rgb_guided_postprocess:
        try:
            post = postprocess_depth(
                frame.rgb,
                any2full_input_depth,
                dense,
                RgbGuidedPostprocessConfig(
                    color_sigma=args.post_color_sigma,
                    depth_sigma_m=args.post_depth_sigma_m,
                    iterations=args.post_iterations,
                    anchor_radius_px=args.post_anchor_radius_px,
                    edge_percentile=args.post_edge_percentile,
                    edge_dilate_px=args.post_edge_dilate_px,
                    sparse_trust_radius_px=args.post_sparse_trust_radius_px,
                    sparse_blend=args.post_sparse_blend,
                    depth_edge_abs_m=args.post_depth_edge_abs_m,
                    depth_edge_rel=args.post_depth_edge_rel,
                    boundary_dilate_px=args.post_boundary_dilate_px,
                    plane_smooth_radius_px=args.post_plane_smooth_radius_px,
                    plane_smooth_weight=args.post_plane_smooth_weight,
                ),
            )
            np.save(refined_dense_path, post.depth.astype(np.float32))
            np.save(confidence_path, post.confidence.astype(np.float32))
            Image.fromarray(confidence_overlay(frame.rgb, post.confidence)).save(confidence_overlay_path)
            Image.fromarray(post.edge_mask.astype(np.uint8) * 255).save(post_edge_mask_path)
            Image.fromarray(post.depth_edge_mask.astype(np.uint8) * 255).save(post_depth_edge_mask_path)
            Image.fromarray(post.corrected_mask.astype(np.uint8) * 255).save(post_corrected_mask_path)
            preview_depth = post.depth
            preview_path = refined_dense_path
            print(
                "[rgb-guided-post] "
                f"saved={refined_dense_path} confidence={confidence_path} overlay={confidence_overlay_path}",
                flush=True,
            )
        except Exception as exc:
            print(f"[rgb-guided-post] failed, using Any2Full dense depth: {exc}", flush=True)

    frame.any2full_depth = preview_depth
    frame.any2full_overlay_rgb = make_depth_overlay(frame.rgb, preview_depth, args.min_depth, args.max_depth, reverse_colors=True)
    frame.any2full_path = preview_path
    frame.any2full_depth_count = int(np.count_nonzero(preview_depth > 0))
    frame.frame_dir = work_dir
    (work_dir / "meta.json").write_text(json.dumps(frame.meta, indent=2), encoding="utf-8")
    print(
        f"[any2full] loaded preview depth: {preview_path} valid={frame.any2full_depth_count}",
        flush=True,
    )
    return frame


def create_device_segmenter(args: argparse.Namespace) -> Sam2DeviceSegmenter | None:
    if args.disable_device_segmentation:
        return None
    model_ids = [args.sam2_model_id]
    if args.sam2_checkpoint is None:
        for fallback in ("facebook/sam2.1-hiera-small", "facebook/sam2.1-hiera-tiny"):
            if fallback not in model_ids:
                model_ids.append(fallback)

    last_error: Exception | None = None
    for model_id in model_ids:
        segmenter = Sam2DeviceSegmenter(
            Sam2RuntimeConfig(
                model_id=model_id,
                checkpoint=args.sam2_checkpoint,
                config=args.sam2_config,
                device=args.sam2_device,
            ),
            Sam2PromptConfig(
                depth_abs_band_m=args.seg_depth_abs_band_m,
                depth_rel_band=args.seg_depth_rel_band,
                positive_window_px=args.seg_positive_window_px,
                negative_inner_radius_px=args.seg_negative_inner_radius_px,
                negative_outer_radius_px=args.seg_negative_outer_radius_px,
            ),
        )
        source = str(args.sam2_checkpoint) if args.sam2_checkpoint is not None else model_id
        print(f"[device-seg] preloading SAM2: {source} on {args.sam2_device}", flush=True)
        try:
            segmenter.load()
        except Exception as exc:
            last_error = exc
            print(f"[device-seg] SAM2 load failed for {source}: {exc}", flush=True)
            if args.sam2_checkpoint is not None:
                break
            continue
        print(f"[device-seg] SAM2 ready: {source}", flush=True)
        return segmenter

    print(f"[device-seg] disabled: SAM2 load failed: {last_error}", flush=True)
    return None


def run_device_segmentation(
    frame: FrameData,
    args: argparse.Namespace,
    segmenter: Sam2DeviceSegmenter | None,
    anchors: list[dict] | None = None,
    re_predict: bool = False,
) -> FrameData:
    if args.disable_device_segmentation:
        return frame

    depth = frame.any2full_depth if frame.any2full_depth is not None else frame.aligned_depth

    work_dir = frame.frame_dir
    if work_dir == Path(".") or str(work_dir) == ".":
        cache_root = args.segment_cache_dir
        if not cache_root.is_absolute():
            cache_root = Path.cwd() / cache_root
        work_dir = cache_root / f"network_{int(time.time() * 1000)}"
        work_dir.mkdir(parents=True, exist_ok=True)
        frame.frame_dir = work_dir

    if anchors and segmenter is not None and segmenter.ready:
        rgb_meta = frame.meta["rgb"]
        rgb_intrinsics_arr = rgb_intrinsics(rgb_meta)
        rgb_pose_world = unity_trs(
            np.array(
                [
                    float(rgb_meta["pose_position_x"]),
                    float(rgb_meta["pose_position_y"]),
                    float(rgb_meta["pose_position_z"]),
                ],
                dtype=np.float32,
            ),
            quaternion_to_matrix(
                np.array(
                    [
                        float(rgb_meta["pose_rotation_x"]),
                        float(rgb_meta["pose_rotation_y"]),
                        float(rgb_meta["pose_rotation_z"]),
                        float(rgb_meta["pose_rotation_w"]),
                    ],
                    dtype=np.float32,
                )
            ),
            (1.0, 1.0, 1.0),
        )
        world_pts = np.array([[float(a["x"]), float(a["y"]), float(a["z"])] for a in anchors], dtype=np.float32)
        labels_arr = np.array([1 if int(a["label"]) > 0 else 0 for a in anchors], dtype=np.int32)
        projection = world_to_rgb_pixel(world_pts, rgb_pose_world, rgb_intrinsics_arr, frame.rgb.shape[0], frame.rgb.shape[1])
        if projection is not None:
            pixel_pts, in_frame = projection
            if in_frame.any():
                pixel_pts = pixel_pts[in_frame].astype(np.float32)
                labels_arr = labels_arr[in_frame]
                current_rgb_hash = hash(frame.rgb.tobytes())
                if (not re_predict) or (segmenter._current_rgb_hash != current_rgb_hash):
                    segmenter.reset_for_image(frame.rgb)
                mask = segmenter.re_predict(pixel_pts, labels_arr)
                if mask is not None:
                    contour_3d = extract_world_contour(mask, frame.aligned_depth, rgb_pose_world, rgb_intrinsics_arr)
                    base_overlay = frame.any2full_overlay_rgb if frame.any2full_overlay_rgb is not None else frame.overlay_rgb
                    frame.device_mask = mask
                    frame.device_contour_3d = contour_3d
                    frame.device_overlay_rgb = overlay_device_mask(base_overlay, mask)
                    frame.device_mask_path = work_dir / "device_mask_vr.png"
                    Image.fromarray(mask.astype(np.uint8) * 255).save(frame.device_mask_path)
                    frame.device_info = {
                        "area_px": int(np.count_nonzero(mask)),
                        "bbox_xyxy": None,
                        "contour_3d_points": len(contour_3d),
                        "anchors_used": int(in_frame.sum()),
                    }
                    print(
                        f"[device-seg] vr mask={frame.device_mask_path} area={frame.device_info['area_px']} anchors={frame.device_info['anchors_used']}",
                        flush=True,
                    )
                    return frame

    cursor_payload = frame.meta.get("cursor")
    if cursor_payload is None:
        print("[device-seg] skipped: no cursor_json in trigger payload", flush=True)
        return frame

    prompt = build_cursor_prompt(
        frame.meta,
        cursor_payload,
        depth,
        CursorPromptConfig(nearest_depth_radius_px=args.cursor_nearest_depth_radius_px),
    )
    frame.meta["cursor_prompt"] = prompt

    rgb_path = work_dir / "rgb.png"
    if not rgb_path.exists():
        Image.fromarray(frame.rgb.astype(np.uint8)).save(rgb_path)
    depth_path = work_dir / "device_depth_source.npy"
    prompt_path = work_dir / "cursor_prompt.json"
    cursor_path = work_dir / "cursor.json"
    meta_path = work_dir / "meta.json"
    np.save(depth_path, depth.astype(np.float32))
    prompt_path.write_text(json.dumps(prompt, indent=2), encoding="utf-8")
    cursor_path.write_text(json.dumps(cursor_payload, indent=2), encoding="utf-8")
    meta_path.write_text(json.dumps(frame.meta, indent=2), encoding="utf-8")

    if not prompt.get("valid", False):
        print(f"[device-seg] skipped: invalid cursor prompt ({prompt.get('reason')})", flush=True)
        return frame

    rgbd_prompt_path = work_dir / "cursor_prompt_rgbd.json"
    depth_component_path = work_dir / "device_depth_component_mask.png"
    rgbd_prompt = build_rgbd_device_prompt(
        depth,
        prompt,
        frame.rgb,
        RgbdPromptConfig(
            local_depth_jump_m=args.seg_depth_local_jump_m,
            local_depth_jump_rel=args.seg_depth_local_jump_rel,
            global_depth_span_m=args.seg_depth_global_span_m,
            max_radius_px=args.seg_depth_max_radius_px,
            bbox_pad_px=args.seg_depth_bbox_pad_px,
            max_component_area_ratio=args.seg_depth_max_component_area_ratio,
            rgb_edge_requires_depth_jump=args.seg_depth_ignore_texture_edges,
        ),
    )
    prompt = rgbd_prompt.prompt
    frame.meta["cursor_prompt"] = prompt
    rgbd_prompt_path.write_text(json.dumps(prompt, indent=2), encoding="utf-8")
    Image.fromarray(rgbd_prompt.component_mask.astype(np.uint8) * 255).save(depth_component_path)
    print(
        "[device-seg] rgbd prompt "
        f"valid={prompt.get('rgbd_prompt_valid')} area={prompt.get('rgbd_component_area_px')} "
        f"box={prompt.get('sam_box_xyxy')}",
        flush=True,
    )

    if segmenter is None or not segmenter.ready:
        print("[device-seg] skipped: SAM2 segmenter is not ready", flush=True)
        return frame

    raw_mask_path = work_dir / "device_mask_raw.png"
    raw_overlay_path = work_dir / "device_mask_raw_overlay.png"
    sam_meta_path = work_dir / "device_sam2_meta.json"
    try:
        sam_result = segmenter.segment(frame.rgb, depth, prompt)
        Image.fromarray(sam_result.mask.astype(np.uint8) * 255).save(raw_mask_path)
        Image.fromarray(overlay_sam2_mask(frame.rgb, sam_result.mask)).save(raw_overlay_path)
        sam_info = {
            "score": sam_result.score,
            "sam_score": sam_result.sam_score,
            "depth_consistency": sam_result.depth_consistency,
            "selected_index": sam_result.selected_index,
            "point_coords": sam_result.point_coords.astype(float).tolist(),
            "point_labels": sam_result.point_labels.astype(int).tolist(),
        }
        sam_meta_path.write_text(json.dumps(sam_info, indent=2), encoding="utf-8")

        refined = refine_device_mask(
            frame.rgb,
            depth,
            sam_result.mask,
            prompt,
            RgbdMaskRefineConfig(
                max_global_depth_span_m=args.seg_refine_depth_span_m,
                depth_component_union=args.seg_enable_depth_component_union,
                open_px=args.seg_refine_open_px,
                close_px=args.seg_refine_close_px,
            ),
        )
        info = write_refine_outputs(refined, frame.rgb, depth, work_dir, prefix="device")
    except Exception as exc:
        print(f"[device-seg] failed: {exc}", flush=True)
        return frame

    base_overlay = frame.any2full_overlay_rgb if frame.any2full_overlay_rgb is not None else frame.overlay_rgb
    frame.device_mask = refined.mask
    frame.device_overlay_rgb = overlay_device_mask(base_overlay, refined.mask)
    frame.device_mask_path = Path(info["mask"])
    frame.device_info = {
        **info,
        "prompt": str(prompt_path),
        "rgbd_prompt": str(rgbd_prompt_path),
        "depth_component_mask": str(depth_component_path),
        "raw_mask": str(raw_mask_path),
        "raw_overlay": str(raw_overlay_path),
        "sam2_meta": str(sam_meta_path),
    }
    frame.device_contour_3d = None
    print(
        f"[device-seg] mask={frame.device_mask_path} area={refined.area_px} bbox={refined.bbox_xyxy}",
        flush=True,
    )
    return frame


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


def decode_rgb_payload(
    rgb_payload_bytes: bytes,
    rgb_format: str,
    meta: dict,
) -> np.ndarray:
    if rgb_format == "raw_rgb24":
        rgb_meta = meta["rgb"]
        rgb_w = int(rgb_meta["resolution_w"])
        rgb_h = int(rgb_meta["resolution_h"])
        raw = np.frombuffer(rgb_payload_bytes, dtype=np.uint8)
        expected_rgb24 = rgb_w * rgb_h * 3
        expected_rgba32 = rgb_w * rgb_h * 4
        if raw.size == expected_rgb24:
            # Unity Texture2D raw data arrives bottom-row first; viewer image
            # coordinates and the RGB intrinsics use top-row first.
            return np.flipud(raw.reshape((rgb_h, rgb_w, 3))).copy()
        if raw.size == expected_rgba32:
            return np.flipud(raw.reshape((rgb_h, rgb_w, 4))[:, :, :3]).copy()
        raise ValueError(
            f"rgb_raw payload has {raw.size} bytes, expected {expected_rgb24} RGB24 "
            f"or {expected_rgba32} RGBA32 bytes"
        )

    bgr = cv2.imdecode(np.frombuffer(rgb_payload_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("rgb_jpeg payload could not be decoded")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def load_frame_from_payload(
    rgb_payload_bytes: bytes,
    rgb_format: str,
    depth_raw_bytes: bytes,
    meta_json_str: str,
    min_depth: float,
    max_depth: float,
    cursor_json_str: str | None = None,
) -> FrameData:
    """Load and align a frame from Quest 3 trigger payload bytes."""
    meta = json.loads(meta_json_str)
    if cursor_json_str:
        meta["cursor"] = json.loads(cursor_json_str)
    rgb = decode_rgb_payload(rgb_payload_bytes, rgb_format, meta)

    depth_meta = meta["depth"]
    depth_w = int(depth_meta["resolution_w"])
    depth_h = int(depth_meta["resolution_h"])
    depth_ndc = np.frombuffer(depth_raw_bytes, dtype=np.float32)
    expected = depth_w * depth_h
    if depth_ndc.size != expected:
        raise ValueError(f"depth_raw payload has {depth_ndc.size} floats, expected {expected}")
    depth_ndc = depth_ndc.reshape((depth_h, depth_w))

    depth_m = raw_depth_to_linear_m(depth_ndc, depth_meta)
    aligned, points_rgb_all, _ = align_depth_to_rgb_sdk(depth_m, meta, min_depth, max_depth)
    if not np.any(aligned > 0):
        print("  [WARN] alignment produced no valid depth pixels")

    overlay_rgb = make_depth_overlay(rgb, aligned, min_depth, max_depth)
    cloud_points, cloud_colors = sample_cloud_colors(rgb, points_rgb_all, meta["rgb"])
    return FrameData(
        frame_dir=Path("."),
        meta=meta,
        rgb=rgb,
        depth_ndc=depth_ndc,
        depth_m=depth_m,
        aligned_depth=aligned,
        overlay_rgb=overlay_rgb,
        any2full_depth=None,
        any2full_overlay_rgb=None,
        any2full_path=None,
        device_mask=None,
        device_overlay_rgb=None,
        device_mask_path=None,
        device_info=None,
        device_contour_3d=None,
        cloud_points=cloud_points,
        cloud_colors=cloud_colors,
        projected_depth_count=int(np.count_nonzero(aligned > 0)),
        any2full_depth_count=0,
        alignment_mode="sdk_reprojection",
    )


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

    # Primary: correct GPU zbuffer-param conversion (matches quest3server backend)
    depth_m = raw_depth_to_linear_m(depth_ndc, depth_meta)

    # Feedback: compare against legacy near-derived method
    near_z = float(depth_meta["near_z"])
    far_z = float(depth_meta.get("far_z", math.inf))
    depth_m_legacy = ndc_to_linear_depth_legacy(depth_ndc, near_z, far_z)
    valid_both = (depth_m > 0) & (depth_m_legacy > 0)
    if valid_both.any():
        abs_diff = np.abs(depth_m[valid_both] - depth_m_legacy[valid_both])
        max_err = float(np.max(abs_diff))
        mean_err = float(np.mean(abs_diff))
        # Only report when error is meaningful (> 1 mm)
        if max_err > 0.001:
            print(
                f"  [NDC→linear] legacy depth error vs GPU zbuffer: "
                f"max={max_err:.4f}m, mean={mean_err:.4f}m, "
                f"pixels_compared={int(np.count_nonzero(valid_both))}",
                flush=True,
            )
    else:
        print(f"  [NDC→linear] no valid pixels to compare depth methods")

    if mode == "sdk_reprojection":
        aligned, points_rgb_all, _ = align_depth_to_rgb_sdk(depth_m, meta, min_depth, max_depth)
    elif mode == "screen_space":
        aligned, points_rgb_all, _ = align_depth_to_rgb_screen_space(depth_m, meta, min_depth, max_depth)
    else:
        raise ValueError(f"Unsupported mode: {mode}")
    if aligned is None or not np.any(aligned > 0):
        print(f"  [WARN] {frame_dir.name}: alignment produced no valid depth pixels")

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
        device_mask=None,
        device_overlay_rgb=None,
        device_mask_path=None,
        device_info=None,
        device_contour_3d=None,
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


def _parse_multipart(body: bytes, content_type: str) -> dict[str, bytes]:
    """Parse multipart/form-data body into {field_name: bytes}.
    
    Uses manual boundary parsing — no email.parser dependency.
    Handles the exact format Unity's multipart POST sends.
    """
    # Extract boundary from Content-Type: multipart/form-data; boundary=----WebKit...
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary="):].strip("\"'")
            break
    if not boundary:
        raise ValueError("no boundary in Content-Type")
    
    sep = ("--" + boundary).encode()
    term = ("--" + boundary + "--").encode()
    parts: dict[str, bytes] = {}
    
    # Split body by boundary separator
    sections = body.split(sep)
    for section in sections[1:]:  # skip preamble before first boundary
        if section.startswith(b"--"):  # terminal boundary
            break
        # Each section: \r\n headers \r\n\r\n content \r\n
        section = section.lstrip(b"\r\n")
        header_end = section.find(b"\r\n\r\n")
        if header_end < 0:
            continue
        header_bytes = section[:header_end]
        content = section[header_end + 4:]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        
        # Parse Content-Disposition header for field name
        header_str = header_bytes.decode("utf-8", errors="replace")
        name = None
        for line in header_str.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                for token in line.split(";"):
                    token = token.strip()
                    if token.startswith("name="):
                        name = token[5:].strip("\"'")
                        break
        if name and content:
            parts[name] = content
    
    return parts


def _build_device_contour(frame: FrameData) -> list[dict[str, float]] | None:
    contour = getattr(frame, "device_contour_3d", None)
    if contour is None or len(contour) == 0:
        return None
    return [
        {"x": round(float(point[0]), 4), "y": round(float(point[1]), 4), "z": round(float(point[2]), 4)}
        for point in contour
    ]


class _PayloadHandler(http.server.BaseHTTPRequestHandler):
    viewer_ref: "RgbdViewer | None" = None

    def do_POST(self) -> None:
        if self.path != "/api/track/start-final-rgbd":
            self.send_error(404)
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_error(400, "expected multipart/form-data")
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "invalid Content-Length")
            return
        body = self.rfile.read(content_length)

        try:
            parts = _parse_multipart(body, content_type)
        except Exception as exc:
            self.send_error(400, f"invalid multipart payload: {exc}")
            return

        rgb_raw = parts.get("rgb_raw")
        rgb_jpeg = parts.get("rgb_jpeg")
        depth_raw = parts.get("depth_raw")
        meta_json = parts.get("meta_json")
        cursor_json = parts.get("cursor_json")
        anchor_points_json = parts.get("anchor_points_json")
        re_predict_raw = parts.get("re_predict")
        if rgb_raw is None and rgb_jpeg is None:
            self.send_error(400, "missing RGB field: need rgb_raw or rgb_jpeg")
            return
        if depth_raw is None or meta_json is None:
            self.send_error(400, "missing fields: need depth_raw and meta_json")
            return

        viewer = self.viewer_ref
        if viewer is None:
            self.send_error(503, "viewer not ready")
            return

        anchors = None
        if anchor_points_json is not None:
            anchors = json.loads(anchor_points_json.decode("utf-8"))
        re_predict = re_predict_raw is not None and re_predict_raw.strip().lower() == b"true"

        try:
            frame = load_frame_from_payload(
                rgb_payload_bytes=rgb_raw if rgb_raw is not None else rgb_jpeg,
                rgb_format="raw_rgb24" if rgb_raw is not None else "jpeg",
                depth_raw_bytes=depth_raw,
                meta_json_str=meta_json.decode("utf-8"),
                min_depth=viewer.args.min_depth,
                max_depth=viewer.args.max_depth,
                cursor_json_str=cursor_json.decode("utf-8") if cursor_json is not None else None,
            )
            frame = run_any2full_completion(frame, viewer.args, viewer.any2full_service)
            frame = run_device_segmentation(frame, viewer.args, viewer.device_segmenter, anchors=anchors, re_predict=re_predict)
        except Exception as exc:
            print(f"[server] alignment failed: {exc}", flush=True)
            self.send_error(500, str(exc))
            return

        viewer.root.after(0, lambda: viewer._on_network_frame(frame))
        response = {
            "ok": True,
            "rgb": {
                "width": int(frame.rgb.shape[1]),
                "height": int(frame.rgb.shape[0]),
                "format": "raw_rgb24" if rgb_raw is not None else "jpeg",
            },
            "depth": {
                "width": int(frame.depth_ndc.shape[1]),
                "height": int(frame.depth_ndc.shape[0]),
                "projected_pixels": int(frame.projected_depth_count),
                "any2full_pixels": int(frame.any2full_depth_count),
                "any2full": frame.any2full_path is not None,
            },
            "cursor": frame.meta.get("cursor_prompt", {"valid": False, "reason": "missing"}),
            "device": {
                "segmented": frame.device_mask_path is not None,
                "mask": str(frame.device_mask_path) if frame.device_mask_path is not None else None,
                "area_px": int(frame.device_info.get("area_px", 0)) if frame.device_info else 0,
                "bbox_xyxy": frame.device_info.get("bbox_xyxy") if frame.device_info else None,
                "contour_3d": _build_device_contour(frame),
            },
            "cloud_points": int(frame.cloud_points.shape[0]),
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[server] {format % args}", flush=True)


def _start_server(viewer: "RgbdViewer", host: str = "0.0.0.0", port: int = 8500) -> threading.Thread:
    _PayloadHandler.viewer_ref = viewer
    server = http.server.HTTPServer((host, port), _PayloadHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[server] listening on {host}:{port}", flush=True)
    return thread


class RgbdViewer:
    def __init__(self, args: argparse.Namespace, frames: list[Path]) -> None:
        self.args = args
        self.frames = frames
        self.frame_index = int(np.clip(args.frame, 0, len(frames) - 1)) if frames else 0
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
        self._network_frame_count = 0
        self._current_frame_name = "network"
        self.any2full_service: Any2FullService | None = None
        self.device_segmenter: Sam2DeviceSegmenter | None = None

        self.root = tk.Tk()
        self.root.title("Quest 3 RGB-D Alignment Viewer")
        self.root.geometry(f"{max(args.view_size, args.cloud_width) + 80}x{args.cloud_height + 150}")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        if self.args.server and not self.args.disable_any2full:
            self.any2full_service = Any2FullService(self.args)
            if not self.any2full_service.start():
                self.any2full_service = None
        if self.args.server and not self.args.disable_device_segmentation:
            self.device_segmenter = create_device_segmenter(self.args)
        if self.frames:
            self.load_current_frame()
        else:
            self.update_rgb_image()
            self.render_cloud()
            self.update_status()

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
        if not self.frames:
            self.frame = None
            self._nearest_cache = {}
            self._last_hover_display_xy = None
            self.frame_var.set("")
            self.update_rgb_image()
            self.render_cloud()
            self.update_status()
            return
        self.frame = load_frame(
            self.frames[self.frame_index],
            self.args.min_depth,
            self.args.max_depth,
            self.args.mode,
            self.args.depth_origin,
        )
        self._current_frame_name = self.frames[self.frame_index].name
        self._nearest_cache = {}
        self._last_hover_display_xy = None
        self.frame_var.set(self._current_frame_name)
        self.yaw = -0.65
        self.pitch = -0.28
        self.zoom = 1.8
        self.update_rgb_image()
        self.render_cloud()
        self.update_status()

    def _on_network_frame(self, frame: FrameData) -> None:
        self._network_frame_count += 1
        self._current_frame_name = f"network_{self._network_frame_count:06d}"
        self.frame_var.set(self._current_frame_name)
        self.frame = frame
        self._nearest_cache = {}
        self._last_hover_display_xy = None
        self.update_rgb_image()
        self.render_cloud()
        self.update_status()

    def update_status(self) -> None:
        if self.frame is None:
            waiting = "Waiting for trigger payload..." if self.args.server else "No local frames found"
            self.status_var.set(waiting)
            self.cloud_var.set(waiting)
            self.clear_hover_panel()
            return
        rgb = self.frame.meta["rgb"]
        depth = self.frame.meta["depth"]
        active_name = self.get_active_depth_source_name()
        active_depth = self.get_active_depth_map()
        exact = int(np.count_nonzero(active_depth > 0))
        cloud = self.frame.cloud_points.shape[0]
        origin_x, origin_y = self.resolve_origin_xy()
        any2full_note = self.frame.any2full_path.name if self.frame.any2full_path is not None else "missing"
        device_note = self.frame.device_mask_path.name if self.frame.device_mask_path is not None else "missing"
        cursor_note = self.get_cursor_status_note()
        self.status_var.set(
            f"{self._current_frame_name} | RGB {rgb['resolution_w']}x{rgb['resolution_h']} | "
            f"depth {depth['resolution_w']}x{depth['resolution_h']} | "
            f"row-order {self.args.depth_origin} | preview {active_name} | origin ({origin_x}, {origin_y}) | "
            f"projected {exact} | cloud {cloud} | any2full {any2full_note} | device {device_note} | cursor {cursor_note}"
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
        if self.frame.device_overlay_rgb is not None:
            return self.frame.device_overlay_rgb
        if self.get_active_depth_source_name() == "any2full" and self.frame.any2full_overlay_rgb is not None:
            return self.frame.any2full_overlay_rgb
        return self.frame.overlay_rgb

    def get_cursor_prompt(self) -> dict | None:
        if self.frame is None:
            return None
        prompt = self.frame.meta.get("cursor_prompt")
        return prompt if isinstance(prompt, dict) else None

    def get_cursor_status_note(self) -> str:
        prompt = self.get_cursor_prompt()
        if prompt is None:
            return "missing"
        if prompt.get("valid", False):
            x = prompt.get("rgb_x", "?")
            y = prompt.get("rgb_y", "?")
            return f"ok({x},{y})"
        return str(prompt.get("reason", "invalid"))

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
        if self.frame is None:
            self.rgb_scale = 1.0
            self.rgb_photo = None
            self.rgb_canvas.config(width=self.args.view_size, height=self.args.view_size)
            self.rgb_canvas.delete("all")
            self.rgb_canvas.create_text(
                self.args.view_size // 2,
                self.args.view_size // 2,
                text="Waiting for trigger payload...",
                fill="#ffffff",
                font=("Consolas", 18),
                anchor=tk.CENTER,
            )
            return
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
        self.draw_cursor_prompt_overlay(pil.width, pil.height)
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

    def draw_cursor_prompt_overlay(self, canvas_w: int, canvas_h: int) -> None:
        prompt = self.get_cursor_prompt()
        if prompt is None or not prompt.get("valid", False):
            return
        try:
            rgb_x = float(prompt["rgb_x"])
            rgb_y = float(prompt["rgb_y"])
        except (KeyError, TypeError, ValueError):
            return
        assert self.frame is not None
        rgb_h, rgb_w = self.frame.rgb.shape[:2]
        if rgb_x < 0 or rgb_x >= rgb_w or rgb_y < 0 or rgb_y >= rgb_h:
            return

        x = int(round(rgb_x * self.rgb_scale))
        y = int(round(rgb_y * self.rgb_scale))
        radius = 13
        arm = 21
        x = int(np.clip(x, 0, max(0, canvas_w - 1)))
        y = int(np.clip(y, 0, max(0, canvas_h - 1)))

        self.rgb_canvas.create_oval(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            outline="#ffffff",
            width=4,
        )
        self.rgb_canvas.create_oval(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            outline="#ffd400",
            width=2,
        )
        self.rgb_canvas.create_line(x - arm, y, x + arm, y, fill="#ffd400", width=2)
        self.rgb_canvas.create_line(x, y - arm, x, y + arm, fill="#ffd400", width=2)

        depth_text = ""
        depth_m = prompt.get("depth_sample_m", prompt.get("rgb_camera_z_m"))
        if depth_m is not None:
            try:
                depth_text = f" {float(depth_m):.2f}m"
            except (TypeError, ValueError):
                depth_text = ""
        label = f"Q3 cursor{depth_text}"
        tx = min(max(x + 18, 8), max(8, canvas_w - 150))
        ty = min(max(y - 28, 8), max(8, canvas_h - 24))
        self.rgb_canvas.create_rectangle(
            tx - 5,
            ty - 3,
            tx + 142,
            ty + 20,
            fill="#101010",
            outline="#ffd400",
            stipple="gray75",
        )
        self.rgb_canvas.create_text(
            tx,
            ty,
            text=label,
            fill="#ffd400",
            anchor=tk.NW,
            font=("Consolas", 10, "bold"),
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
        if self.frame is None:
            self.depth_var.set("Waiting for trigger payload...")
            return
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
        if self.frame is None:
            img = render_cloud_image(
                np.empty((0, 3), dtype=np.float32),
                np.empty((0, 3), dtype=np.uint8),
                {},
                self.args.cloud_width,
                self.args.cloud_height,
                self.yaw,
                self.pitch,
                self.zoom,
            )
        else:
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
        if not self.frames:
            return
        selected = self.frame_combo.current()
        if selected >= 0:
            self.frame_index = selected
            self.load_current_frame()

    def prev_frame(self) -> None:
        if not self.frames:
            return
        self.frame_index = (self.frame_index - 1) % len(self.frames)
        self.load_current_frame()

    def next_frame(self) -> None:
        if not self.frames:
            return
        self.frame_index = (self.frame_index + 1) % len(self.frames)
        self.load_current_frame()

    def run(self) -> None:
        self.root.mainloop()

    def on_close(self) -> None:
        if self.any2full_service is not None:
            self.any2full_service.stop()
        self.root.destroy()


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
            f"pose_source={depth.get('pose_source', 'unknown')}"
        )


def main() -> None:
    args = parse_args()
    data_root = args.data or DEFAULT_DATA_DIR
    frames = [] if args.server else discover_frames(data_root)
    if args.export_dir is not None:
        export_overlays(frames, args)
    if args.export_ply_dir is not None:
        export_point_clouds(frames, args)
    if args.no_ui:
        print_stats(frames, args)
        return
    viewer = RgbdViewer(args, frames)
    if args.server:
        _start_server(viewer, host=args.host, port=args.port)
    viewer.run()


if __name__ == "__main__":
    main()
