from __future__ import annotations

import argparse
import base64
import http.server
import json
import math
import io
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import scrolledtext
from tkinter import ttk

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from discover_client.pairing import (
    build_pairing_prompt,
    normalize_visual_profile,
    parse_json_object,
    score_candidates,
)
from discover_client.config import (
    SCHEMAS as DISCOVER_SOURCE_SCHEMAS,
    load_config as load_discover_config,
    save_config as save_discover_config,
    save_config_text as save_discover_config_text,
)
from discover_client.runtime import DiscoverRuntime
from discover_client.source import SourceConfig
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
from rgb_guided_depth_postprocess import RgbGuidedPostprocessConfig, confidence_overlay, postprocess_depth
from rgb_edge_depth_refine import EdgeDepthRefineConfig, refine_depth_anchors


DEFAULT_DATA_DIR = Path("E:/test/rgbd-v10/rgbd_test")
DEFAULT_ANY2FULL_ROOT = Path("D:/FromGithub/Any2Full")
DEFAULT_ROOM_STORE_ROOT = BACKEND_ROOT / "viewer_room_store"
DEFAULT_ANY2FULL_CACHE_ROOT = BACKEND_ROOT / "viewer_any2full_cache"
DEFAULT_SEGMENT_CACHE_ROOT = BACKEND_ROOT / "viewer_device_segments"
_ATOMIC_WRITE_LOCKS: dict[str, threading.RLock] = {}
_ATOMIC_WRITE_LOCKS_GUARD = threading.Lock()


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
    cloud_points: np.ndarray
    cloud_colors: np.ndarray
    projected_depth_count: int
    any2full_depth_count: int
    alignment_mode: str


def _atomic_write_json(path: Path, payload: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_key = str(path.resolve()).casefold()
    with _ATOMIC_WRITE_LOCKS_GUARD:
        lock = _ATOMIC_WRITE_LOCKS.setdefault(lock_key, threading.RLock())

    body = json.dumps(payload, indent=2, ensure_ascii=False)
    last_error: PermissionError | None = None
    with lock:
        for attempt in range(7):
            tmp = path.with_name(
                f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(body)
                os.replace(tmp, path)
                return
            except PermissionError as exc:
                last_error = exc
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(0.025 * (2**attempt))
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

    assert last_error is not None
    raise last_error


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
        "--any2full-startup-timeout",
        type=float,
        default=120.0,
        help="Maximum seconds to wait for the background Any2Full worker to become ready.",
    )
    parser.add_argument(
        "--any2full-cache-dir",
        type=Path,
        default=DEFAULT_ANY2FULL_CACHE_ROOT,
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
    parser.add_argument("--segment-cache-dir", type=Path, default=DEFAULT_SEGMENT_CACHE_ROOT)
    parser.add_argument(
        "--room-store-dir",
        type=Path,
        default=DEFAULT_ROOM_STORE_ROOT,
        help="Room/device/object capture store used by Quest point prompts.",
    )
    parser.add_argument(
        "--disable-discovery",
        action="store_true",
        help="Do not start the integrated Discover runtime.",
    )
    parser.add_argument(
        "--discover-config",
        type=Path,
        default=BACKEND_ROOT / "discover_client" / "config.toml",
        help="Discover source configuration loaded by the integrated runtime.",
    )
    parser.add_argument(
        "--pairing-max-candidates",
        type=int,
        default=50,
        help="Maximum shortlisted network devices reviewed by the LLM and retained in the saved pairing result.",
    )
    parser.add_argument("--vlm-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--point-debounce-ms", type=int, default=250)
    parser.add_argument("--point-debounce-world-m", type=float, default=0.02)
    parser.add_argument("--point-delete-world-m", type=float, default=0.08)
    parser.add_argument("--image-match-depth-abs-m", type=float, default=0.08)
    parser.add_argument("--image-match-depth-rel", type=float, default=0.06)
    parser.add_argument(
        "--image-match-depth-source",
        choices=("sparse", "any2full"),
        default="sparse",
        help="Depth source for deciding whether a world point is visible in a saved image. Sparse is stricter and avoids reusing occluded 2D views.",
    )
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
        self.starting = False
        self.device = "unknown"
        self.start_error = ""

    def start(self) -> bool:
        if self.args.disable_any2full:
            return False
        self.starting = True
        self.start_error = ""
        any2full_root = self.args.any2full_root.resolve()
        script = any2full_root / "any2full_infer.py"
        worker_script = Path(__file__).resolve().parent / "any2full_worker.py"
        checkpoint = resolve_any2full_checkpoint(any2full_root, self.args.any2full_checkpoint).resolve()
        python_exe = resolve_any2full_python(any2full_root, self.args.any2full_python).resolve()
        if not script.exists():
            print(f"[any2full] disabled: script not found: {script}", flush=True)
            self.starting = False
            return False
        if not worker_script.exists():
            print(f"[any2full] disabled: worker not found: {worker_script}", flush=True)
            self.starting = False
            return False
        if not checkpoint.exists():
            print(f"[any2full] disabled: checkpoint not found: {checkpoint}", flush=True)
            self.starting = False
            return False
        if not python_exe.exists():
            print(f"[any2full] disabled: python not found: {python_exe}", flush=True)
            self.starting = False
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
        ready_lines: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_ready_line() -> None:
            assert self.process is not None and self.process.stdout is not None
            ready_lines.put(self.process.stdout.readline())

        threading.Thread(target=read_ready_line, name="Any2FullReady", daemon=True).start()
        try:
            ready_line = ready_lines.get(
                timeout=max(1.0, float(self.args.any2full_startup_timeout))
            )
        except queue.Empty:
            self.start_error = (
                f"worker startup timed out after {self.args.any2full_startup_timeout:.0f}s"
            )
            print(f"[any2full] {self.start_error}", flush=True)
            self.stop()
            self.starting = False
            return False
        if not ready_line:
            print("[any2full] worker exited before ready", flush=True)
            self.start_error = "worker exited before ready"
            self.stop()
            self.starting = False
            return False
        try:
            ready = json.loads(ready_line)
        except json.JSONDecodeError:
            print(f"[any2full] invalid worker ready line: {ready_line!r}", flush=True)
            self.start_error = "invalid worker ready response"
            self.stop()
            self.starting = False
            return False
        if not ready.get("ready"):
            print(f"[any2full] worker not ready: {ready}", flush=True)
            self.start_error = str(ready.get("error") or "worker not ready")
            self.stop()
            self.starting = False
            return False
        self.ready = True
        self.starting = False
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
        self.starting = False
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

    if service is not None:
        if not service.ready:
            print("[any2full] worker is still loading; skipping completion for this frame", flush=True)
            return frame
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
) -> FrameData:
    if args.disable_device_segmentation:
        return frame

    cursor_payload = frame.meta.get("cursor")
    if cursor_payload is None:
        print("[device-seg] skipped: no cursor_json in trigger payload", flush=True)
        return frame

    depth = frame.any2full_depth if frame.any2full_depth is not None else frame.aligned_depth
    prompt = frame.meta.get("cursor_prompt")
    if not isinstance(prompt, dict) or not prompt.get("valid", False):
        prompt = build_cursor_prompt(
            frame.meta,
            cursor_payload,
            depth,
            CursorPromptConfig(nearest_depth_radius_px=args.cursor_nearest_depth_radius_px),
        )
    frame.meta["cursor_prompt"] = prompt

    work_dir = frame.frame_dir
    if work_dir == Path(".") or str(work_dir) == ".":
        cache_root = args.segment_cache_dir
        if not cache_root.is_absolute():
            cache_root = Path.cwd() / cache_root
        work_dir = cache_root / f"network_{int(time.time() * 1000)}"
        work_dir.mkdir(parents=True, exist_ok=True)
        frame.frame_dir = work_dir

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
    explicit_labels = prompt.get("sam_point_labels") or prompt.get("point_labels") or []
    if isinstance(explicit_labels, list) and explicit_labels and not any(int(label) > 0 for label in explicit_labels):
        print("[device-seg] skipped: prompt has no positive point", flush=True)
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
        f"box={prompt.get('sam_box_xyxy')} "
        f"points=+{prompt.get('positive_point_count', '?')}/-{prompt.get('negative_point_count', '?')} "
        f"user_only={prompt.get('rgbd_prompt_user_points_only', False)}",
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


_SAFE_PATH_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_path_component(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    text = _SAFE_PATH_RE.sub("_", text).strip("._")
    return text or fallback


def _room_context(cursor: dict) -> dict:
    room_raw = cursor.get("room_id") or cursor.get("room_name") or "room_default"
    device_raw = cursor.get("device_id") or cursor.get("device_name") or "device_default"
    object_raw = cursor.get("object_session_id") or cursor.get("object_id") or "object_default"
    return {
        "room_id": _safe_path_component(room_raw, "room_default"),
        "room_name": str(cursor.get("room_name") or room_raw),
        "device_id": _safe_path_component(device_raw, "device_default"),
        "device_name": str(cursor.get("device_name") or device_raw),
        "device_model": str(cursor.get("device_model") or ""),
        "object_id": _safe_path_component(object_raw, "object_default"),
    }


def _room_store_root(args: argparse.Namespace) -> Path:
    root = args.room_store_dir
    return (root if root.is_absolute() else Path.cwd() / root).resolve()


def _vlm_config_path(args: argparse.Namespace) -> Path:
    return _room_store_root(args) / "vlm_config.json"


def _legacy_vlm_config_paths(args: argparse.Namespace) -> list[Path]:
    if _room_store_root(args) != DEFAULT_ROOM_STORE_ROOT.resolve():
        return []
    return [
        Path(__file__).resolve().parent / "viewer_room_store" / "vlm_config.json",
    ]


def _load_vlm_config(args: argparse.Namespace) -> dict:
    path = _vlm_config_path(args)
    if not path.exists():
        for legacy_path in _legacy_vlm_config_paths(args):
            if not legacy_path.exists() or legacy_path.resolve() == path.resolve():
                continue
            try:
                legacy_config = json.loads(legacy_path.read_text(encoding="utf-8"))
                if isinstance(legacy_config, dict):
                    _atomic_write_json(path, legacy_config)
                    print(f"[vlm] migrated config from {legacy_path} to {path}", flush=True)
                    break
            except Exception as exc:
                print(f"[vlm] failed to migrate config from {legacy_path}: {exc}", flush=True)
        if not path.exists():
            return {
                "base_url": "",
                "token": "",
                "model": "",
                "tested_at_ms": 0,
                "saved_at_ms": 0,
            }
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        config = {}
    if not isinstance(config, dict):
        config = {}
    config.setdefault("base_url", "")
    config.setdefault("token", "")
    config.setdefault("model", "")
    config.setdefault("tested_at_ms", 0)
    config.setdefault("saved_at_ms", 0)
    return config


def _save_vlm_config(args: argparse.Namespace, config: dict) -> None:
    path = _vlm_config_path(args)
    payload = {
        "base_url": str(config.get("base_url") or "").strip(),
        "token": str(config.get("token") or "").strip(),
        "model": str(config.get("model") or "").strip(),
        "tested_at_ms": int(config.get("tested_at_ms") or 0),
        "saved_at_ms": _now_ms(),
    }
    _atomic_write_json(path, payload)


def _vlm_api_url(base_url: str, suffix: str) -> str:
    root = str(base_url or "").strip().rstrip("/")
    if not root:
        raise ValueError("missing VLM base URL")
    if root.endswith("/v1"):
        root = root[:-3]
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return root + "/v1" + suffix


def _vlm_json_request(method: str, url: str, token: str, payload: dict | None = None, timeout: float = 60.0) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method.upper())
    request.add_header("Accept", "application/json")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    token = str(token or "").strip()
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout))) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}") from exc
    return json.loads(raw.decode("utf-8"))


def _vlm_list_models(config: dict, timeout: float = 60.0) -> list[str]:
    url = _vlm_api_url(str(config.get("base_url") or ""), "/models")
    data = _vlm_json_request("GET", url, str(config.get("token") or ""), timeout=timeout)
    models: list[str] = []
    for item in data.get("data", []):
        if isinstance(item, dict) and item.get("id"):
            models.append(str(item["id"]))
    return models


def _vlm_chat_completion(config: dict, messages: list[dict], timeout: float = 120.0, max_tokens: int = 1200) -> str:
    model = str(config.get("model") or "").strip()
    if not model:
        raise ValueError("missing VLM model")
    url = _vlm_api_url(str(config.get("base_url") or ""), "/chat/completions")
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    data = _vlm_json_request("POST", url, str(config.get("token") or ""), payload=payload, timeout=timeout)
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content", "")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("text"):
                parts.append(str(part["text"]))
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _vlm_test_connection(config: dict, timeout: float = 60.0) -> tuple[list[str], str]:
    models = _vlm_list_models(config, timeout=timeout)
    model = str(config.get("model") or "").strip()
    if model:
        response = _vlm_chat_completion(
            config,
            [{"role": "user", "content": "Reply with exactly: Smart Room VLM OK"}],
            timeout=timeout,
            max_tokens=32,
        )
    else:
        response = "model list ok"
    return models, response


def _room_device_paths(args: argparse.Namespace, cursor: dict) -> tuple[dict, Path, Path]:
    ctx = _room_context(cursor)
    device_dir = _room_store_root(args) / ctx["room_id"] / ctx["device_id"]
    return ctx, device_dir, device_dir / "points.json"


def _load_point_store(args: argparse.Namespace, cursor: dict) -> tuple[dict, Path, dict]:
    ctx, device_dir, points_path = _room_device_paths(args, cursor)
    if points_path.exists():
        try:
            store = json.loads(points_path.read_text(encoding="utf-8"))
        except Exception:
            store = {}
    else:
        store = {}
    store.setdefault("schema_version", 1)
    store["room_id"] = ctx["room_id"]
    store["room_name"] = ctx["room_name"]
    store["device_id"] = ctx["device_id"]
    store["device_name"] = ctx["device_name"]
    store["device_model"] = ctx["device_model"]
    store["active_object_id"] = ctx["object_id"]
    store.setdefault("images", [])
    store.setdefault("points", [])
    return ctx, device_dir, store


def _save_point_store(device_dir: Path, store: dict) -> None:
    _atomic_write_json(device_dir / "points.json", store)


def _resolve_store_path(device_dir: Path, value: object) -> Path:
    return device_dir / str(value or "")


def _load_room_image_depth(device_dir: Path, image_record: dict, source: str = "any2full") -> np.ndarray | None:
    capture_dir = _resolve_store_path(device_dir, image_record.get("capture_dir"))
    if source == "sparse":
        names = ("aligned_depth.npy",)
    else:
        names = ("dense_depth_any2full.npy", "aligned_depth.npy")
    for name in names:
        path = capture_dir / name
        if path.exists():
            try:
                depth = np.load(path).astype(np.float32, copy=False)
            except Exception as exc:
                print(f"[room-store] failed to load depth {path}: {exc}", flush=True)
                continue
            if depth.ndim == 2:
                return depth
    return None


def _load_room_match_depth(args: argparse.Namespace, device_dir: Path, image_record: dict) -> np.ndarray | None:
    source = str(getattr(args, "image_match_depth_source", "sparse") or "sparse")
    return _load_room_image_depth(device_dir, image_record, source=source)


def _prompt_depth_match(args: argparse.Namespace, prompt: dict, require_depth: bool = True) -> bool:
    if not prompt.get("valid", False):
        return False
    sampled = prompt.get("depth_sample_m")
    camera_z = prompt.get("rgb_camera_z_m")
    if sampled is None or camera_z is None:
        return not require_depth
    try:
        sampled_f = float(sampled)
        camera_z_f = float(camera_z)
    except (TypeError, ValueError):
        return not require_depth
    if not np.isfinite(sampled_f) or not np.isfinite(camera_z_f) or sampled_f <= 0.0 or camera_z_f <= 0.0:
        return not require_depth
    threshold = max(float(args.image_match_depth_abs_m), abs(camera_z_f) * float(args.image_match_depth_rel))
    diff = abs(sampled_f - camera_z_f)
    if diff <= threshold:
        prompt["image_match_depth_diff_m"] = float(diff)
        prompt["image_match_depth_threshold_m"] = float(threshold)
        return True
    prompt["reason"] = "depth_mismatch"
    prompt["image_match_depth_diff_m"] = float(diff)
    prompt["image_match_depth_threshold_m"] = float(threshold)
    return False


def _next_image_id(store: dict) -> str:
    max_index = 0
    for image in store.get("images", []):
        raw = str(image.get("image_id", ""))
        match = re.search(r"(\d+)$", raw)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return f"pic_{max_index + 1:06d}"


def _create_room_capture(
    frame: FrameData,
    viewer: "RgbdViewer",
    rgb_payload_bytes: bytes,
    rgb_format: str,
    depth_raw_bytes: bytes,
    meta_json_bytes: bytes,
    cursor: dict,
) -> tuple[dict, Path, dict, dict]:
    ctx, device_dir, store = _load_point_store(viewer.args, cursor)
    image_id = _next_image_id(store)
    capture_dir = device_dir / image_id
    capture_dir.mkdir(parents=True, exist_ok=True)

    (capture_dir / "meta.json").write_bytes(meta_json_bytes)
    (capture_dir / "depth.raw").write_bytes(depth_raw_bytes)
    if rgb_format == "raw_rgb24":
        (capture_dir / "rgb.raw").write_bytes(rgb_payload_bytes)
        Image.fromarray(frame.rgb.astype(np.uint8)).save(capture_dir / "rgb.jpg", quality=95)
    else:
        (capture_dir / "rgb.jpg").write_bytes(rgb_payload_bytes)
    Image.fromarray(frame.rgb.astype(np.uint8)).save(device_dir / f"{image_id}.png")
    np.save(capture_dir / "aligned_depth.npy", frame.aligned_depth.astype(np.float32))

    record = {
        "image_id": image_id,
        "object_id": ctx["object_id"],
        "created_at_ms": _now_ms(),
        "rgb_png": f"{image_id}.png",
        "capture_dir": image_id,
        "meta_json": f"{image_id}/meta.json",
        "rgb_jpg": f"{image_id}/rgb.jpg",
        "depth_raw": f"{image_id}/depth.raw",
    }
    if rgb_format == "raw_rgb24":
        record["rgb_raw"] = f"{image_id}/rgb.raw"
    store["images"].append(record)
    _save_point_store(device_dir, store)
    frame.frame_dir = capture_dir
    return ctx, device_dir, store, record


def _finalize_room_capture_frame(frame: FrameData, capture_dir: Path) -> None:
    if frame.any2full_depth is not None:
        dense_path = capture_dir / "dense_depth_any2full.npy"
        np.save(dense_path, frame.any2full_depth.astype(np.float32))
        frame.any2full_path = dense_path
    frame.frame_dir = capture_dir
    (capture_dir / "meta.json").write_text(json.dumps(frame.meta, indent=2), encoding="utf-8")


def _project_cursor_for_image(
    args: argparse.Namespace,
    device_dir: Path,
    image_record: dict,
    cursor: dict,
    depth: np.ndarray | None = None,
) -> dict:
    meta_path = _resolve_store_path(device_dir, image_record.get("meta_json"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return build_cursor_prompt(
        meta,
        cursor,
        depth,
        CursorPromptConfig(nearest_depth_radius_px=args.cursor_nearest_depth_radius_px),
    )


def _find_matching_room_image(
    viewer: "RgbdViewer",
    cursor: dict,
) -> tuple[dict, Path, dict, dict | None, dict | None]:
    ctx, device_dir, store = _load_point_store(viewer.args, cursor)
    if bool(cursor.get("force_new_capture", False)):
        return ctx, device_dir, store, None, None
    object_id = ctx["object_id"]
    for image in reversed(store.get("images", [])):
        if str(image.get("object_id") or "object_default") != object_id:
            continue
        try:
            depth = _load_room_match_depth(viewer.args, device_dir, image)
            prompt = _project_cursor_for_image(viewer.args, device_dir, image, cursor, depth)
        except Exception as exc:
            print(f"[room-store] image projection failed for {image.get('image_id')}: {exc}", flush=True)
            continue
        if _prompt_depth_match(viewer.args, prompt, require_depth=depth is not None):
            return ctx, device_dir, store, image, prompt
    return ctx, device_dir, store, None, None


def _project_cursor_to_visible_object_images(
    viewer: "RgbdViewer",
    device_dir: Path,
    store: dict,
    cursor: dict,
    object_id: str,
) -> list[tuple[dict, dict]]:
    matches: list[tuple[dict, dict]] = []
    for image in store.get("images", []):
        if str(image.get("object_id") or "object_default") != object_id:
            continue
        try:
            depth = _load_room_match_depth(viewer.args, device_dir, image)
            prompt = _project_cursor_for_image(viewer.args, device_dir, image, cursor, depth)
        except Exception as exc:
            print(f"[room-store] visible projection failed for {image.get('image_id')}: {exc}", flush=True)
            continue
        if _prompt_depth_match(viewer.args, prompt, require_depth=depth is not None):
            matches.append((image, prompt))
    return matches


def _point_world_xyz(cursor: dict, prompt: dict) -> list[float]:
    if isinstance(prompt.get("world_xyz_m"), list) and len(prompt["world_xyz_m"]) == 3:
        return [float(v) for v in prompt["world_xyz_m"]]
    return [
        float(cursor.get("hit_world_x", 0.0)),
        float(cursor.get("hit_world_y", 0.0)),
        float(cursor.get("hit_world_z", 0.0)),
    ]


def _is_duplicate_point(args: argparse.Namespace, store: dict, point: dict) -> bool:
    debounce_ms = max(0, int(args.point_debounce_ms))
    debounce_world = max(0.0, float(args.point_debounce_world_m))
    if debounce_ms <= 0 and debounce_world <= 0:
        return False
    ts = int(point.get("timestamp_ms") or 0)
    world = np.asarray(point.get("world_xyz_m") or [0.0, 0.0, 0.0], dtype=np.float32)
    for prev in reversed(store.get("points", [])):
        if prev.get("image_id") != point.get("image_id"):
            continue
        if prev.get("object_id") != point.get("object_id"):
            continue
        if int(prev.get("label", 1)) != int(point.get("label", 1)):
            continue
        prev_ts = int(prev.get("timestamp_ms") or 0)
        prev_world = np.asarray(prev.get("world_xyz_m") or [0.0, 0.0, 0.0], dtype=np.float32)
        world_close = debounce_world > 0 and float(np.linalg.norm(world - prev_world)) <= debounce_world
        time_close = debounce_ms > 0 and ts > 0 and prev_ts > 0 and abs(ts - prev_ts) <= debounce_ms
        if world_close or (debounce_world <= 0 and time_close):
            return True
    return False


def _append_room_point(
    args: argparse.Namespace,
    device_dir: Path,
    store: dict,
    image_record: dict,
    cursor: dict,
    prompt: dict,
) -> tuple[bool, dict]:
    label = 1 if int(cursor.get("label", cursor.get("point_label", 1))) > 0 else 0
    timestamp_ms = int(cursor.get("timestamp_ms") or _now_ms())
    logical_point_id = str(cursor.get("logical_point_id") or "").strip()
    point = {
        "point_id": f"pt_{timestamp_ms}_{len(store.get('points', [])) + 1:04d}",
        "image_id": image_record["image_id"],
        "object_id": image_record.get("object_id") or store.get("active_object_id") or "object_default",
        "label": label,
        "mode": cursor.get("mode") or ("add" if label > 0 else "del"),
        "rgb_xy": [int(prompt["rgb_x"]), int(prompt["rgb_y"])],
        "world_xyz_m": _point_world_xyz(cursor, prompt),
        "timestamp_ms": timestamp_ms,
        "rgb_camera_z_m": float(prompt.get("rgb_camera_z_m", 0.0)),
    }
    if logical_point_id:
        point["logical_point_id"] = logical_point_id
    if _is_duplicate_point(args, store, point):
        return False, point
    if not logical_point_id:
        point["logical_point_id"] = point["point_id"]
    store.setdefault("points", []).append(point)
    image_record["updated_at_ms"] = timestamp_ms
    _save_point_store(device_dir, store)
    return True, point


def _cursor_for_stored_point(point: dict, fallback_cursor: dict) -> dict:
    cursor = dict(fallback_cursor)
    world = point.get("world_xyz_m")
    if isinstance(world, list) and len(world) == 3:
        cursor["hit_world_x"] = float(world[0])
        cursor["hit_world_y"] = float(world[1])
        cursor["hit_world_z"] = float(world[2])
    cursor["is_hitting"] = True
    cursor["label"] = 1 if int(point.get("label", 1)) > 0 else 0
    cursor["mode"] = point.get("mode") or ("add" if int(point.get("label", 1)) > 0 else "del")
    cursor["timestamp_ms"] = int(cursor.get("timestamp_ms") or _now_ms())
    return cursor


def _preseed_visible_points_for_new_image(
    args: argparse.Namespace,
    device_dir: Path,
    store: dict,
    image_record: dict,
    frame: FrameData,
    fallback_cursor: dict,
) -> int:
    object_id = image_record.get("object_id") or store.get("active_object_id") or "object_default"
    depth = frame.aligned_depth if str(getattr(args, "image_match_depth_source", "sparse") or "sparse") == "sparse" else (
        frame.any2full_depth if frame.any2full_depth is not None else frame.aligned_depth
    )
    existing_source_ids = {
        str(point.get("source_point_id") or point.get("point_id"))
        for point in store.get("points", [])
        if point.get("image_id") == image_record.get("image_id")
    }
    source_points: list[dict] = []
    seen_source_ids: set[str] = set()
    for point in store.get("points", []):
        if point.get("image_id") == image_record.get("image_id"):
            continue
        if point.get("object_id") != object_id:
            continue
        if not isinstance(point.get("world_xyz_m"), list):
            continue
        source_id = str(point.get("source_point_id") or point.get("point_id"))
        if source_id in seen_source_ids or source_id in existing_source_ids:
            continue
        seen_source_ids.add(source_id)
        source_points.append(point)

    added_count = 0
    for source in source_points:
        cursor = _cursor_for_stored_point(source, fallback_cursor)
        prompt = build_cursor_prompt(
            frame.meta,
            cursor,
            depth,
            CursorPromptConfig(nearest_depth_radius_px=args.cursor_nearest_depth_radius_px),
        )
        if not _prompt_depth_match(args, prompt, require_depth=depth is not None):
            continue

        timestamp_ms = _now_ms()
        point = {
            "point_id": f"pt_{timestamp_ms}_{len(store.get('points', [])) + 1:04d}",
            "image_id": image_record["image_id"],
            "object_id": object_id,
            "label": 1 if int(source.get("label", 1)) > 0 else 0,
            "mode": source.get("mode") or ("add" if int(source.get("label", 1)) > 0 else "del"),
            "rgb_xy": [int(prompt["rgb_x"]), int(prompt["rgb_y"])],
            "world_xyz_m": _point_world_xyz(cursor, prompt),
            "timestamp_ms": timestamp_ms,
            "rgb_camera_z_m": float(prompt.get("rgb_camera_z_m", 0.0)),
            "source_point_id": str(source.get("source_point_id") or source.get("point_id")),
            "source_image_id": str(source.get("image_id") or ""),
            "preseeded": True,
        }
        point["logical_point_id"] = str(source.get("logical_point_id") or source.get("source_point_id") or source.get("point_id"))
        if _is_duplicate_point(args, store, point):
            continue
        store.setdefault("points", []).append(point)
        added_count += 1

    if added_count:
        image_record["preseeded_point_count"] = int(image_record.get("preseeded_point_count", 0)) + added_count
        image_record["updated_at_ms"] = _now_ms()
        _save_point_store(device_dir, store)
    return added_count


def _remove_room_points_near_world(
    args: argparse.Namespace,
    store: dict,
    cursor: dict,
) -> list[dict]:
    object_id = _room_context(cursor)["object_id"]
    try:
        target = np.asarray(
            [
                float(cursor["hit_world_x"]),
                float(cursor["hit_world_y"]),
                float(cursor["hit_world_z"]),
            ],
            dtype=np.float32,
        )
    except (KeyError, TypeError, ValueError):
        return []

    max_distance = max(0.0, float(args.point_delete_world_m))
    best_index = -1
    best_distance = float("inf")
    for index, point in enumerate(store.get("points", [])):
        if point.get("object_id") != object_id:
            continue
        world = point.get("world_xyz_m")
        if not isinstance(world, list) or len(world) != 3:
            continue
        point_world = np.asarray(world, dtype=np.float32)
        distance = float(np.linalg.norm(point_world - target))
        if distance < best_distance:
            best_distance = distance
            best_index = index

    if best_index < 0 or best_distance > max_distance:
        return []

    best_point = store["points"][best_index]
    best_world = np.asarray(best_point.get("world_xyz_m") or [0.0, 0.0, 0.0], dtype=np.float32)
    logical_id = str(best_point.get("logical_point_id") or "").strip()
    source_id = str(best_point.get("source_point_id") or best_point.get("point_id") or "").strip()
    removed: list[dict] = []
    kept: list[dict] = []
    for point in store.get("points", []):
        if point.get("object_id") != object_id:
            kept.append(point)
            continue

        point_logical_id = str(point.get("logical_point_id") or "").strip()
        point_source_id = str(point.get("source_point_id") or point.get("point_id") or "").strip()
        point_id = str(point.get("point_id") or "").strip()
        same_logical = bool(logical_id) and point_logical_id == logical_id
        same_source = not logical_id and bool(source_id) and (point_source_id == source_id or point_id == source_id)
        if same_logical or same_source:
            item = dict(point)
            item["delete_distance_m"] = float(np.linalg.norm(np.asarray(point.get("world_xyz_m") or best_world, dtype=np.float32) - target))
            removed.append(item)
        else:
            kept.append(point)

    store["points"] = kept
    return removed


def _points_for_image(store: dict, image_record: dict) -> list[dict]:
    image_id = image_record.get("image_id")
    object_id = image_record.get("object_id") or store.get("active_object_id") or "object_default"
    return [
        point
        for point in store.get("points", [])
        if point.get("image_id") == image_id and point.get("object_id") == object_id
    ]


def _object_records(store: dict) -> list[dict]:
    objects = store.setdefault("objects", [])
    if isinstance(objects, dict):
        converted: list[dict] = []
        for object_id, record in objects.items():
            item = dict(record) if isinstance(record, dict) else {}
            item.setdefault("object_id", str(object_id))
            converted.append(item)
        store["objects"] = converted
        objects = converted
    if not isinstance(objects, list):
        store["objects"] = []
    return store["objects"]


def _find_object_record(store: dict, object_id: str) -> dict | None:
    for record in _object_records(store):
        if str(record.get("object_id") or "") == object_id:
            return record
    return None


def _object_images(store: dict, object_id: str) -> list[dict]:
    records = [
        image
        for image in store.get("images", [])
        if str(image.get("object_id") or "object_default") == object_id
    ]
    records.sort(key=lambda image: int(image.get("created_at_ms") or 0))
    return records


def _object_points(store: dict, object_id: str) -> list[dict]:
    return [
        point
        for point in store.get("points", [])
        if str(point.get("object_id") or "object_default") == object_id
    ]


def _object_spatial_points(store: dict, object_id: str) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()
    for point in _object_points(store, object_id):
        world = point.get("world_xyz_m")
        if not isinstance(world, list) or len(world) != 3:
            continue
        key = str(point.get("source_point_id") or point.get("point_id") or "")
        if not key:
            try:
                key = "world:" + ",".join(f"{float(value):.4f}" for value in world)
            except (TypeError, ValueError):
                continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                "point_id": str(point.get("point_id") or key),
                "label": 1 if int(point.get("label", 1)) > 0 else 0,
                "world_xyz_m": [float(world[0]), float(world[1]), float(world[2])],
                "image_id": str(point.get("image_id") or ""),
            }
        )
    return deduped


def _empty_object_spatial_summary(reason: str) -> dict:
    return {
        "valid": False,
        "reason": reason,
        "center_xyz_m": [],
        "min_xyz_m": [],
        "max_xyz_m": [],
        "extent_xyz_m": [],
        "radius_m": 0.0,
        "point_count": 0,
        "image_count": 0,
        "source": "rgbd_mask_world_points",
    }


def _rgb_camera_points_to_world(points_rgb: np.ndarray, rgb_meta: dict) -> np.ndarray:
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
    return (points_rgb @ rgb_rot.T) + rgb_pos[None, :]


def _masked_rgb_depth_world_points(
    mask: np.ndarray,
    depth: np.ndarray,
    meta: dict,
    args: argparse.Namespace,
    max_points: int = 12000,
) -> np.ndarray:
    if mask.shape != depth.shape:
        mask = cv2.resize(
            mask.astype(np.uint8),
            (depth.shape[1], depth.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        ).astype(bool)

    valid = (
        mask.astype(bool)
        & np.isfinite(depth)
        & (depth >= float(args.min_depth))
        & (depth <= float(args.max_depth))
    )
    ys, xs = np.where(valid)
    if xs.size == 0:
        return np.empty((0, 3), dtype=np.float32)

    if xs.size > max_points:
        indices = np.linspace(0, xs.size - 1, num=max_points, dtype=np.int64)
        xs = xs[indices]
        ys = ys[indices]

    rgb_meta = meta["rgb"]
    fx = float(rgb_meta["focal_length_x"])
    fy = float(rgb_meta["focal_length_y"])
    cx = float(rgb_meta["principal_point_x"])
    cy = float(rgb_meta["principal_point_y"])
    height = int(rgb_meta["resolution_h"])

    z = depth[ys, xs].astype(np.float32)
    sensor_y = (height - 1) - ys.astype(np.float32)
    points_rgb = np.stack(
        [
            (xs.astype(np.float32) - cx) * z / fx,
            (sensor_y - cy) * z / fy,
            z,
        ],
        axis=1,
    )
    points_world = _rgb_camera_points_to_world(points_rgb, rgb_meta).astype(np.float32)
    finite = np.isfinite(points_world).all(axis=1)
    return points_world[finite]


def _compute_object_spatial_summary(
    args: argparse.Namespace,
    device_dir: Path,
    store: dict,
    object_id: str,
) -> dict:
    point_sets: list[np.ndarray] = []
    image_count = 0
    for image in _object_images(store, object_id):
        mask_path = _mask_path_for_image(device_dir, image)
        if mask_path is None:
            continue
        mask_u8 = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask_u8 is None:
            continue
        depth = _load_room_image_depth(device_dir, image, source="any2full")
        if depth is None:
            continue
        meta_path = _resolve_store_path(device_dir, image.get("meta_json"))
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            points = _masked_rgb_depth_world_points(mask_u8 > 0, depth, meta, args)
        except Exception as exc:
            print(f"[room-object] spatial summary skipped {image.get('image_id')}: {exc}", flush=True)
            continue
        if points.size == 0:
            continue
        point_sets.append(points)
        image_count += 1

    if not point_sets:
        return _empty_object_spatial_summary("no_mask_depth_points")

    points_all = np.concatenate(point_sets, axis=0)
    if points_all.shape[0] < 16:
        return _empty_object_spatial_summary("too_few_mask_depth_points")

    median = np.median(points_all, axis=0)
    distances = np.linalg.norm(points_all - median[None, :], axis=1)
    if distances.size >= 32:
        cutoff = float(np.percentile(distances, 96.0))
        points_trimmed = points_all[distances <= max(cutoff, 1e-4)]
    else:
        points_trimmed = points_all
    if points_trimmed.shape[0] < 8:
        points_trimmed = points_all

    min_xyz = np.percentile(points_trimmed, 2.0, axis=0)
    max_xyz = np.percentile(points_trimmed, 98.0, axis=0)
    center = (min_xyz + max_xyz) * 0.5
    radius = float(np.percentile(np.linalg.norm(points_trimmed - center[None, :], axis=1), 95.0))
    extent = max_xyz - min_xyz
    return {
        "valid": True,
        "reason": "ok",
        "center_xyz_m": [float(v) for v in center],
        "min_xyz_m": [float(v) for v in min_xyz],
        "max_xyz_m": [float(v) for v in max_xyz],
        "extent_xyz_m": [float(v) for v in extent],
        "radius_m": radius,
        "point_count": int(points_trimmed.shape[0]),
        "raw_point_count": int(points_all.shape[0]),
        "image_count": image_count,
        "source": "rgbd_mask_world_points",
    }


def _format_local_timestamp_s(timestamp_ms: int | None = None) -> str:
    seconds = (int(timestamp_ms) if timestamp_ms is not None else _now_ms()) / 1000.0
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seconds))


def _path_inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _delete_store_path(device_dir: Path, path: Path | None) -> None:
    if path is None:
        return
    try:
        resolved = path.resolve()
    except Exception:
        return
    if not _path_inside(device_dir, resolved) or not resolved.exists():
        return
    if resolved.is_dir():
        shutil.rmtree(resolved)
    else:
        resolved.unlink()


def _remove_object_content(device_dir: Path, store: dict, object_id: str) -> int:
    removed_images = 0
    kept_images: list[dict] = []
    for image in store.get("images", []):
        if str(image.get("object_id") or "object_default") != object_id:
            kept_images.append(image)
            continue
        removed_images += 1
        rgb_png = image.get("rgb_png")
        if rgb_png:
            _delete_store_path(device_dir, _resolve_store_path(device_dir, rgb_png))
        capture_dir = image.get("capture_dir")
        if capture_dir:
            _delete_store_path(device_dir, _resolve_store_path(device_dir, capture_dir))
    store["images"] = kept_images
    store["points"] = [
        point
        for point in store.get("points", [])
        if str(point.get("object_id") or "object_default") != object_id
    ]
    store["objects"] = [
        record
        for record in _object_records(store)
        if str(record.get("object_id") or "") != object_id
    ]
    return removed_images


def _object_backup_root(device_dir: Path) -> Path:
    return device_dir / ".object_edit_backups"


def _object_backup_dir(device_dir: Path, edit_session_id: str) -> Path:
    return _object_backup_root(device_dir) / _safe_path_component(edit_session_id, "edit_default")


def _relative_to_device(device_dir: Path, path: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(device_dir.resolve()).as_posix())
    except Exception:
        return None


def _begin_object_edit(device_dir: Path, store: dict, object_id: str) -> tuple[str, list[dict]]:
    edit_session_id = f"edit_{_now_ms()}_{object_id}"
    backup_dir = _object_backup_dir(device_dir, edit_session_id)
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "points.json").write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")

    mask_entries: list[dict] = []
    for image in _object_images(store, object_id):
        image_id = str(image.get("image_id") or "")
        capture_dir = _resolve_store_path(device_dir, image.get("capture_dir"))
        candidates: list[Path] = []
        raw_mask = image.get("device_mask")
        if raw_mask:
            candidates.append(Path(str(raw_mask)))
        candidates.extend(
            [
                capture_dir / "device_mask.png",
                capture_dir / "device_mask_raw.png",
                capture_dir / "device_mask_raw_overlay.png",
            ]
        )
        for path in candidates:
            if not path.exists() or not _path_inside(device_dir, path):
                continue
            rel = _relative_to_device(device_dir, path)
            if rel is None:
                continue
            dst = backup_dir / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dst)
            mask_entries.append({"image_id": image_id, "relative_path": rel})

    manifest = {
        "schema_version": 1,
        "object_id": object_id,
        "created_at_ms": _now_ms(),
        "image_ids": [str(image.get("image_id") or "") for image in _object_images(store, object_id)],
        "file_backups": mask_entries,
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return edit_session_id, _object_spatial_points(store, object_id)


def _discard_object_edit_backup(device_dir: Path, edit_session_id: str) -> None:
    if not edit_session_id:
        return
    _delete_store_path(device_dir, _object_backup_dir(device_dir, edit_session_id))


def _restore_object_edit_backup(device_dir: Path, edit_session_id: str) -> dict:
    backup_dir = _object_backup_dir(device_dir, edit_session_id)
    points_path = backup_dir / "points.json"
    manifest_path = backup_dir / "manifest.json"
    if not points_path.exists():
        raise FileNotFoundError(f"edit backup not found: {edit_session_id}")

    backup_store = json.loads(points_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    object_id = str(manifest.get("object_id") or "")
    original_image_ids = {str(value) for value in manifest.get("image_ids", [])}

    current_store_path = device_dir / "points.json"
    if object_id and current_store_path.exists():
        try:
            current_store = json.loads(current_store_path.read_text(encoding="utf-8"))
        except Exception:
            current_store = {}
        for image in _object_images(current_store, object_id):
            image_id = str(image.get("image_id") or "")
            if image_id in original_image_ids:
                continue
            rgb_png = image.get("rgb_png")
            if rgb_png:
                _delete_store_path(device_dir, _resolve_store_path(device_dir, rgb_png))
            capture_dir = image.get("capture_dir")
            if capture_dir:
                _delete_store_path(device_dir, _resolve_store_path(device_dir, capture_dir))

    for entry in manifest.get("file_backups", []):
        rel = str(entry.get("relative_path") or "")
        if not rel:
            continue
        src = backup_dir / "files" / rel
        dst = device_dir / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    _save_point_store(device_dir, backup_store)
    _discard_object_edit_backup(device_dir, edit_session_id)
    return backup_store


def _complete_object(
    args: argparse.Namespace,
    device_dir: Path,
    store: dict,
    object_id: str,
    edit_session_id: str = "",
) -> dict:
    images = _object_images(store, object_id)
    points = _object_points(store, object_id)
    if not images:
        raise ValueError("object has no captured images")
    if not any(int(point.get("label", 1)) > 0 for point in points):
        raise ValueError("object needs at least one positive point")

    now_ms = _now_ms()
    record = _find_object_record(store, object_id)
    if record is None:
        record = {"object_id": object_id}
        _object_records(store).append(record)
    if not str(record.get("name") or "").strip():
        record["name"] = _format_local_timestamp_s(now_ms)
    record["status"] = "completed"
    record.setdefault("created_at_ms", now_ms)
    record["completed_at_ms"] = int(record.get("completed_at_ms") or now_ms)
    record["updated_at_ms"] = now_ms
    record["image_count"] = len(images)
    record["point_count"] = len(points)
    record["positive_point_count"] = sum(1 for point in points if int(point.get("label", 1)) > 0)
    record["negative_point_count"] = len(points) - int(record["positive_point_count"])
    record["thumbnail_version"] = now_ms
    record["spatial"] = _compute_object_spatial_summary(args, device_dir, store, object_id)
    _save_point_store(device_dir, store)
    _discard_object_edit_backup(device_dir, edit_session_id)
    return record


def _completed_object_records(store: dict) -> list[dict]:
    records: list[dict] = []
    for record in _object_records(store):
        if str(record.get("status") or "") != "completed":
            continue
        object_id = str(record.get("object_id") or "")
        images = _object_images(store, object_id)
        points = _object_points(store, object_id)
        records.append(
            {
                "object_id": object_id,
                "name": str(record.get("name") or _format_local_timestamp_s(int(record.get("completed_at_ms") or _now_ms()))),
                "status": "completed",
                "created_at_ms": int(record.get("created_at_ms") or record.get("completed_at_ms") or 0),
                "completed_at_ms": int(record.get("completed_at_ms") or 0),
                "updated_at_ms": int(record.get("updated_at_ms") or record.get("completed_at_ms") or 0),
                "image_count": len(images),
                "point_count": len(points),
                "positive_point_count": sum(1 for point in points if int(point.get("label", 1)) > 0),
                "negative_point_count": sum(1 for point in points if int(point.get("label", 1)) <= 0),
                "thumbnail_version": int(record.get("thumbnail_version") or record.get("updated_at_ms") or 0),
                "pairing_status": str(record.get("pairing_status") or "not_started"),
                "network_binding": record.get("network_binding"),
                "spatial": record.get("spatial") or _empty_object_spatial_summary("missing"),
            }
        )
    records.sort(key=lambda record: int(record.get("completed_at_ms") or 0), reverse=True)
    return records


def _compact_network_profile(profile: dict) -> dict:
    identifiers = profile.get("identifiers") if isinstance(profile.get("identifiers"), dict) else {}
    connections = profile.get("connections") if isinstance(profile.get("connections"), dict) else {}
    data = profile.get("data") if isinstance(profile.get("data"), dict) else {}
    operations = profile.get("operations") if isinstance(profile.get("operations"), list) else []

    def flatten(mapping: dict, limit: int = 6) -> list[str]:
        values: list[str] = []
        for key in sorted(mapping):
            raw = mapping.get(key)
            if isinstance(raw, list):
                for item in raw:
                    text = str(item or "").strip()
                    if text:
                        values.append(text)
            else:
                text = str(raw or "").strip()
                if text:
                    values.append(text)
            if len(values) >= limit:
                break
        return values[:limit]

    data_preview = []
    for key, value in list(sorted(data.items()))[:6]:
        if isinstance(value, dict):
            rendered_value = value.get("value")
            unit = str(value.get("unit") or "")
        else:
            rendered_value = value
            unit = ""
        data_preview.append(
            {
                "key": str(key),
                "value": str(rendered_value),
                "unit": unit,
            }
        )

    operation_preview = []
    for operation in operations[:6]:
        if not isinstance(operation, dict):
            continue
        operation_preview.append(
            {
                "topic": str(operation.get("topic") or operation.get("command_topic") or ""),
                "action": str(operation.get("action") or operation.get("property") or ""),
            }
        )

    addresses = flatten(connections, limit=8)
    identity_values = flatten(identifiers, limit=8)
    return {
        "canonical_device_id": str(profile.get("canonical_device_id") or ""),
        "display_name": str(profile.get("display_name") or ""),
        "summary": str(profile.get("summary") or ""),
        "vendor": str(profile.get("vendor") or ""),
        "model_candidates": profile.get("model_candidates") or [],
        "device_type": str(profile.get("device_type") or ""),
        "capabilities": profile.get("capabilities") or [],
        "protocols": profile.get("protocols") or [],
        "online": bool(profile.get("online", False)),
        "last_seen": float(profile.get("last_seen") or 0.0),
        "data_count": len(data),
        "operation_count": len(operations),
        "data_preview": data_preview,
        "operation_preview": operation_preview,
        "address_summary": " / ".join(addresses),
        "identifier_summary": " / ".join(identity_values),
    }


def _pairing_candidate_payloads(record: dict, limit: int | None = None, compact_profile: bool = False) -> list[dict]:
    candidates = record.get("pairing_candidates") or []
    if not isinstance(candidates, list):
        return []
    if limit is not None:
        candidates = candidates[: max(0, int(limit))]
    if not compact_profile:
        return candidates
    payloads: list[dict] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        item = dict(candidate)
        profile = item.get("profile")
        if isinstance(profile, dict):
            item["profile"] = _compact_network_profile(profile)
        payloads.append(item)
    return payloads


def _pairing_record_payload(record: dict, candidate_limit: int | None = None, compact_profile: bool = False) -> dict:
    return {
        "object_name": str(record.get("name") or record.get("object_id") or ""),
        "vlm_status": str(record.get("vlm_status") or "not_started"),
        "vlm_error": str(record.get("vlm_error") or ""),
        "pairing_status": str(record.get("pairing_status") or "not_started"),
        "pairing_error": str(record.get("pairing_error") or ""),
        "pairing_warning": str(record.get("pairing_warning") or ""),
        "visual_profile": record.get("pairing_visual_profile") or _object_visual_profile(record),
        "candidates": _pairing_candidate_payloads(record, candidate_limit, compact_profile),
        "binding": record.get("network_binding"),
        "started_at_ms": int(record.get("pairing_started_at_ms") or 0),
        "completed_at_ms": int(record.get("pairing_completed_at_ms") or 0),
        "evaluated_candidate_count": int(record.get("pairing_evaluated_candidate_count") or 0),
        "llm_candidate_count": int(record.get("pairing_llm_candidate_count") or 0),
    }


def _find_binding_conflict(
    args: argparse.Namespace,
    room_id: str,
    canonical_device_id: str,
    current_device_dir: Path,
    current_object_id: str,
) -> dict | None:
    store_root = _room_store_root(args)
    if not store_root.exists():
        return None
    for room_dir in store_root.iterdir():
        if not room_dir.is_dir():
            continue
        for device_dir in room_dir.iterdir():
            if not device_dir.is_dir():
                continue
            points_path = device_dir / "points.json"
            if not points_path.exists():
                continue
            try:
                store = json.loads(points_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for record in _object_records(store):
                object_id = str(record.get("object_id") or "")
                if device_dir.resolve() == current_device_dir.resolve() and object_id == current_object_id:
                    continue
                binding = record.get("network_binding")
                if not isinstance(binding, dict):
                    continue
                if str(binding.get("canonical_device_id") or "") != canonical_device_id:
                    continue
                return {
                    "room_id": str(store.get("room_id") or room_id or room_dir.name),
                    "quest_device_id": str(store.get("device_id") or device_dir.name),
                    "object_id": object_id,
                    "object_name": str(record.get("name") or object_id),
                    "binding": binding,
                }
    return None


def _load_rgb_for_image(viewer: "RgbdViewer", device_dir: Path, image_record: dict) -> np.ndarray:
    rgb_png = image_record.get("rgb_png")
    if rgb_png:
        path = _resolve_store_path(device_dir, rgb_png)
        if path.exists():
            return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)
    frame = _load_room_frame(viewer, device_dir, image_record)
    return frame.rgb.astype(np.uint8, copy=False)


def _masked_rgb_pil_for_image(
    viewer: "RgbdViewer",
    device_dir: Path,
    image_record: dict,
    *,
    crop_to_mask: bool = True,
    output_size: tuple[int, int] | None = None,
    max_side: int | None = None,
) -> Image.Image:
    rgb = _load_rgb_for_image(viewer, device_dir, image_record)
    canvas = np.full_like(rgb, 255, dtype=np.uint8)
    mask_path = _mask_path_for_image(device_dir, image_record)
    if mask_path is not None:
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is not None and mask.shape == rgb.shape[:2]:
            mask_bool = mask > 0
            canvas[mask_bool] = rgb[mask_bool]
            if crop_to_mask:
                ys, xs = np.where(mask_bool)
                if len(xs) > 0 and len(ys) > 0:
                    pad = 24
                    x0 = max(0, int(xs.min()) - pad)
                    x1 = min(canvas.shape[1], int(xs.max()) + pad + 1)
                    y0 = max(0, int(ys.min()) - pad)
                    y1 = min(canvas.shape[0], int(ys.max()) + pad + 1)
                    canvas = canvas[y0:y1, x0:x1]
    else:
        canvas = rgb

    image = Image.fromarray(canvas.astype(np.uint8)).convert("RGB")
    if output_size is not None:
        return image.resize(output_size, Image.Resampling.LANCZOS)
    if max_side is not None and max(image.size) > max_side:
        scale = max_side / float(max(image.size))
        size = (max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale))))
        return image.resize(size, Image.Resampling.LANCZOS)
    return image


def _object_masked_images(viewer: "RgbdViewer", device_dir: Path, store: dict, object_id: str) -> list[tuple[dict, Image.Image]]:
    images: list[tuple[dict, Image.Image]] = []
    for image_record in _object_images(store, object_id):
        if _mask_path_for_image(device_dir, image_record) is None:
            continue
        try:
            image = _masked_rgb_pil_for_image(viewer, device_dir, image_record, crop_to_mask=True, max_side=1024)
        except Exception as exc:
            print(f"[room-object] masked image failed for {image_record.get('image_id')}: {exc}", flush=True)
            continue
        images.append((image_record, image))
    return images


def _pil_to_data_url(image: Image.Image, quality: int = 90) -> str:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + encoded


def _build_object_thumbnail(viewer: "RgbdViewer", device_dir: Path, store: dict, object_id: str) -> bytes:
    image_record = next((image for image in _object_images(store, object_id) if _mask_path_for_image(device_dir, image) is not None), None)
    if image_record is None:
        images = _object_images(store, object_id)
        image_record = images[0] if images else None
    if image_record is None:
        image = Image.fromarray(np.full((192, 256, 3), 255, dtype=np.uint8))
    else:
        image = _masked_rgb_pil_for_image(viewer, device_dir, image_record, crop_to_mask=True, output_size=(256, 192))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _object_detail_images(viewer: "RgbdViewer", device_dir: Path, store: dict, object_id: str) -> list[tuple[dict, Image.Image]]:
    images: list[tuple[dict, Image.Image]] = []
    for image_record in _object_images(store, object_id):
        try:
            image = _masked_rgb_pil_for_image(viewer, device_dir, image_record, crop_to_mask=True, max_side=900)
        except Exception as exc:
            print(f"[room-object] detail image failed for {image_record.get('image_id')}: {exc}", flush=True)
            continue
        images.append((image_record, image))
    return images


def _vlm_object_prompt() -> str:
    return (
        "Analyze all supplied masked images as different views of the same physical device. "
        "Use visible appearance, text, labels, ports, screens, installation and likely function. "
        "Network search may be used only when the configured model supports it. "
        "Do not invent a brand or model. Distinguish confirmed visible evidence from inference.\n\n"
        "Return exactly one JSON object with this schema:\n"
        "{\n"
        '  "summary_zh": "concise Chinese description",\n'
        '  "device_type": "stable snake_case type such as temperature_humidity_sensor",\n'
        '  "vendor_candidates": ["candidate vendor"],\n'
        '  "model_candidates": ["candidate model"],\n'
        '  "visible_text": ["text actually visible in images"],\n'
        '  "capabilities": ["temperature", "humidity", "power", "..."],\n'
        '  "physical_features": ["shape, color, ports, screen, mounting"],\n'
        '  "uncertainties": ["facts that cannot be confirmed"],\n'
        '  "suggested_views": ["additional views that would improve identification"]\n'
        "}\n"
        "Use empty arrays for unavailable evidence. Do not wrap the JSON in Markdown."
    )


def _build_vlm_object_messages(viewer: "RgbdViewer", device_dir: Path, store: dict, object_id: str) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": _vlm_object_prompt()}]
    for image_record, image in _object_masked_images(viewer, device_dir, store, object_id):
        content.append({"type": "text", "text": f"Image {image_record.get('image_id', '')}"})
        content.append({"type": "image_url", "image_url": {"url": _pil_to_data_url(image)}})
    return [{"role": "user", "content": content}]


def _run_vlm_object_analysis(viewer: "RgbdViewer", cursor: dict, object_id: str) -> None:
    try:
        config = _load_vlm_config(viewer.args)
        if not str(config.get("base_url") or "").strip() or not str(config.get("model") or "").strip():
            print("[vlm] skipped: VLM config is incomplete", flush=True)
            _finish_analysis_job(viewer, "vlm", cursor, object_id)
            return

        ctx, device_dir, store = _load_point_store(viewer.args, cursor)
        _ = ctx
        record = _find_object_record(store, object_id)
        if record is None:
            _finish_analysis_job(viewer, "vlm", cursor, object_id)
            return
        record["vlm_status"] = "processing"
        record["vlm_started_at_ms"] = _now_ms()
        record["vlm_error"] = ""
        _save_point_store(device_dir, store)
    except Exception as exc:
        _finish_analysis_job(viewer, "vlm", cursor, object_id)
        print(f"[vlm] failed to initialize object analysis for {object_id}: {exc}", flush=True)
        return
    if hasattr(viewer, "root"):
        viewer.root.after(0, viewer.refresh_device_tree)

    try:
        messages = _build_vlm_object_messages(viewer, device_dir, store, object_id)
        if len(messages[0]["content"]) <= 1:
            raise ValueError("no masked images available for VLM")
        description = _vlm_chat_completion(
            config,
            messages,
            timeout=float(getattr(viewer.args, "vlm_timeout_seconds", 120.0)),
            max_tokens=1600,
        )
        if not description.strip():
            raise ValueError("VLM returned an empty device description")
        ctx, device_dir, store = _load_point_store(viewer.args, cursor)
        _ = ctx
        record = _find_object_record(store, object_id)
        if record is not None:
            record["vlm_status"] = "done"
            record["vlm_model"] = str(config.get("model") or "")
            record["vlm_description"] = description
            record["vlm_profile"] = normalize_visual_profile(parse_json_object(description), description)
            record["vlm_prompt"] = _vlm_object_prompt()
            record["vlm_completed_at_ms"] = _now_ms()
            record["vlm_error"] = ""
            _save_point_store(device_dir, store)
            _schedule_object_pairing_analysis(viewer, cursor, object_id)
    except Exception as exc:
        ctx, device_dir, store = _load_point_store(viewer.args, cursor)
        _ = ctx
        record = _find_object_record(store, object_id)
        if record is not None:
            record["vlm_status"] = "error"
            record["vlm_error"] = str(exc)
            record["vlm_completed_at_ms"] = _now_ms()
            _save_point_store(device_dir, store)
        print(f"[vlm] object analysis failed for {object_id}: {exc}", flush=True)
    finally:
        _finish_analysis_job(viewer, "vlm", cursor, object_id)
        if hasattr(viewer, "root"):
            viewer.root.after(0, viewer.refresh_device_tree)


def _object_visual_profile(record: dict) -> dict:
    stored = record.get("vlm_profile")
    if isinstance(stored, dict):
        return normalize_visual_profile(stored, str(record.get("vlm_description") or ""))
    description = str(record.get("vlm_description") or "")
    return normalize_visual_profile(parse_json_object(description), description)


def _visual_profile_has_evidence(profile: dict) -> bool:
    if str(profile.get("summary") or "").strip() or str(profile.get("device_type") or "").strip():
        return True
    return any(
        bool(profile.get(key))
        for key in (
            "vendor_candidates",
            "model_candidates",
            "visible_text",
            "capabilities",
            "physical_features",
        )
    )


def _analysis_job_key(cursor: dict, object_id: str) -> tuple[str, str, str]:
    ctx = _room_context(cursor)
    return ctx["room_id"], ctx["device_id"], str(object_id)


def _reserve_analysis_job(viewer: "RgbdViewer", job_type: str, cursor: dict, object_id: str) -> bool:
    key = _analysis_job_key(cursor, object_id)
    jobs = viewer._vlm_jobs if job_type == "vlm" else viewer._pairing_jobs
    with viewer._analysis_jobs_lock:
        if key in jobs:
            return False
        jobs.add(key)
        return True


def _finish_analysis_job(viewer: "RgbdViewer", job_type: str, cursor: dict, object_id: str) -> None:
    key = _analysis_job_key(cursor, object_id)
    jobs = viewer._vlm_jobs if job_type == "vlm" else viewer._pairing_jobs
    with viewer._analysis_jobs_lock:
        jobs.discard(key)


def _run_object_pairing_analysis(viewer: "RgbdViewer", cursor: dict, object_id: str) -> None:
    runtime = getattr(viewer, "discover_runtime", None)
    if runtime is None:
        try:
            with viewer._pairing_lock:
                _ctx, device_dir, store = _load_point_store(viewer.args, cursor)
                record = _find_object_record(store, object_id)
                if record is not None:
                    record["pairing_status"] = "error"
                    record["pairing_error"] = "discovery runtime is not running"
                    record["pairing_completed_at_ms"] = _now_ms()
                    _save_point_store(device_dir, store)
        except Exception:
            pass
        _finish_analysis_job(viewer, "pairing", cursor, object_id)
        return

    try:
        with viewer._pairing_lock:
            ctx, device_dir, store = _load_point_store(viewer.args, cursor)
            _ = ctx
            record = _find_object_record(store, object_id)
            if record is None:
                return
            visual_profile = _object_visual_profile(record)
            if str(record.get("vlm_status") or "") != "done" or not _visual_profile_has_evidence(visual_profile):
                record["pairing_status"] = "waiting_for_vlm"
                record["pairing_error"] = "Visual device description is unavailable. Run VLM analysis first."
                record["pairing_candidates"] = []
                record["pairing_completed_at_ms"] = _now_ms()
                _save_point_store(device_dir, store)
                return
            record["pairing_status"] = "processing"
            record["pairing_started_at_ms"] = _now_ms()
            record["pairing_error"] = ""
            record["pairing_warning"] = ""
            record["pairing_candidates"] = []
            _save_point_store(device_dir, store)

        profiles = runtime.profiles()
        baseline_candidates = score_candidates(visual_profile, profiles)
        profile_by_id = {
            str(profile.get("canonical_device_id") or ""): profile
            for profile in profiles
        }
        shortlist_limit = max(1, int(getattr(viewer.args, "pairing_max_candidates", 50)))
        shortlisted_profiles = [
            profile_by_id[str(candidate.get("canonical_device_id") or "")]
            for candidate in baseline_candidates[:shortlist_limit]
            if str(candidate.get("canonical_device_id") or "") in profile_by_id
        ]

        llm_payload = None
        llm_response = ""
        llm_warning = ""
        config = _load_vlm_config(viewer.args)
        if shortlisted_profiles and str(config.get("base_url") or "").strip() and str(config.get("model") or "").strip():
            try:
                prompt = build_pairing_prompt(visual_profile, shortlisted_profiles)
                llm_response = _vlm_chat_completion(
                    config,
                    [{"role": "user", "content": prompt}],
                    timeout=float(getattr(viewer.args, "vlm_timeout_seconds", 120.0)),
                    max_tokens=max(1800, min(8000, 900 + len(shortlisted_profiles) * 350)),
                )
                llm_payload = parse_json_object(llm_response)
                if llm_payload is None:
                    llm_warning = "LLM pairing response was not valid JSON; deterministic scoring was used."
            except Exception as exc:
                llm_warning = f"LLM pairing review failed; deterministic scoring was used: {exc}"
                print(f"[pairing] LLM review failed for {object_id}: {exc}", flush=True)

        all_candidates = score_candidates(visual_profile, profiles, llm_payload)
        candidates = all_candidates[:shortlist_limit]
        with viewer._pairing_lock:
            _ctx, device_dir, store = _load_point_store(viewer.args, cursor)
            record = _find_object_record(store, object_id)
            if record is not None:
                record["pairing_status"] = "done"
                record["pairing_visual_profile"] = visual_profile
                record["pairing_candidates"] = candidates
                record["pairing_llm_model"] = str(config.get("model") or "") if llm_payload is not None else ""
                record["pairing_llm_response"] = llm_response
                record["pairing_evaluated_candidate_count"] = len(all_candidates)
                record["pairing_llm_candidate_count"] = len(shortlisted_profiles)
                record["pairing_completed_at_ms"] = _now_ms()
                record["pairing_error"] = ""
                record["pairing_warning"] = llm_warning
                _save_point_store(device_dir, store)
    except Exception as exc:
        try:
            with viewer._pairing_lock:
                _ctx, device_dir, store = _load_point_store(viewer.args, cursor)
                record = _find_object_record(store, object_id)
                if record is not None:
                    record["pairing_status"] = "error"
                    record["pairing_error"] = str(exc)
                    record["pairing_completed_at_ms"] = _now_ms()
                    _save_point_store(device_dir, store)
        except Exception:
            pass
        print(f"[pairing] analysis failed for {object_id}: {exc}", flush=True)
    finally:
        _finish_analysis_job(viewer, "pairing", cursor, object_id)
        if hasattr(viewer, "root"):
            viewer.root.after(0, viewer.refresh_device_tree)


def _schedule_object_pairing_analysis(viewer: "RgbdViewer", cursor: dict, object_id: str) -> bool:
    if not _reserve_analysis_job(viewer, "pairing", cursor, object_id):
        return False
    try:
        worker = threading.Thread(
            target=_run_object_pairing_analysis,
            args=(viewer, dict(cursor), object_id),
            daemon=True,
        )
        worker.start()
    except Exception:
        _finish_analysis_job(viewer, "pairing", cursor, object_id)
        raise
    return True


def _schedule_vlm_object_analysis(viewer: "RgbdViewer", cursor: dict, object_id: str) -> bool:
    config = _load_vlm_config(viewer.args)
    _ctx, device_dir, store = _load_point_store(viewer.args, cursor)
    record = _find_object_record(store, object_id)
    if record is None:
        return False
    if not str(config.get("base_url") or "").strip() or not str(config.get("model") or "").strip():
        record["vlm_status"] = "error"
        record["vlm_error"] = "VLM is not configured. Configure a base URL and model before pairing."
        record["vlm_completed_at_ms"] = _now_ms()
        record["pairing_status"] = "waiting_for_vlm"
        record["pairing_error"] = "Waiting for a visual device description."
        record["pairing_candidates"] = []
        _save_point_store(device_dir, store)
        return False
    if not _reserve_analysis_job(viewer, "vlm", cursor, object_id):
        return True
    try:
        record["vlm_status"] = "processing"
        record["vlm_started_at_ms"] = _now_ms()
        record["vlm_error"] = ""
        record["pairing_status"] = "waiting_for_vlm"
        record["pairing_error"] = ""
        record["pairing_candidates"] = []
        _save_point_store(device_dir, store)
        worker = threading.Thread(
            target=_run_vlm_object_analysis,
            args=(viewer, dict(cursor), object_id),
            daemon=True,
        )
        worker.start()
    except Exception:
        _finish_analysis_job(viewer, "vlm", cursor, object_id)
        raise
    return True


def _vlm_processing_stale(args: argparse.Namespace, record: dict) -> bool:
    if str(record.get("vlm_status") or "") != "processing":
        return False
    started_at = int(record.get("vlm_started_at_ms") or 0)
    if started_at <= 0:
        return True
    timeout_ms = int(max(60_000, float(getattr(args, "vlm_timeout_seconds", 120.0)) * 1000.0 * 1.5))
    return _now_ms() - started_at > timeout_ms


def _mark_stale_vlm_records(args: argparse.Namespace, device_dir: Path, store: dict) -> bool:
    changed = False
    for record in _object_records(store):
        if _vlm_processing_stale(args, record):
            record["vlm_status"] = "error"
            record["vlm_error"] = "Previous VLM processing timed out or was interrupted. Use Retry VLM."
            record["vlm_completed_at_ms"] = _now_ms()
            changed = True
        if str(record.get("pairing_status") or "") == "processing":
            started_at = int(record.get("pairing_started_at_ms") or 0)
            timeout_ms = int(max(90_000, float(getattr(args, "vlm_timeout_seconds", 120.0)) * 2000.0))
            if started_at <= 0 or _now_ms() - started_at > timeout_ms:
                record["pairing_status"] = "error"
                record["pairing_error"] = "Previous pairing analysis timed out or was interrupted. Refresh Match to retry."
                record["pairing_completed_at_ms"] = _now_ms()
                changed = True
    if changed:
        _save_point_store(device_dir, store)
    return changed


def _preview_query_to_cursor(query: dict[str, list[str]]) -> dict:
    def first(*names: str, fallback: str = "") -> str:
        for name in names:
            values = query.get(name)
            if values:
                return values[0]
        return fallback

    return {
        "room_id": first("room_id", "room_name", fallback="room_default"),
        "room_name": first("room_name", "room_id", fallback="room_default"),
        "device_id": first("device_id", "device_name", fallback="device_default"),
        "device_name": first("device_name", "device_id", fallback="device_default"),
        "device_model": first("device_model", fallback=""),
        "object_session_id": first("object_id", "object_session_id", fallback="object_default"),
    }


def _room_preview_records(store: dict, image_records: list[dict]) -> list[dict]:
    records: list[dict] = []
    for image in image_records:
        points = _points_for_image(store, image)
        positive_count = sum(1 for point in points if int(point.get("label", 1)) > 0)
        negative_count = len(points) - positive_count
        records.append(
            {
                "image_id": str(image.get("image_id") or ""),
                "created_at_ms": int(image.get("created_at_ms") or 0),
                "updated_at_ms": int(image.get("updated_at_ms") or image.get("created_at_ms") or 0),
                "last_segmented_at_ms": int(image.get("last_segmented_at_ms") or 0),
                "point_count": len(points),
                "positive_point_count": positive_count,
                "negative_point_count": negative_count,
                "preseeded_point_count": int(image.get("preseeded_point_count") or 0),
                "segmented": bool(image.get("device_mask")),
            }
        )
    return records


def _mask_path_for_image(device_dir: Path, image_record: dict) -> Path | None:
    raw_path = image_record.get("device_mask")
    candidates: list[Path] = []
    if raw_path:
        candidates.append(Path(str(raw_path)))
    capture_dir = _resolve_store_path(device_dir, image_record.get("capture_dir"))
    candidates.extend(
        [
            capture_dir / "device_mask.png",
            capture_dir / "device_mask_raw.png",
        ]
    )
    for path in candidates:
        if path.exists():
            return path
    return None


def _draw_preview_points(image: np.ndarray, points: list[dict]) -> np.ndarray:
    out = image.astype(np.uint8, copy=True)
    h, w = out.shape[:2]
    for index, point in enumerate(points, start=1):
        coord = point.get("rgb_xy")
        if not isinstance(coord, list) or len(coord) != 2:
            continue
        try:
            x = int(round(float(coord[0])))
            y = int(round(float(coord[1])))
        except (TypeError, ValueError):
            continue
        if x < 0 or x >= w or y < 0 or y >= h:
            continue

        positive = int(point.get("label", 1)) > 0
        color = (0, 230, 118) if positive else (255, 77, 79)
        cv2.circle(out, (x, y), 12, (16, 16, 16), -1, lineType=cv2.LINE_AA)
        cv2.circle(out, (x, y), 10, color, -1, lineType=cv2.LINE_AA)
        cv2.circle(out, (x, y), 12, (255, 255, 255), 2, lineType=cv2.LINE_AA)
        label = str(index)
        cv2.putText(
            out,
            label,
            (x - 5, y + 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.36,
            (8, 8, 8),
            1,
            cv2.LINE_AA,
        )
    return out


def _build_room_preview_image(viewer: "RgbdViewer", device_dir: Path, store: dict, image_record: dict) -> bytes:
    frame = _load_room_frame(viewer, device_dir, image_record)
    base_overlay = frame.any2full_overlay_rgb if frame.any2full_overlay_rgb is not None else frame.overlay_rgb
    preview = base_overlay.astype(np.uint8, copy=True)
    mask_path = _mask_path_for_image(device_dir, image_record)
    if mask_path is not None:
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is not None and mask.shape == preview.shape[:2]:
            preview = overlay_device_mask(preview, mask > 0)

    points = _points_for_image(store, image_record)
    preview = _draw_preview_points(preview, points)

    buffer = io.BytesIO()
    Image.fromarray(preview).save(buffer, format="PNG")
    return buffer.getvalue()


def _build_prompt_from_room_points(
    frame: FrameData,
    args: argparse.Namespace,
    store: dict,
    image_record: dict,
    fallback_cursor: dict,
) -> dict:
    points = _points_for_image(store, image_record)
    coords: list[list[int]] = []
    labels: list[int] = []
    for point in points:
        coord = point.get("rgb_xy")
        if not isinstance(coord, list) or len(coord) != 2:
            continue
        coords.append([int(coord[0]), int(coord[1])])
        labels.append(1 if int(point.get("label", 1)) > 0 else 0)
    positives = [point for point in points if int(point.get("label", 1)) > 0 and isinstance(point.get("rgb_xy"), list)]
    if not positives:
        return {
            "valid": False,
            "reason": "no_positive_point",
            "sam_point_coords": coords,
            "sam_point_labels": labels,
            "user_point_coords": coords,
            "user_point_labels": labels,
        }

    seed = positives[-1]
    seed_cursor = dict(fallback_cursor)
    world = seed.get("world_xyz_m") or fallback_cursor
    if isinstance(world, list) and len(world) == 3:
        seed_cursor["hit_world_x"] = float(world[0])
        seed_cursor["hit_world_y"] = float(world[1])
        seed_cursor["hit_world_z"] = float(world[2])
    seed_cursor["is_hitting"] = True
    seed_cursor["label"] = 1
    seed_cursor["mode"] = "add"

    depth = frame.any2full_depth if frame.any2full_depth is not None else frame.aligned_depth
    prompt = build_cursor_prompt(
        frame.meta,
        seed_cursor,
        depth,
        CursorPromptConfig(nearest_depth_radius_px=args.cursor_nearest_depth_radius_px),
    )
    if not prompt.get("valid", False):
        sx, sy = seed["rgb_xy"]
        prompt = {
            "valid": True,
            "reason": "ok_from_stored_pixel",
            "rgb_x": int(sx),
            "rgb_y": int(sy),
            "point_coords": [[int(sx), int(sy)]],
            "point_labels": [1],
            "world_xyz_m": seed.get("world_xyz_m"),
            "cursor": seed_cursor,
        }
    prompt["user_point_coords"] = [[int(coord[0]), int(coord[1])] for coord in coords]
    prompt["user_point_labels"] = labels
    prompt["sam_point_coords"] = prompt["user_point_coords"]
    prompt["sam_point_labels"] = labels
    prompt["point_coords"] = prompt["sam_point_coords"]
    prompt["point_labels"] = labels
    prompt["room_id"] = store.get("room_id")
    prompt["device_id"] = store.get("device_id")
    prompt["object_id"] = image_record.get("object_id")
    prompt["point_count"] = len(coords)
    prompt["positive_point_count"] = sum(1 for label in labels if label > 0)
    prompt["negative_point_count"] = sum(1 for label in labels if label <= 0)
    frame.meta["cursor"] = seed_cursor
    frame.meta["cursor_prompt"] = prompt
    return prompt


def _apply_room_segmentation(
    frame: FrameData,
    viewer: "RgbdViewer",
    device_dir: Path,
    store: dict,
    image_record: dict,
    cursor: dict,
) -> FrameData:
    prompt = _build_prompt_from_room_points(frame, viewer.args, store, image_record, cursor)
    frame.meta["cursor_prompt"] = prompt
    if prompt.get("valid", False):
        frame = run_device_segmentation(frame, viewer.args, viewer.device_segmenter)
        image_record["last_segmented_at_ms"] = _now_ms()
        if frame.device_mask_path is not None:
            image_record["device_mask"] = str(frame.device_mask_path)
        _save_point_store(device_dir, store)
    else:
        print(f"[room-store] segmentation skipped: {prompt.get('reason')}", flush=True)
        image_record.pop("device_mask", None)
        _save_point_store(device_dir, store)
    return frame


def _load_room_frame(viewer: "RgbdViewer", device_dir: Path, image_record: dict) -> FrameData:
    capture_dir = _resolve_store_path(device_dir, image_record.get("capture_dir"))
    return load_frame(
        capture_dir,
        viewer.args.min_depth,
        viewer.args.max_depth,
        viewer.args.mode,
        viewer.args.depth_origin,
    )


class _PayloadHandler(http.server.BaseHTTPRequestHandler):
    viewer_ref: "RgbdViewer | None" = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/room/preview/list":
            self._handle_room_preview_list(parsed)
            return
        if parsed.path == "/api/room/preview/image":
            self._handle_room_preview_image(parsed)
            return
        if parsed.path == "/api/room/object/list":
            self._handle_room_object_list(parsed)
            return
        if parsed.path == "/api/room/object/thumbnail":
            self._handle_room_object_thumbnail(parsed)
            return
        if parsed.path == "/api/room/object/points":
            self._handle_room_object_points(parsed)
            return
        if parsed.path == "/api/room/object/pairing/candidates":
            self._handle_room_object_pairing_candidates(parsed)
            return
        if parsed.path == "/api/discover/status":
            self._handle_discover_status()
            return
        if parsed.path == "/api/discover/devices":
            self._handle_discover_devices()
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/api/room/object/begin_edit":
            self._handle_room_object_begin_edit()
            return

        if self.path == "/api/room/object/complete":
            self._handle_room_object_complete()
            return

        if self.path == "/api/room/object/abandon":
            self._handle_room_object_abandon()
            return

        if self.path == "/api/room/object/delete":
            self._handle_room_object_delete()
            return

        if self.path == "/api/room/object/rename":
            self._handle_room_object_rename()
            return

        if self.path == "/api/room/object/pairing/refresh":
            self._handle_room_object_pairing_refresh()
            return

        if self.path == "/api/room/object/pairing/bind":
            self._handle_room_object_pairing_bind()
            return

        if self.path == "/api/room/object/pairing/unbind":
            self._handle_room_object_pairing_unbind()
            return

        if self.path == "/api/room/point/delete":
            self._handle_room_point_delete()
            return

        if self.path == "/api/room/point":
            self._handle_room_point()
            return

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
        try:
            cursor_payload = json.loads(cursor_json.decode("utf-8")) if cursor_json is not None else None
        except Exception as exc:
            self.send_error(400, f"invalid cursor_json: {exc}")
            return
        if isinstance(cursor_payload, dict) and not str(cursor_payload.get("logical_point_id") or "").strip():
            timestamp_ms = int(cursor_payload.get("timestamp_ms") or _now_ms())
            cursor_payload["logical_point_id"] = f"lp_{timestamp_ms}"
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

        try:
            rgb_payload_bytes = rgb_raw if rgb_raw is not None else rgb_jpeg
            rgb_format = "raw_rgb24" if rgb_raw is not None else "jpeg"
            frame = load_frame_from_payload(
                rgb_payload_bytes=rgb_payload_bytes,
                rgb_format=rgb_format,
                depth_raw_bytes=depth_raw,
                meta_json_str=meta_json.decode("utf-8"),
                min_depth=viewer.args.min_depth,
                max_depth=viewer.args.max_depth,
                cursor_json_str=cursor_json.decode("utf-8") if cursor_json is not None else None,
            )
            room_device_dir = None
            room_store = None
            room_image = None
            if isinstance(cursor_payload, dict) and (cursor_payload.get("room_id") or cursor_payload.get("room_name")):
                _ctx, room_device_dir, room_store, room_image = _create_room_capture(
                    frame,
                    viewer,
                    rgb_payload_bytes,
                    rgb_format,
                    depth_raw,
                    meta_json,
                    cursor_payload,
                )

            frame = run_any2full_completion(frame, viewer.args, viewer.any2full_service)
            if room_image is not None and room_device_dir is not None and room_store is not None:
                capture_dir = _resolve_store_path(room_device_dir, room_image.get("capture_dir"))
                _finalize_room_capture_frame(frame, capture_dir)
                preseeded_count = _preseed_visible_points_for_new_image(
                    viewer.args,
                    room_device_dir,
                    room_store,
                    room_image,
                    frame,
                    cursor_payload,
                )
                if preseeded_count:
                    print(f"[room-store] preseeded {preseeded_count} visible point(s) into {room_image.get('image_id')}", flush=True)
                depth = frame.any2full_depth if frame.any2full_depth is not None else frame.aligned_depth
                prompt = build_cursor_prompt(
                    frame.meta,
                    cursor_payload,
                    depth,
                    CursorPromptConfig(nearest_depth_radius_px=viewer.args.cursor_nearest_depth_radius_px),
                )
                if prompt.get("valid", False):
                    _append_room_point(viewer.args, room_device_dir, room_store, room_image, cursor_payload, prompt)
                else:
                    print(f"[room-store] uploaded point outside new image: {prompt.get('reason')}", flush=True)
                frame = _apply_room_segmentation(frame, viewer, room_device_dir, room_store, room_image, cursor_payload)
            else:
                frame = run_device_segmentation(frame, viewer.args, viewer.device_segmenter)
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
            },
            "cloud_points": int(frame.cloud_points.shape[0]),
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_payload(self) -> tuple[dict | None, str | None]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None, "invalid_content_length"
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except Exception as exc:
            return None, f"invalid_json: {exc}"
        if not isinstance(payload, dict):
            return None, "json_body_must_be_object"
        return payload, None

    def _handle_room_point(self) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "needs_capture": True, "reason": "viewer_not_ready"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"ok": False, "needs_capture": True, "reason": "invalid_content_length"})
            return

        try:
            cursor = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except Exception as exc:
            self._send_json(400, {"ok": False, "needs_capture": True, "reason": f"invalid_json: {exc}"})
            return
        if not str(cursor.get("logical_point_id") or "").strip():
            timestamp_ms = int(cursor.get("timestamp_ms") or _now_ms())
            cursor["logical_point_id"] = f"lp_{timestamp_ms}"

        try:
            _ctx, device_dir, store, image_record, projected = _find_matching_room_image(viewer, cursor)
            if image_record is None or projected is None:
                _save_point_store(device_dir, store)
                reason = "forced_new_capture" if bool(cursor.get("force_new_capture", False)) else "no_matching_image"
                self._send_json(200, {"ok": True, "needs_capture": True, "reason": reason})
                return

            object_id = image_record.get("object_id") or _room_context(cursor)["object_id"]
            visible_matches = _project_cursor_to_visible_object_images(viewer, device_dir, store, cursor, object_id)
            primary_image_id = str(image_record.get("image_id") or "")
            if not any(str(match_image.get("image_id") or "") == primary_image_id for match_image, _prompt in visible_matches):
                visible_matches.append((image_record, projected))

            added = False
            point = None
            affected_image_ids: list[str] = []
            frame = None
            primary_frame = None
            primary_point = None
            for match_image, match_prompt in visible_matches:
                point_added, point_record = _append_room_point(viewer.args, device_dir, store, match_image, cursor, match_prompt)
                added = added or point_added
                point = point_record
                if str(match_image.get("image_id") or "") == primary_image_id:
                    primary_point = point_record
                if point_added:
                    affected_image_ids.append(str(match_image.get("image_id") or ""))
                current_frame = _load_room_frame(viewer, device_dir, match_image)
                current_frame = _apply_room_segmentation(current_frame, viewer, device_dir, store, match_image, cursor)
                if str(match_image.get("image_id") or "") == primary_image_id:
                    primary_frame = current_frame
                frame = current_frame
            if primary_frame is not None:
                frame = primary_frame
            if primary_point is not None:
                point = primary_point
        except Exception as exc:
            print(f"[room-store] point handling failed: {exc}", flush=True)
            self._send_json(500, {"ok": False, "needs_capture": True, "reason": str(exc)})
            return

        viewer.root.after(0, lambda: viewer._on_network_frame(frame))
        self._send_json(
            200,
            {
                "ok": True,
                "needs_capture": False,
                "reason": "matched_existing_image",
                "image_id": image_record.get("image_id"),
                "affected_image_ids": affected_image_ids,
                "point_added": added,
                "point": point,
            },
        )

    def _handle_room_point_delete(self) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "deleted": False, "reason": "viewer_not_ready"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"ok": False, "deleted": False, "reason": "invalid_content_length"})
            return

        try:
            cursor = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except Exception as exc:
            self._send_json(400, {"ok": False, "deleted": False, "reason": f"invalid_json: {exc}"})
            return

        try:
            _ctx, device_dir, store = _load_point_store(viewer.args, cursor)
            removed_points = _remove_room_points_near_world(viewer.args, store, cursor)
            if not removed_points:
                self._send_json(200, {"ok": True, "deleted": False, "reason": "no_point_nearby"})
                return

            affected_image_ids = sorted({str(point.get("image_id")) for point in removed_points if point.get("image_id")})
            image_records = [
                image
                for image in store.get("images", [])
                if str(image.get("image_id")) in affected_image_ids
            ]
            for image_record in image_records:
                image_record["updated_at_ms"] = _now_ms()
            _save_point_store(device_dir, store)

            frame = None
            for image_record in image_records:
                frame = _load_room_frame(viewer, device_dir, image_record)
                frame = _apply_room_segmentation(frame, viewer, device_dir, store, image_record, cursor)
        except Exception as exc:
            print(f"[room-store] point delete failed: {exc}", flush=True)
            self._send_json(500, {"ok": False, "deleted": False, "reason": str(exc)})
            return

        if frame is not None:
            viewer.root.after(0, lambda: viewer._on_network_frame(frame))
        self._send_json(
            200,
            {
                "ok": True,
                "deleted": True,
                "reason": "deleted",
                "image_ids": affected_image_ids,
                "deleted_count": len(removed_points),
                "point": removed_points[0],
                "points": removed_points,
            },
        )

    def _handle_room_preview_list(self, parsed: urllib.parse.ParseResult) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready", "images": []})
            return

        query = urllib.parse.parse_qs(parsed.query)
        cursor = _preview_query_to_cursor(query)
        try:
            ctx, _device_dir, store = _load_point_store(viewer.args, cursor)
            object_id = ctx["object_id"]
            image_records = [
                image
                for image in store.get("images", [])
                if str(image.get("object_id") or "object_default") == object_id
            ]
            image_records.sort(key=lambda image: int(image.get("created_at_ms") or 0))
            records = _room_preview_records(store, image_records)
            selected_index = max(0, len(records) - 1) if records else -1
        except Exception as exc:
            print(f"[room-preview] list failed: {exc}", flush=True)
            self._send_json(500, {"ok": False, "reason": str(exc), "images": []})
            return

        payload = {
            "ok": True,
            "room_id": ctx["room_id"],
            "device_id": ctx["device_id"],
            "object_id": object_id,
            "selected_index": selected_index,
            "images": records,
        }
        self._send_json(200, payload)

    def _handle_room_preview_image(self, parsed: urllib.parse.ParseResult) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready"})
            return

        query = urllib.parse.parse_qs(parsed.query)
        cursor = _preview_query_to_cursor(query)
        image_id = (query.get("image_id") or [""])[0]
        if not image_id:
            self.send_error(400, "missing image_id")
            return

        try:
            ctx, device_dir, store = _load_point_store(viewer.args, cursor)
            object_id = ctx["object_id"]
            image_record = next(
                (
                    image
                    for image in store.get("images", [])
                    if str(image.get("image_id") or "") == image_id
                    and str(image.get("object_id") or "object_default") == object_id
                ),
                None,
            )
            if image_record is None:
                self.send_error(404, "image not found")
                return
            body = _build_room_preview_image(viewer, device_dir, store, image_record)
        except Exception as exc:
            print(f"[room-preview] image failed: {exc}", flush=True)
            self.send_error(500, str(exc))
            return

        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_room_object_list(self, parsed: urllib.parse.ParseResult) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready", "objects": []})
            return

        query = urllib.parse.parse_qs(parsed.query)
        cursor = _preview_query_to_cursor(query)
        try:
            ctx, device_dir, store = _load_point_store(viewer.args, cursor)
            spatial_changed = False
            for record in _object_records(store):
                if str(record.get("status") or "") != "completed":
                    continue
                object_id = str(record.get("object_id") or "")
                spatial = record.get("spatial")
                if object_id and (not isinstance(spatial, dict) or not spatial.get("valid", False)):
                    record["spatial"] = _compute_object_spatial_summary(viewer.args, device_dir, store, object_id)
                    spatial_changed = True
            if spatial_changed:
                _save_point_store(device_dir, store)
            records = _completed_object_records(store)
        except Exception as exc:
            print(f"[room-object] list failed: {exc}", flush=True)
            self._send_json(500, {"ok": False, "reason": str(exc), "objects": []})
            return

        self._send_json(
            200,
            {
                "ok": True,
                "room_id": ctx["room_id"],
                "device_id": ctx["device_id"],
                "objects": records,
            },
        )

    def _handle_room_object_thumbnail(self, parsed: urllib.parse.ParseResult) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self.send_error(503, "viewer not ready")
            return

        query = urllib.parse.parse_qs(parsed.query)
        cursor = _preview_query_to_cursor(query)
        object_id = _safe_path_component((query.get("object_id") or query.get("object_session_id") or [""])[0], "")
        if not object_id:
            self.send_error(400, "missing object_id")
            return

        try:
            _ctx, device_dir, store = _load_point_store(viewer.args, cursor)
            if _find_object_record(store, object_id) is None:
                self.send_error(404, "object not found")
                return
            body = _build_object_thumbnail(viewer, device_dir, store, object_id)
        except Exception as exc:
            print(f"[room-object] thumbnail failed: {exc}", flush=True)
            self.send_error(500, str(exc))
            return

        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_room_object_points(self, parsed: urllib.parse.ParseResult) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready", "points": []})
            return

        query = urllib.parse.parse_qs(parsed.query)
        cursor = _preview_query_to_cursor(query)
        object_id = _safe_path_component((query.get("object_id") or query.get("object_session_id") or [""])[0], "")
        if not object_id:
            self._send_json(400, {"ok": False, "reason": "missing_object_id", "points": []})
            return

        try:
            _ctx, _device_dir, store = _load_point_store(viewer.args, cursor)
            points = _object_spatial_points(store, object_id)
        except Exception as exc:
            print(f"[room-object] points failed: {exc}", flush=True)
            self._send_json(500, {"ok": False, "reason": str(exc), "points": []})
            return

        self._send_json(200, {"ok": True, "object_id": object_id, "points": points})

    def _handle_room_object_begin_edit(self) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready", "points": []})
            return

        payload, error = self._read_json_payload()
        if error is not None:
            self._send_json(400, {"ok": False, "reason": error, "points": []})
            return

        object_id = _safe_path_component(payload.get("object_id") or payload.get("object_session_id"), "")
        if not object_id:
            self._send_json(400, {"ok": False, "reason": "missing_object_id", "points": []})
            return

        try:
            ctx, device_dir, store = _load_point_store(viewer.args, payload)
            record = _find_object_record(store, object_id)
            if record is None or str(record.get("status") or "") != "completed":
                self._send_json(404, {"ok": False, "reason": "completed_object_not_found", "points": []})
                return
            spatial = record.get("spatial")
            if not isinstance(spatial, dict) or not spatial.get("valid", False):
                record["spatial"] = _compute_object_spatial_summary(viewer.args, device_dir, store, object_id)
                _save_point_store(device_dir, store)
            edit_session_id, points = _begin_object_edit(device_dir, store, object_id)
        except Exception as exc:
            print(f"[room-object] begin_edit failed: {exc}", flush=True)
            self._send_json(500, {"ok": False, "reason": str(exc), "points": []})
            return

        self._send_json(
            200,
            {
                "ok": True,
                "room_id": ctx["room_id"],
                "device_id": ctx["device_id"],
                "object_id": object_id,
                "edit_session_id": edit_session_id,
                "name": str(record.get("name") or object_id),
                "spatial": record.get("spatial") or _empty_object_spatial_summary("missing"),
                "points": points,
                "pairing": _pairing_record_payload(record, candidate_limit=10, compact_profile=True),
            },
        )

    def _handle_room_object_complete(self) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready"})
            return

        payload, error = self._read_json_payload()
        if error is not None:
            self._send_json(400, {"ok": False, "reason": error})
            return

        object_id = _safe_path_component(payload.get("object_id") or payload.get("object_session_id"), "")
        if not object_id:
            self._send_json(400, {"ok": False, "reason": "missing_object_id"})
            return
        edit_session_id = str(payload.get("edit_session_id") or "")

        try:
            ctx, device_dir, store = _load_point_store(viewer.args, payload)
            record = _complete_object(viewer.args, device_dir, store, object_id, edit_session_id)
            _schedule_vlm_object_analysis(viewer, payload, object_id)
            _ctx, device_dir, store = _load_point_store(viewer.args, payload)
            record = _find_object_record(store, object_id) or record
        except ValueError as exc:
            self._send_json(400, {"ok": False, "reason": str(exc)})
            return
        except Exception as exc:
            print(f"[room-object] complete failed: {exc}", flush=True)
            self._send_json(500, {"ok": False, "reason": str(exc)})
            return

        self._send_json(
            200,
            {
                "ok": True,
                "room_id": ctx["room_id"],
                "device_id": ctx["device_id"],
                "object_id": object_id,
                "object": record,
                "spatial": record.get("spatial") or _empty_object_spatial_summary("missing"),
                "points": _object_spatial_points(store, object_id),
                "pairing": _pairing_record_payload(record, candidate_limit=10, compact_profile=True),
            },
        )
        viewer.root.after(0, viewer.refresh_device_tree)

    def _handle_room_object_abandon(self) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready"})
            return

        payload, error = self._read_json_payload()
        if error is not None:
            self._send_json(400, {"ok": False, "reason": error})
            return

        object_id = _safe_path_component(payload.get("object_id") or payload.get("object_session_id"), "")
        if not object_id:
            self._send_json(400, {"ok": False, "reason": "missing_object_id"})
            return
        edit_session_id = str(payload.get("edit_session_id") or "")

        try:
            _ctx, device_dir, store = _load_point_store(viewer.args, payload)
            if edit_session_id:
                store = _restore_object_edit_backup(device_dir, edit_session_id)
                mode = "restored_edit_snapshot"
            else:
                removed_images = _remove_object_content(device_dir, store, object_id)
                _save_point_store(device_dir, store)
                mode = f"deleted_unfinished_object_images_{removed_images}"
        except Exception as exc:
            print(f"[room-object] abandon failed: {exc}", flush=True)
            self._send_json(500, {"ok": False, "reason": str(exc)})
            return

        self._send_json(
            200,
            {
                "ok": True,
                "object_id": object_id,
                "reason": mode,
                "points": _object_spatial_points(store, object_id),
            },
        )

    def _handle_room_object_delete(self) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready"})
            return

        payload, error = self._read_json_payload()
        if error is not None:
            self._send_json(400, {"ok": False, "reason": error})
            return

        object_id = _safe_path_component(payload.get("object_id") or payload.get("object_session_id"), "")
        if not object_id:
            self._send_json(400, {"ok": False, "reason": "missing_object_id"})
            return

        try:
            _ctx, device_dir, store = _load_point_store(viewer.args, payload)
            removed_images = _remove_object_content(device_dir, store, object_id)
            _save_point_store(device_dir, store)
        except Exception as exc:
            print(f"[room-object] delete failed: {exc}", flush=True)
            self._send_json(500, {"ok": False, "reason": str(exc)})
            return

        self._send_json(200, {"ok": True, "object_id": object_id, "deleted_images": removed_images})
        viewer.root.after(0, viewer.refresh_device_tree)

    def _handle_room_object_rename(self) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready"})
            return

        payload, error = self._read_json_payload()
        if error is not None:
            self._send_json(400, {"ok": False, "reason": error})
            return

        object_id = _safe_path_component(payload.get("object_id") or payload.get("object_session_id"), "")
        name = str(payload.get("name") or "").strip()
        if not object_id:
            self._send_json(400, {"ok": False, "reason": "missing_object_id"})
            return
        if not name:
            self._send_json(400, {"ok": False, "reason": "missing_name"})
            return

        try:
            _ctx, device_dir, store = _load_point_store(viewer.args, payload)
            record = _find_object_record(store, object_id)
            if record is None:
                self._send_json(404, {"ok": False, "reason": "object_not_found"})
                return
            record["name"] = name
            record["updated_at_ms"] = _now_ms()
            _save_point_store(device_dir, store)
        except Exception as exc:
            print(f"[room-object] rename failed: {exc}", flush=True)
            self._send_json(500, {"ok": False, "reason": str(exc)})
            return

        self._send_json(200, {"ok": True, "object_id": object_id, "object": record})
        viewer.root.after(0, viewer.refresh_device_tree)

    def _handle_discover_status(self) -> None:
        viewer = self.viewer_ref
        runtime = getattr(viewer, "discover_runtime", None) if viewer is not None else None
        if runtime is None:
            self._send_json(503, {"ok": False, "reason": "discovery_not_running"})
            return
        self._send_json(200, {"ok": True, **runtime.status()})

    def _handle_discover_devices(self) -> None:
        viewer = self.viewer_ref
        runtime = getattr(viewer, "discover_runtime", None) if viewer is not None else None
        if runtime is None:
            self._send_json(503, {"ok": False, "reason": "discovery_not_running", "devices": []})
            return
        self._send_json(200, {"ok": True, "devices": runtime.profiles()})

    def _handle_room_object_pairing_candidates(self, parsed: urllib.parse.ParseResult) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready", "candidates": []})
            return
        query = urllib.parse.parse_qs(parsed.query)
        cursor = _preview_query_to_cursor(query)
        object_id = _safe_path_component((query.get("object_id") or [""])[0], "")
        if not object_id:
            self._send_json(400, {"ok": False, "reason": "missing_object_id", "candidates": []})
            return
        try:
            _ctx, _device_dir, store = _load_point_store(viewer.args, cursor)
            record = _find_object_record(store, object_id)
            if record is None:
                self._send_json(404, {"ok": False, "reason": "object_not_found", "candidates": []})
                return
            limit_raw = (query.get("limit") or [""])[0]
            try:
                limit = int(limit_raw) if str(limit_raw).strip() else None
            except ValueError:
                limit = None
            compact = str((query.get("compact") or [""])[0]).lower() in {"1", "true", "yes"}
            payload = _pairing_record_payload(record, candidate_limit=limit, compact_profile=compact)
        except Exception as exc:
            self._send_json(500, {"ok": False, "reason": str(exc), "candidates": []})
            return
        self._send_json(200, {"ok": True, "object_id": object_id, **payload})

    def _handle_room_object_pairing_refresh(self) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready"})
            return
        if viewer.discover_runtime is None:
            self._send_json(503, {"ok": False, "reason": "discovery_not_running"})
            return
        payload, error = self._read_json_payload()
        if error is not None:
            self._send_json(400, {"ok": False, "reason": error})
            return
        object_id = _safe_path_component(payload.get("object_id") or payload.get("object_session_id"), "")
        if not object_id:
            self._send_json(400, {"ok": False, "reason": "missing_object_id"})
            return
        try:
            _ctx, device_dir, store = _load_point_store(viewer.args, payload)
            record = _find_object_record(store, object_id)
            if record is None:
                self._send_json(404, {"ok": False, "reason": "object_not_found"})
                return
            visual_profile = _object_visual_profile(record)
            if str(record.get("vlm_status") or "") == "processing":
                self._send_json(
                    202,
                    {
                        "ok": True,
                        "object_id": object_id,
                        "vlm_status": "processing",
                        "pairing_status": "waiting_for_vlm",
                    },
                )
                return
            if str(record.get("vlm_status") or "") != "done" or not _visual_profile_has_evidence(visual_profile):
                if _schedule_vlm_object_analysis(viewer, payload, object_id):
                    self._send_json(
                        202,
                        {"ok": True, "object_id": object_id, "vlm_status": "processing", "pairing_status": "waiting_for_vlm"},
                    )
                else:
                    self._send_json(
                        409,
                        {"ok": False, "object_id": object_id, "reason": "vlm_not_configured"},
                    )
                return
            record["pairing_status"] = "processing"
            record["pairing_started_at_ms"] = _now_ms()
            record["pairing_error"] = ""
            record["pairing_warning"] = ""
            record["pairing_candidates"] = []
            _save_point_store(device_dir, store)
            _schedule_object_pairing_analysis(viewer, payload, object_id)
        except Exception as exc:
            self._send_json(500, {"ok": False, "reason": str(exc)})
            return
        self._send_json(202, {"ok": True, "object_id": object_id, "pairing_status": "processing"})

    def _handle_room_object_pairing_bind(self) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready"})
            return
        payload, error = self._read_json_payload()
        if error is not None:
            self._send_json(400, {"ok": False, "reason": error})
            return
        object_id = _safe_path_component(payload.get("object_id") or payload.get("object_session_id"), "")
        canonical_id = str(payload.get("canonical_device_id") or "").strip()
        if not object_id or not canonical_id:
            self._send_json(400, {"ok": False, "reason": "missing_object_or_canonical_device_id"})
            return
        try:
            ctx, device_dir, store = _load_point_store(viewer.args, payload)
            record = _find_object_record(store, object_id)
            if record is None:
                self._send_json(404, {"ok": False, "reason": "object_not_found"})
                return
            conflict = _find_binding_conflict(
                viewer.args,
                ctx["room_id"],
                canonical_id,
                device_dir,
                object_id,
            )
            if conflict is not None:
                self._send_json(409, {"ok": False, "reason": "device_already_bound", "conflict": conflict})
                return
            candidates = record.get("pairing_candidates") or []
            candidate = next(
                (
                    item
                    for item in candidates
                    if str(item.get("canonical_device_id") or "") == canonical_id
                ),
                None,
            )
            runtime_profiles = viewer.discover_runtime.profiles() if viewer.discover_runtime is not None else []
            profile = next(
                (
                    item
                    for item in runtime_profiles
                    if str(item.get("canonical_device_id") or "") == canonical_id
                ),
                None,
            )
            if profile is None and isinstance(candidate, dict):
                profile = candidate.get("profile")
            if not isinstance(profile, dict):
                self._send_json(404, {"ok": False, "reason": "network_device_not_found"})
                return
            binding = {
                "canonical_device_id": canonical_id,
                "display_name": str(profile.get("display_name") or canonical_id),
                "method": "semantic_match_manual_confirmation",
                "score": int((candidate or {}).get("score") or 0),
                "evidence_coverage_percent": int((candidate or {}).get("evidence_coverage_percent") or 0),
                "bound_at_ms": _now_ms(),
                "profile_snapshot": profile,
            }
            history = record.setdefault("binding_history", [])
            if isinstance(history, list):
                previous = record.get("network_binding")
                if isinstance(previous, dict) and str(previous.get("canonical_device_id") or "") != canonical_id:
                    history.append({"action": "replaced", "at_ms": _now_ms(), "previous": previous})
                history.append({"action": "bound", "at_ms": binding["bound_at_ms"], "binding": binding})
            record["network_binding"] = binding
            record["updated_at_ms"] = _now_ms()
            _save_point_store(device_dir, store)
        except Exception as exc:
            self._send_json(500, {"ok": False, "reason": str(exc)})
            return
        self._send_json(200, {"ok": True, "object_id": object_id, "binding": binding})
        viewer.root.after(0, viewer.refresh_device_tree)

    def _handle_room_object_pairing_unbind(self) -> None:
        viewer = self.viewer_ref
        if viewer is None:
            self._send_json(503, {"ok": False, "reason": "viewer_not_ready"})
            return
        payload, error = self._read_json_payload()
        if error is not None:
            self._send_json(400, {"ok": False, "reason": error})
            return
        object_id = _safe_path_component(payload.get("object_id") or payload.get("object_session_id"), "")
        if not object_id:
            self._send_json(400, {"ok": False, "reason": "missing_object_id"})
            return
        try:
            _ctx, device_dir, store = _load_point_store(viewer.args, payload)
            record = _find_object_record(store, object_id)
            if record is None:
                self._send_json(404, {"ok": False, "reason": "object_not_found"})
                return
            record.pop("network_binding", None)
            record["updated_at_ms"] = _now_ms()
            _save_point_store(device_dir, store)
        except Exception as exc:
            self._send_json(500, {"ok": False, "reason": str(exc)})
            return
        self._send_json(200, {"ok": True, "object_id": object_id})
        viewer.root.after(0, viewer.refresh_device_tree)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
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
        self._any2full_start_thread: threading.Thread | None = None
        self._segmenter_start_thread: threading.Thread | None = None
        self._model_startup_lock = threading.Lock()
        self._closing = False
        self.discover_runtime: DiscoverRuntime | None = None
        self._pairing_lock = threading.RLock()
        self._analysis_jobs_lock = threading.Lock()
        self._vlm_jobs: set[tuple[str, str, str]] = set()
        self._pairing_jobs: set[tuple[str, str, str]] = set()
        self._device_tree_item_context: dict[str, dict] = {}
        self._network_tree_item_context: dict[str, dict] = {}
        self._network_operation_context: dict[str, dict] = {}
        self._network_profile_item_context: dict[str, dict] = {}
        self._discover_configs: list[SourceConfig] = []
        self._network_refresh_after_id: str | None = None
        self._network_profiles_pending = threading.Event()
        self._discovery_restart_completed = threading.Event()
        self._last_network_profile_revision = -1
        self._network_profiles_snapshot: list[dict] = []
        self._vlm_last_test_ok = False
        self._vlm_last_test_signature = ""

        self.root = tk.Tk()
        self.root.title("Quest 3 RGB-D Alignment Viewer")
        self.root.geometry(f"{max(args.view_size, args.cloud_width) + 80}x{args.cloud_height + 150}")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        if not self.args.disable_discovery:
            self._start_discover_runtime()
            self.root.after(250, self.refresh_network_devices)
        if self.args.server and not self.args.disable_device_segmentation:
            self._start_device_segmenter_background()
        if self.args.server and not self.args.disable_any2full:
            self._start_any2full_background()
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
        self.main_notebook = notebook
        notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        rgb_tab = ttk.Frame(notebook, padding=8)
        cloud_tab = ttk.Frame(notebook, padding=8)
        devices_tab = ttk.Frame(notebook, padding=8)
        network_tab = ttk.Frame(notebook, padding=8)
        network_data_tab = ttk.Frame(notebook, padding=8)
        network_operations_tab = ttk.Frame(notebook, padding=8)
        network_profiles_tab = ttk.Frame(notebook, padding=8)
        discovery_tab = ttk.Frame(notebook, padding=8)
        vlm_tab = ttk.Frame(notebook, padding=8)
        notebook.add(rgb_tab, text="RGB depth")
        notebook.add(cloud_tab, text="Point cloud")
        notebook.add(devices_tab, text="Room Devices")
        notebook.add(network_tab, text="Network Devices")
        notebook.add(network_data_tab, text="Data")
        notebook.add(network_operations_tab, text="Operations")
        notebook.add(network_profiles_tab, text="Device Profiles")
        notebook.add(discovery_tab, text="Discovery Settings")
        notebook.add(vlm_tab, text="VLM Settings")
        self._network_data_tab = network_data_tab
        self._network_operations_tab = network_operations_tab
        self._network_profiles_tab = network_profiles_tab
        notebook.bind("<<NotebookTabChanged>>", self._on_main_tab_changed)

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

        self._build_device_tree_tab(devices_tab)
        self._build_network_devices_tab(network_tab)
        self._build_network_data_tab(network_data_tab)
        self._build_network_operations_tab(network_operations_tab)
        self._build_network_profiles_tab(network_profiles_tab)
        self._build_discovery_settings_tab(discovery_tab)
        self._build_vlm_settings_tab(vlm_tab)

    def _build_device_tree_tab(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        ttk.Button(toolbar, text="Refresh", command=self.refresh_device_tree).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Retry VLM", command=self.retry_selected_vlm).pack(side=tk.LEFT, padx=(8, 0))
        self.device_tree_status_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self.device_tree_status_var).pack(side=tk.LEFT, padx=12)

        columns = ("name", "completed", "images", "vlm", "network")
        self.device_tree = ttk.Treeview(parent, columns=columns, show="tree headings", height=18)
        self.device_tree.heading("#0", text="Room / Quest / Device")
        self.device_tree.heading("name", text="Name")
        self.device_tree.heading("completed", text="Completed")
        self.device_tree.heading("images", text="Images")
        self.device_tree.heading("vlm", text="VLM")
        self.device_tree.heading("network", text="Network Pairing")
        ttk.Style().configure("Device.Treeview", rowheight=56)
        self.device_tree.configure(style="Device.Treeview")
        self.device_tree.column("#0", width=320, anchor=tk.W)
        self.device_tree.column("name", width=220, anchor=tk.W)
        self.device_tree.column("completed", width=160, anchor=tk.W)
        self.device_tree.column("images", width=80, anchor=tk.CENTER)
        self.device_tree.column("vlm", width=120, anchor=tk.W)
        self.device_tree.column("network", width=220, anchor=tk.W)
        tree_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.device_tree.yview)
        self.device_tree.configure(yscrollcommand=tree_scroll.set)
        self.device_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.device_tree.bind("<Double-1>", self.on_device_tree_double_click)
        self.refresh_device_tree()

    def _build_vlm_settings_tab(self, parent: ttk.Frame) -> None:
        config = _load_vlm_config(self.args)
        self.vlm_base_url_var = tk.StringVar(value=str(config.get("base_url") or ""))
        self.vlm_token_var = tk.StringVar(value=str(config.get("token") or ""))
        self.vlm_model_var = tk.StringVar(value=str(config.get("model") or ""))
        self.vlm_status_var = tk.StringVar(value="VLM config loaded" if config.get("model") else "Configure VLM endpoint")

        form = ttk.Frame(parent)
        form.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(form, text="Base URL").grid(row=0, column=0, sticky=tk.W, padx=(0, 8), pady=6)
        ttk.Entry(form, textvariable=self.vlm_base_url_var, width=72).grid(row=0, column=1, sticky=tk.EW, pady=6)
        ttk.Label(form, text="Token").grid(row=1, column=0, sticky=tk.W, padx=(0, 8), pady=6)
        ttk.Entry(form, textvariable=self.vlm_token_var, width=72, show="*").grid(row=1, column=1, sticky=tk.EW, pady=6)
        ttk.Label(form, text="Model").grid(row=2, column=0, sticky=tk.W, padx=(0, 8), pady=6)
        self.vlm_model_combo = ttk.Combobox(form, textvariable=self.vlm_model_var, values=(), width=68)
        self.vlm_model_combo.grid(row=2, column=1, sticky=tk.EW, pady=6)
        form.columnconfigure(1, weight=1)

        actions = ttk.Frame(parent)
        actions.pack(side=tk.TOP, fill=tk.X, pady=(10, 6))
        ttk.Button(actions, text="Connect / Load Models", command=self.on_vlm_connect).pack(side=tk.LEFT)
        ttk.Button(actions, text="Test", command=self.on_vlm_test).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Save", command=self.on_vlm_save).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(actions, textvariable=self.vlm_status_var).pack(side=tk.LEFT, padx=14)

        prompt_group = ttk.LabelFrame(parent, text="Device Analysis Prompt", padding=8)
        prompt_group.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(8, 0))
        prompt_text = scrolledtext.ScrolledText(prompt_group, height=12, wrap=tk.WORD)
        prompt_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        prompt_text.insert(tk.END, _vlm_object_prompt())
        prompt_text.configure(state=tk.DISABLED)

    def _build_network_devices_tab(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        ttk.Button(toolbar, text="Refresh", command=lambda: self.refresh_network_devices(force=True)).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Copy Rows", command=self.copy_selected_network_rows).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(toolbar, text="Copy JSON", command=self.copy_selected_network_json).pack(side=tk.LEFT, padx=(8, 0))
        self.network_device_status_var = tk.StringVar(value="Discovery runtime is starting")
        ttk.Label(toolbar, textvariable=self.network_device_status_var).pack(side=tk.LEFT, padx=12)

        columns = (
            "entity",
            "channels",
            "topics",
            "type",
            "data",
            "operations",
            "address",
            "evidence",
            "status",
        )
        table = ttk.Frame(parent)
        table.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.network_device_tree = ttk.Treeview(
            table,
            columns=columns,
            show="tree headings",
            height=18,
            selectmode="extended",
        )
        self.network_device_tree.heading("#0", text="Discovered Device")
        self.network_device_tree.heading("entity", text="Physical Entity Prefix")
        self.network_device_tree.heading("channels", text="Channels")
        self.network_device_tree.heading("topics", text="Topics")
        self.network_device_tree.heading("type", text="Type")
        self.network_device_tree.heading("data", text="Data")
        self.network_device_tree.heading("operations", text="Operations")
        self.network_device_tree.heading("address", text="IP / MAC")
        self.network_device_tree.heading("evidence", text="Evidence")
        self.network_device_tree.heading("status", text="Status")
        self.network_device_tree.column("#0", width=290, anchor=tk.W)
        self.network_device_tree.column("entity", width=340, anchor=tk.W)
        self.network_device_tree.column("channels", width=180, anchor=tk.W)
        self.network_device_tree.column("topics", width=70, anchor=tk.CENTER)
        self.network_device_tree.column("type", width=170, anchor=tk.W)
        self.network_device_tree.column("data", width=70, anchor=tk.CENTER)
        self.network_device_tree.column("operations", width=90, anchor=tk.CENTER)
        self.network_device_tree.column("address", width=230, anchor=tk.W)
        self.network_device_tree.column("evidence", width=90, anchor=tk.CENTER)
        self.network_device_tree.column("status", width=90, anchor=tk.CENTER)
        y_scroll = ttk.Scrollbar(table, orient=tk.VERTICAL, command=self.network_device_tree.yview)
        x_scroll = ttk.Scrollbar(table, orient=tk.HORIZONTAL, command=self.network_device_tree.xview)
        self.network_device_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.network_device_tree.grid(row=0, column=0, sticky=tk.NSEW)
        y_scroll.grid(row=0, column=1, sticky=tk.NS)
        x_scroll.grid(row=1, column=0, sticky=tk.EW)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.network_device_tree.bind("<Double-1>", self.on_network_device_double_click)
        self.network_device_tree.bind("<Control-c>", self.copy_selected_network_rows)

    def _build_network_data_tab(self, parent: ttk.Frame) -> None:
        columns = ("device", "sensor", "value", "unit", "updated", "runtime")
        self.network_data_tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended")
        headings = {
            "device": "Device",
            "sensor": "Property / Sensor",
            "value": "Latest Value",
            "unit": "Unit",
            "updated": "Updated",
            "runtime": "Source Member",
        }
        for column in columns:
            self.network_data_tree.heading(column, text=headings[column])
        self.network_data_tree.column("device", width=280, anchor=tk.W)
        self.network_data_tree.column("sensor", width=320, anchor=tk.W)
        self.network_data_tree.column("value", width=180, anchor=tk.W)
        self.network_data_tree.column("unit", width=90, anchor=tk.W)
        self.network_data_tree.column("updated", width=160, anchor=tk.W)
        self.network_data_tree.column("runtime", width=150, anchor=tk.W)
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.network_data_tree.yview)
        self.network_data_tree.configure(yscrollcommand=scroll.set)
        self.network_data_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_network_operations_tab(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        ttk.Label(toolbar, text="Payload").pack(side=tk.LEFT)
        self.network_operation_payload_var = tk.StringVar(value="")
        ttk.Entry(toolbar, textvariable=self.network_operation_payload_var, width=48).pack(side=tk.LEFT, padx=(6, 8))
        ttk.Button(toolbar, text="Publish Selected", command=self.publish_selected_network_operation).pack(side=tk.LEFT)
        self.network_operation_status_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self.network_operation_status_var).pack(side=tk.LEFT, padx=12)

        columns = ("device", "topic", "action", "property", "values", "confidence", "seen")
        self.network_operation_tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        headings = {
            "device": "Device",
            "topic": "Command Topic",
            "action": "Action",
            "property": "Property",
            "values": "Observed Values",
            "confidence": "Confidence",
            "seen": "Last Seen",
        }
        for column in columns:
            self.network_operation_tree.heading(column, text=headings[column])
        self.network_operation_tree.column("device", width=260, anchor=tk.W)
        self.network_operation_tree.column("topic", width=380, anchor=tk.W)
        self.network_operation_tree.column("action", width=100, anchor=tk.W)
        self.network_operation_tree.column("property", width=130, anchor=tk.W)
        self.network_operation_tree.column("values", width=150, anchor=tk.W)
        self.network_operation_tree.column("confidence", width=90, anchor=tk.CENTER)
        self.network_operation_tree.column("seen", width=160, anchor=tk.W)
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.network_operation_tree.yview)
        self.network_operation_tree.configure(yscrollcommand=scroll.set)
        self.network_operation_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.network_operation_tree.bind("<<TreeviewSelect>>", self.on_network_operation_selected)

    def _build_network_profiles_tab(self, parent: ttk.Frame) -> None:
        columns = ("kind", "summary", "details")
        self.network_profile_tree = ttk.Treeview(parent, columns=columns, show="tree headings", selectmode="browse")
        self.network_profile_tree.heading("#0", text="Device / Detail")
        self.network_profile_tree.heading("kind", text="Kind")
        self.network_profile_tree.heading("summary", text="Summary")
        self.network_profile_tree.heading("details", text="Identity / Value")
        self.network_profile_tree.column("#0", width=320, anchor=tk.W)
        self.network_profile_tree.column("kind", width=120, anchor=tk.W)
        self.network_profile_tree.column("summary", width=360, anchor=tk.W)
        self.network_profile_tree.column("details", width=520, anchor=tk.W)
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.network_profile_tree.yview)
        self.network_profile_tree.configure(yscrollcommand=scroll.set)
        self.network_profile_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.network_profile_tree.bind("<Double-1>", self.on_network_profile_double_click)

    def _build_discovery_settings_tab(self, parent: ttk.Frame) -> None:
        summary = ttk.LabelFrame(parent, text="Integrated Discover Runtime", padding=10)
        summary.pack(side=tk.TOP, fill=tk.X)
        self.discovery_runtime_var = tk.StringVar(value="Starting")
        self.discovery_config_var = tk.StringVar(value=str(self.args.discover_config))
        self.discovery_registry_var = tk.StringVar(value=str(_room_store_root(self.args) / "discover_registry.json"))
        ttk.Label(summary, textvariable=self.discovery_runtime_var, justify=tk.LEFT).pack(side=tk.TOP, fill=tk.X)
        ttk.Label(summary, text="Config:").pack(side=tk.TOP, anchor=tk.W, pady=(8, 0))
        ttk.Label(summary, textvariable=self.discovery_config_var).pack(side=tk.TOP, anchor=tk.W)
        ttk.Label(summary, text="Persistent registry:").pack(side=tk.TOP, anchor=tk.W, pady=(8, 0))
        ttk.Label(summary, textvariable=self.discovery_registry_var).pack(side=tk.TOP, anchor=tk.W)

        manager_group = ttk.LabelFrame(parent, text="Discovery Sources", padding=8)
        manager_group.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(10, 0))
        manager_actions = ttk.Frame(manager_group)
        manager_actions.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        ttk.Button(manager_actions, text="Add", command=self.add_discovery_source).pack(side=tk.LEFT)
        ttk.Button(manager_actions, text="Edit", command=self.edit_discovery_source).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(manager_actions, text="Enable / Disable", command=self.toggle_discovery_source).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(manager_actions, text="Delete", command=self.delete_discovery_source).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(manager_actions, text="Restart", command=self.restart_discovery_runtime).pack(side=tk.LEFT, padx=(12, 0))
        self.discovery_source_config_status_var = tk.StringVar(value="")
        ttk.Label(manager_actions, textvariable=self.discovery_source_config_status_var).pack(side=tk.LEFT, padx=12)

        self.discovery_config_tree = ttk.Treeview(
            manager_group,
            columns=("type", "enabled", "settings"),
            show="tree headings",
            height=6,
            selectmode="browse",
        )
        self.discovery_config_tree.heading("#0", text="Source ID")
        self.discovery_config_tree.heading("type", text="Type")
        self.discovery_config_tree.heading("enabled", text="Enabled")
        self.discovery_config_tree.heading("settings", text="Settings")
        self.discovery_config_tree.column("#0", width=180, anchor=tk.W)
        self.discovery_config_tree.column("type", width=100, anchor=tk.W)
        self.discovery_config_tree.column("enabled", width=80, anchor=tk.CENTER)
        self.discovery_config_tree.column("settings", width=720, anchor=tk.W)
        self.discovery_config_tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.discovery_config_tree.bind("<Double-1>", lambda _event: self.edit_discovery_source())
        self.reload_discovery_source_manager()

        config_group = ttk.LabelFrame(parent, text="Advanced TOML", padding=8)
        config_group.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(10, 0))
        config_actions = ttk.Frame(config_group)
        config_actions.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        ttk.Button(config_actions, text="Reload", command=self.reload_discovery_config_editor).pack(side=tk.LEFT)
        ttk.Button(config_actions, text="Save and Restart", command=self.save_and_restart_discovery).pack(side=tk.LEFT, padx=(8, 0))
        self.discovery_config_status_var = tk.StringVar(value="")
        ttk.Label(config_actions, textvariable=self.discovery_config_status_var).pack(side=tk.LEFT, padx=12)
        self.discovery_config_editor = scrolledtext.ScrolledText(config_group, height=10, wrap=tk.NONE)
        self.discovery_config_editor.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.reload_discovery_config_editor()

        source_group = ttk.LabelFrame(parent, text="Runtime Status", padding=8)
        source_group.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(10, 0))
        self.discovery_source_tree = ttk.Treeview(
            source_group,
            columns=("source", "type", "state", "message", "updated"),
            show="headings",
            height=12,
        )
        self.discovery_source_tree.heading("source", text="Source ID")
        self.discovery_source_tree.heading("type", text="Type")
        self.discovery_source_tree.heading("state", text="State")
        self.discovery_source_tree.heading("message", text="Message")
        self.discovery_source_tree.heading("updated", text="Updated")
        self.discovery_source_tree.column("source", width=150, anchor=tk.W)
        self.discovery_source_tree.column("type", width=120, anchor=tk.W)
        self.discovery_source_tree.column("state", width=120, anchor=tk.W)
        self.discovery_source_tree.column("message", width=600, anchor=tk.W)
        self.discovery_source_tree.column("updated", width=160, anchor=tk.W)
        self.discovery_source_tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def _start_discover_runtime(self) -> None:
        registry_path = _room_store_root(self.args) / "discover_registry.json"
        runtime = DiscoverRuntime(
            config_path=self.args.discover_config,
            registry_path=registry_path,
            on_update=self._on_discover_profiles_updated,
        )
        self.discover_runtime = runtime
        runtime.start_background()

    def _start_any2full_background(self) -> None:
        if self._any2full_start_thread is not None and self._any2full_start_thread.is_alive():
            return
        service = Any2FullService(self.args)
        self.any2full_service = service

        def worker() -> None:
            with self._model_startup_lock:
                if self._closing:
                    return
                try:
                    ready = service.start()
                except Exception as exc:
                    service.start_error = str(exc)
                    service.stop()
                    print(f"[any2full] startup failed: {exc}", flush=True)
                    ready = False
            if not ready and self.any2full_service is service:
                self.any2full_service = None

        self._any2full_start_thread = threading.Thread(
            target=worker,
            name="Any2FullStartup",
            daemon=True,
        )
        self._any2full_start_thread.start()

    def _start_device_segmenter_background(self) -> None:
        if self._segmenter_start_thread is not None and self._segmenter_start_thread.is_alive():
            return

        def worker() -> None:
            with self._model_startup_lock:
                if self._closing:
                    return
                segmenter = create_device_segmenter(self.args)
            if not self._closing:
                self.device_segmenter = segmenter

        self._segmenter_start_thread = threading.Thread(
            target=worker,
            name="Sam2Startup",
            daemon=True,
        )
        self._segmenter_start_thread.start()

    def reload_discovery_source_manager(self) -> None:
        try:
            self._discover_configs = load_discover_config(self.args.discover_config)
            message = f"Loaded {len(self._discover_configs)} source(s)"
        except FileNotFoundError:
            self._discover_configs = []
            message = "No source configuration file"
        except Exception as exc:
            self._discover_configs = []
            message = "Load failed: " + str(exc)
        self._render_discovery_source_manager()
        if hasattr(self, "discovery_source_config_status_var"):
            self.discovery_source_config_status_var.set(message)

    def _render_discovery_source_manager(self) -> None:
        if not hasattr(self, "discovery_config_tree"):
            return
        self.discovery_config_tree.delete(*self.discovery_config_tree.get_children())
        for config in self._discover_configs:
            settings = []
            for key, value in sorted(config.settings.items()):
                if key in {"password", "token"} and value:
                    rendered = "********"
                elif isinstance(value, list):
                    rendered = ", ".join(str(item) for item in value)
                else:
                    rendered = str(value or "")
                if rendered:
                    settings.append(f"{key}={rendered}")
            self.discovery_config_tree.insert(
                "",
                tk.END,
                iid=config.source_id,
                text=config.source_id,
                values=(config.source_type, "yes" if config.enabled else "no", "; ".join(settings)),
            )

    def add_discovery_source(self) -> None:
        self._open_discovery_source_dialog(None)

    def edit_discovery_source(self) -> None:
        selected = self.discovery_config_tree.selection() if hasattr(self, "discovery_config_tree") else ()
        if not selected:
            self.discovery_source_config_status_var.set("Select a source first")
            return
        config = next((item for item in self._discover_configs if item.source_id == selected[0]), None)
        if config is not None:
            self._open_discovery_source_dialog(config)

    def toggle_discovery_source(self) -> None:
        selected = self.discovery_config_tree.selection() if hasattr(self, "discovery_config_tree") else ()
        if not selected:
            return
        for index, config in enumerate(self._discover_configs):
            if config.source_id == selected[0]:
                self._discover_configs[index] = SourceConfig(
                    source_id=config.source_id,
                    source_type=config.source_type,
                    enabled=not config.enabled,
                    settings=dict(config.settings),
                )
                break
        self._save_discovery_sources_and_restart()

    def delete_discovery_source(self) -> None:
        selected = self.discovery_config_tree.selection() if hasattr(self, "discovery_config_tree") else ()
        if not selected:
            return
        self._discover_configs = [config for config in self._discover_configs if config.source_id != selected[0]]
        self._save_discovery_sources_and_restart()

    def _open_discovery_source_dialog(self, existing: SourceConfig | None) -> None:
        window = tk.Toplevel(self.root)
        window.title("Edit Discovery Source" if existing is not None else "Add Discovery Source")
        window.geometry("560x620")
        window.transient(self.root)
        window.grab_set()

        source_id_var = tk.StringVar(value=existing.source_id if existing is not None else "")
        source_type_var = tk.StringVar(
            value=existing.source_type if existing is not None else next(iter(DISCOVER_SOURCE_SCHEMAS))
        )
        enabled_var = tk.BooleanVar(value=existing.enabled if existing is not None else True)

        header = ttk.Frame(window, padding=10)
        header.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(header, text="Source ID").grid(row=0, column=0, sticky=tk.W, pady=4)
        source_id_entry = ttk.Entry(header, textvariable=source_id_var)
        source_id_entry.grid(row=0, column=1, sticky=tk.EW, padx=(8, 0), pady=4)
        ttk.Label(header, text="Type").grid(row=1, column=0, sticky=tk.W, pady=4)
        type_combo = ttk.Combobox(
            header,
            textvariable=source_type_var,
            values=tuple(DISCOVER_SOURCE_SCHEMAS),
            state="disabled" if existing is not None else "readonly",
        )
        type_combo.grid(row=1, column=1, sticky=tk.EW, padx=(8, 0), pady=4)
        ttk.Checkbutton(header, text="Enabled", variable=enabled_var).grid(row=2, column=1, sticky=tk.W, padx=(8, 0), pady=4)
        header.columnconfigure(1, weight=1)

        settings_group = ttk.LabelFrame(window, text="Settings", padding=10)
        settings_group.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10)
        widgets: dict[str, tuple[tk.Widget, object]] = {}

        def rebuild_settings() -> None:
            for child in settings_group.winfo_children():
                child.destroy()
            widgets.clear()
            schema = DISCOVER_SOURCE_SCHEMAS[source_type_var.get()]
            current = existing.settings if existing is not None and existing.source_type == source_type_var.get() else {}
            for row, (key, default) in enumerate(schema.defaults.items()):
                ttk.Label(settings_group, text=key.replace("_", " ").title()).grid(
                    row=row, column=0, sticky=tk.NW, padx=(0, 8), pady=4
                )
                value = current.get(key, default)
                if isinstance(default, list):
                    widget = tk.Text(settings_group, height=3, width=42)
                    widget.insert("1.0", "\n".join(str(item) for item in value or []))
                    widget.grid(row=row, column=1, sticky=tk.EW, pady=4)
                    widgets[key] = (widget, list)
                elif isinstance(default, bool):
                    variable = tk.BooleanVar(value=bool(value))
                    widget = ttk.Checkbutton(settings_group, variable=variable)
                    widget.grid(row=row, column=1, sticky=tk.W, pady=4)
                    widgets[key] = (widget, variable)
                elif isinstance(default, int):
                    variable = tk.IntVar(value=int(value))
                    widget = ttk.Spinbox(
                        settings_group,
                        from_=0 if int(default) == 0 else 1,
                        to=65535,
                        textvariable=variable,
                    )
                    widget.grid(row=row, column=1, sticky=tk.EW, pady=4)
                    widgets[key] = (widget, variable)
                else:
                    variable = tk.StringVar(value="" if value is None else str(value))
                    widget = ttk.Entry(
                        settings_group,
                        textvariable=variable,
                        show="*" if key in {"password", "token"} else "",
                    )
                    widget.grid(row=row, column=1, sticky=tk.EW, pady=4)
                    widgets[key] = (widget, variable)
            settings_group.columnconfigure(1, weight=1)

        type_combo.bind("<<ComboboxSelected>>", lambda _event: rebuild_settings())
        rebuild_settings()
        status_var = tk.StringVar(value="")
        ttk.Label(window, textvariable=status_var).pack(side=tk.TOP, fill=tk.X, padx=10, pady=(6, 0))

        def save() -> None:
            source_id = source_id_var.get().strip()
            source_type = source_type_var.get().strip()
            if not source_id:
                status_var.set("Source ID is required")
                return
            if existing is None and any(config.source_id == source_id for config in self._discover_configs):
                status_var.set("Source ID already exists")
                return
            settings: dict[str, object] = {}
            for key, (widget, kind) in widgets.items():
                if kind is list:
                    text = widget.get("1.0", tk.END).strip()  # type: ignore[attr-defined]
                    settings[key] = [line.strip() for line in text.splitlines() if line.strip()]
                else:
                    settings[key] = kind.get()  # type: ignore[union-attr]
            schema = DISCOVER_SOURCE_SCHEMAS[source_type]
            try:
                validated = schema.validate(settings)
                for required in schema.required:
                    if validated.get(required) in (None, ""):
                        raise ValueError(f"{required} is required")
            except Exception as exc:
                status_var.set(str(exc))
                return
            new_config = SourceConfig(source_id, source_type, enabled_var.get(), validated)
            if existing is None:
                self._discover_configs.append(new_config)
            else:
                self._discover_configs = [
                    new_config if item.source_id == existing.source_id else item
                    for item in self._discover_configs
                ]
            window.destroy()
            self._save_discovery_sources_and_restart()

        actions = ttk.Frame(window, padding=10)
        actions.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(actions, text="Cancel", command=window.destroy).pack(side=tk.RIGHT)
        ttk.Button(actions, text="Save", command=save).pack(side=tk.RIGHT, padx=(0, 8))

    def _save_discovery_sources_and_restart(self) -> None:
        try:
            save_discover_config(self._discover_configs, self.args.discover_config)
            self._render_discovery_source_manager()
            self.reload_discovery_config_editor()
        except Exception as exc:
            self.discovery_source_config_status_var.set("Save failed: " + str(exc))
            return
        self.restart_discovery_runtime()

    def restart_discovery_runtime(self) -> None:
        self.discovery_source_config_status_var.set("Restarting...")

        def restart() -> None:
            previous = self.discover_runtime
            if previous is not None:
                previous.stop()
            self._start_discover_runtime()
            self._network_profiles_pending.set()
            self._discovery_restart_completed.set()

        threading.Thread(target=restart, name="DiscoverRestart", daemon=True).start()

    def reload_discovery_config_editor(self) -> None:
        if not hasattr(self, "discovery_config_editor"):
            return
        try:
            content = self.args.discover_config.read_text(encoding="utf-8")
            message = "Configuration loaded"
        except FileNotFoundError:
            content = ""
            message = "Configuration file does not exist yet"
        except Exception as exc:
            content = ""
            message = "Load failed: " + str(exc)
        self.discovery_config_editor.delete("1.0", tk.END)
        self.discovery_config_editor.insert(tk.END, content)
        self.discovery_config_status_var.set(message)

    def save_and_restart_discovery(self) -> None:
        if not hasattr(self, "discovery_config_editor"):
            return
        content = self.discovery_config_editor.get("1.0", tk.END).rstrip() + "\n"
        self.discovery_config_status_var.set("Validating...")
        try:
            import tomllib

            tomllib.loads(content)
        except ImportError:
            import tomli

            try:
                tomli.loads(content)
            except Exception as exc:
                self.discovery_config_status_var.set("Invalid TOML: " + str(exc))
                return
        except Exception as exc:
            self.discovery_config_status_var.set("Invalid TOML: " + str(exc))
            return

        try:
            save_discover_config_text(content, self.args.discover_config)
        except Exception as exc:
            self.discovery_config_status_var.set("Save failed: " + str(exc))
            return

        self.reload_discovery_source_manager()
        self.discovery_config_status_var.set("Saved")
        self.restart_discovery_runtime()

    def _on_discover_profiles_updated(self, _profiles: list[dict]) -> None:
        self._network_profiles_pending.set()

    def refresh_network_devices(self, *, force: bool = False) -> None:
        if not hasattr(self, "network_device_tree"):
            return
        if self._discovery_restart_completed.is_set():
            self._discovery_restart_completed.clear()
            self.discovery_source_config_status_var.set("Saved and restarted")
        if self._network_refresh_after_id is not None:
            try:
                self.root.after_cancel(self._network_refresh_after_id)
            except Exception:
                pass
            self._network_refresh_after_id = None

        runtime = self.discover_runtime
        status = runtime.status() if runtime is not None else {
            "running": False,
            "device_count": 0,
            "sources": {},
            "last_error": "Discovery disabled",
            "profile_revision": 0,
        }
        revision = int(status.get("profile_revision") or 0)
        should_render = (
            force
            or self._network_profiles_pending.is_set()
            or revision != self._last_network_profile_revision
        )
        if should_render:
            profiles = runtime.profiles() if runtime is not None else []
            self._network_profiles_snapshot = profiles
            self.network_device_tree.delete(*self.network_device_tree.get_children())
            self._network_tree_item_context.clear()
            for profile in profiles:
                connections = profile.get("connections") or {}
                addresses = [*(connections.get("ip") or []), *(connections.get("mac") or [])]
                identifiers = profile.get("identifiers") or {}
                entities = identifiers.get("mqtt_entity_prefix") or identifiers.get("mqtt_topic_prefix") or []
                channels = identifiers.get("mqtt_channel") or []
                identity = profile.get("identity") or {}
                classification = profile.get("classification") or {}
                classification_confidence = float(classification.get("confidence") or 0.0)
                item = self.network_device_tree.insert(
                    "",
                    tk.END,
                    text=str(profile.get("display_name") or "Unknown network device"),
                    values=(
                        ", ".join(str(value) for value in entities),
                        ", ".join(str(value) for value in channels) or "-",
                        int(identity.get("observed_topic_count") or len(identifiers.get("mqtt_topic_prefix") or [])),
                        f"{profile.get('device_type') or ''} ({classification_confidence:.0%})",
                        len(profile.get("data") or {}),
                        len(profile.get("operations") or []),
                        ", ".join(str(value) for value in addresses),
                        int(profile.get("evidence_count") or 0),
                        "online" if profile.get("online", False) else "stored",
                    ),
                )
                self._network_tree_item_context[item] = profile
            self._refresh_visible_network_secondary_view()
            self._last_network_profile_revision = revision
            self._network_profiles_pending.clear()

        running = "running" if status.get("running") else "stopped"
        last_error = str(status.get("last_error") or "")
        status_text = (
            f"{int(status.get('device_count') or 0)} network device(s) | runtime {running} | "
            f"{int(status.get('events_ingested') or 0)} events / "
            f"{int(status.get('profile_refresh_count') or 0)} batches"
        )
        if last_error:
            status_text += " | " + last_error
        self.network_device_status_var.set(status_text)
        self.discovery_runtime_var.set(status_text)

        if hasattr(self, "discovery_source_tree"):
            self.discovery_source_tree.delete(*self.discovery_source_tree.get_children())
            for source_id, source in sorted((status.get("sources") or {}).items()):
                updated = float(source.get("updated_at") or 0.0)
                updated_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(updated)) if updated else ""
                self.discovery_source_tree.insert(
                    "",
                    tk.END,
                    values=(
                        source_id,
                        str(source.get("source_type") or source_id),
                        str(source.get("state") or ""),
                        str(source.get("message") or ""),
                        updated_text,
                    ),
                )
        self._network_refresh_after_id = self.root.after(2000, self.refresh_network_devices)

    def _on_main_tab_changed(self, _event: tk.Event) -> None:
        self._refresh_visible_network_secondary_view()

    def _refresh_visible_network_secondary_view(self) -> None:
        if not hasattr(self, "main_notebook"):
            return
        selected = self.main_notebook.select()
        if selected == str(self._network_data_tab):
            self.network_data_tree.delete(*self.network_data_tree.get_children())
            for profile in self._network_profiles_snapshot:
                self._append_network_data_rows(profile)
        elif selected == str(self._network_operations_tab):
            self.network_operation_tree.delete(*self.network_operation_tree.get_children())
            self._network_operation_context.clear()
            for profile in self._network_profiles_snapshot:
                self._append_network_operation_rows(profile)
        elif selected == str(self._network_profiles_tab):
            self.network_profile_tree.delete(*self.network_profile_tree.get_children())
            self._network_profile_item_context.clear()
            for profile in self._network_profiles_snapshot:
                self._append_network_profile_rows(profile)

    def copy_selected_network_rows(self, _event: tk.Event | None = None) -> str:
        selected = self.network_device_tree.selection()
        if not selected:
            return "break"
        headers = ["Device"] + [
            self.network_device_tree.heading(column, "text")
            for column in self.network_device_tree["columns"]
        ]
        rows = ["\t".join(str(value) for value in headers)]
        for item_id in selected:
            item = self.network_device_tree.item(item_id)
            rows.append("\t".join([str(item.get("text") or ""), *(str(value) for value in item.get("values") or [])]))
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(rows))
        self.network_device_status_var.set(f"Copied {len(selected)} row(s)")
        return "break"

    def copy_selected_network_json(self) -> None:
        profiles = [
            self._network_tree_item_context[item_id]
            for item_id in self.network_device_tree.selection()
            if item_id in self._network_tree_item_context
        ]
        if not profiles:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(json.dumps(profiles, indent=2, ensure_ascii=False))
        self.network_device_status_var.set(f"Copied {len(profiles)} profile(s) as JSON")

    def _append_network_data_rows(self, profile: dict) -> None:
        if not hasattr(self, "network_data_tree"):
            return
        name = str(profile.get("display_name") or "")
        for sensor, reading in sorted((profile.get("data") or {}).items()):
            timestamp = float(reading.get("timestamp") or 0.0)
            updated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp)) if timestamp else ""
            value = reading.get("value")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            self.network_data_tree.insert(
                "",
                tk.END,
                values=(
                    name,
                    sensor,
                    value,
                    str(reading.get("unit") or ""),
                    updated,
                    str(reading.get("runtime_device_id") or ""),
                ),
            )

    def _append_network_operation_rows(self, profile: dict) -> None:
        if not hasattr(self, "network_operation_tree"):
            return
        name = str(profile.get("display_name") or "")
        for operation in profile.get("operations") or []:
            timestamp = float(operation.get("last_seen") or 0.0)
            seen = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp)) if timestamp else ""
            item = self.network_operation_tree.insert(
                "",
                tk.END,
                values=(
                    name,
                    str(operation.get("topic") or ""),
                    str(operation.get("action") or ""),
                    str(operation.get("sensor_key") or ""),
                    ", ".join(str(value) for value in operation.get("accepted_values") or []),
                    f"{float(operation.get('confidence') or 0.0):.2f}",
                    seen,
                ),
            )
            self._network_operation_context[item] = operation

    def _append_network_profile_rows(self, profile: dict) -> None:
        if not hasattr(self, "network_profile_tree"):
            return
        identity = profile.get("identity") or {}
        classification = profile.get("classification") or {}
        parent = self.network_profile_tree.insert(
            "",
            tk.END,
            text=str(profile.get("display_name") or "Unknown device"),
            values=(
                (
                    f"{profile.get('device_type') or ''} "
                    f"({float(classification.get('confidence') or 0.0):.0%})"
                ),
                str(profile.get("summary") or ""),
                f"{identity.get('channel_count', 0)} channels, {identity.get('observed_topic_count', 0)} topics",
            ),
            open=False,
        )
        self._network_profile_item_context[parent] = profile
        identifiers = profile.get("identifiers") or {}
        self.network_profile_tree.insert(
            parent,
            tk.END,
            text="MQTT entity",
            values=(
                "Identity",
                ", ".join(str(value) for value in identifiers.get("mqtt_entity_prefix") or []),
                ", ".join(str(value) for value in identifiers.get("mqtt_channel") or []) or "no separate channels",
            ),
        )
        for reason in identity.get("reasons") or []:
            self.network_profile_tree.insert(parent, tk.END, text="Dedup decision", values=("Evidence", "", str(reason)))
        separation_policy = str(identity.get("separation_policy") or "")
        if separation_policy:
            self.network_profile_tree.insert(
                parent,
                tk.END,
                text="Why separate",
                values=("Policy", "", separation_policy),
            )
        for sensor, reading in sorted((profile.get("data") or {}).items()):
            self.network_profile_tree.insert(
                parent,
                tk.END,
                text=sensor,
                values=("Data", f"{reading.get('value')} {reading.get('unit') or ''}".strip(), ""),
            )
        for operation in profile.get("operations") or []:
            child = self.network_profile_tree.insert(
                parent,
                tk.END,
                text=str(operation.get("action") or "command"),
                values=(
                    "Operation",
                    str(operation.get("sensor_key") or ""),
                    str(operation.get("topic") or ""),
                ),
            )
            self._network_profile_item_context[child] = {"profile": profile, "operation": operation}

    def on_network_operation_selected(self, _event: tk.Event) -> None:
        selected = self.network_operation_tree.selection()
        if not selected:
            return
        operation = self._network_operation_context.get(selected[0])
        if operation is None:
            return
        values = operation.get("accepted_values") or []
        if values:
            self.network_operation_payload_var.set(str(values[0]))
        elif not self.network_operation_payload_var.get():
            self.network_operation_payload_var.set("{}")

    def publish_selected_network_operation(self) -> None:
        selected = self.network_operation_tree.selection()
        if not selected:
            self.network_operation_status_var.set("Select an operation first")
            return
        operation = self._network_operation_context.get(selected[0])
        if operation is None:
            return
        raw = self.network_operation_payload_var.get().strip()
        try:
            payload = json.loads(raw) if raw and raw[0] in "[{\"" else raw
        except json.JSONDecodeError as exc:
            self.network_operation_status_var.set("Invalid JSON: " + str(exc))
            return
        runtime = self.discover_runtime
        ok = runtime is not None and runtime.publish_mqtt(str(operation.get("topic") or ""), payload)
        self.network_operation_status_var.set("Published" if ok else "Publish failed: MQTT source unavailable")

    def on_network_profile_double_click(self, _event: tk.Event) -> None:
        selected = self.network_profile_tree.selection()
        if not selected:
            return
        context = self._network_profile_item_context.get(selected[0])
        if isinstance(context, dict) and "operation" in context:
            operation = context["operation"]
            self.network_operation_payload_var.set(
                str((operation.get("accepted_values") or ["{}"])[0])
            )
            return
        if isinstance(context, dict):
            self._open_network_profile_detail(context)

    def on_network_device_double_click(self, _event: tk.Event) -> None:
        if not hasattr(self, "network_device_tree"):
            return
        selected = self.network_device_tree.selection()
        if not selected:
            return
        profile = self._network_tree_item_context.get(selected[0])
        if profile is None:
            return
        self._open_network_profile_detail(profile)

    def _open_network_profile_detail(self, profile: dict) -> None:
        window = tk.Toplevel(self.root)
        window.title(str(profile.get("display_name") or "Network Device"))
        window.geometry("920x720")
        tabs = ttk.Notebook(window)
        tabs.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=8)

        summary_tab = ttk.Frame(tabs, padding=10)
        data_tab = ttk.Frame(tabs, padding=10)
        operations_tab = ttk.Frame(tabs, padding=10)
        raw_tab = ttk.Frame(tabs, padding=10)
        tabs.add(summary_tab, text="Summary")
        tabs.add(data_tab, text="Data")
        tabs.add(operations_tab, text="Operations")
        tabs.add(raw_tab, text="Raw Profile")

        summary = scrolledtext.ScrolledText(summary_tab, wrap=tk.WORD)
        summary.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        identity = profile.get("identity") or {}
        identifiers = profile.get("identifiers") or {}
        lines = [
            str(profile.get("display_name") or ""),
            "",
            str(profile.get("summary") or ""),
            "",
            (
                "Classification: "
                f"{profile.get('device_type') or '-'} "
                f"({float((profile.get('classification') or {}).get('confidence') or 0.0):.0%}, "
                f"{(profile.get('classification') or {}).get('method') or 'unknown'})"
            ),
            "",
            "Physical entity:",
            *[f"  {value}" for value in identifiers.get("mqtt_entity_prefix") or []],
            "MQTT client ids:",
            *[f"  {value}" for value in identifiers.get("mqtt_client_id") or []],
            "Channels: " + (", ".join(identifiers.get("mqtt_channel") or []) or "-"),
            "Observed topic prefixes:",
            *[f"  {value}" for value in identifiers.get("mqtt_topic_prefix") or []],
            "",
            "Deduplication decisions:",
            *[f"  {value}" for value in identity.get("reasons") or []],
            "",
            "Separation policy:",
            f"  {identity.get('separation_policy') or '-'}",
        ]
        summary.insert(tk.END, "\n".join(lines))
        summary.configure(state=tk.DISABLED)

        data_tree = ttk.Treeview(data_tab, columns=("property", "value", "unit", "updated"), show="headings")
        for column, title in (
            ("property", "Property"),
            ("value", "Value"),
            ("unit", "Unit"),
            ("updated", "Updated"),
        ):
            data_tree.heading(column, text=title)
        data_tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        for sensor, reading in sorted((profile.get("data") or {}).items()):
            timestamp = float(reading.get("timestamp") or 0.0)
            data_tree.insert(
                "",
                tk.END,
                values=(
                    sensor,
                    reading.get("value"),
                    reading.get("unit") or "",
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp)) if timestamp else "",
                ),
            )

        operation_text = scrolledtext.ScrolledText(operations_tab, wrap=tk.WORD)
        operation_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        operation_text.insert(
            tk.END,
            json.dumps(profile.get("operations") or [], indent=2, ensure_ascii=False),
        )
        operation_text.configure(state=tk.DISABLED)

        raw = scrolledtext.ScrolledText(raw_tab, wrap=tk.NONE)
        raw.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        raw.insert(tk.END, json.dumps(profile, indent=2, ensure_ascii=False))
        raw.configure(state=tk.DISABLED)

    def _vlm_form_config(self) -> dict:
        return {
            "base_url": self.vlm_base_url_var.get().strip(),
            "token": self.vlm_token_var.get().strip(),
            "model": self.vlm_model_var.get().strip(),
        }

    def _vlm_form_signature(self) -> str:
        config = self._vlm_form_config()
        return json.dumps(
            {
                "base_url": config["base_url"],
                "token": config["token"],
                "model": config["model"],
            },
            sort_keys=True,
        )

    def on_vlm_connect(self) -> None:
        config = self._vlm_form_config()
        self.vlm_status_var.set("Connecting...")

        def worker() -> None:
            try:
                models = _vlm_list_models(config, timeout=float(getattr(self.args, "vlm_timeout_seconds", 120.0)))
                self.root.after(0, lambda: self._on_vlm_models_loaded(models))
            except Exception as exc:
                reason = str(exc)
                self.root.after(0, lambda reason=reason: self.vlm_status_var.set("Connect failed: " + reason))

        threading.Thread(target=worker, daemon=True).start()

    def _on_vlm_models_loaded(self, models: list[str]) -> None:
        self.vlm_model_combo.configure(values=models)
        if models and not self.vlm_model_var.get().strip():
            self.vlm_model_var.set(models[0])
        self._vlm_last_test_ok = False
        self.vlm_status_var.set(f"Loaded {len(models)} model(s)")

    def on_vlm_test(self) -> None:
        config = self._vlm_form_config()
        if not config["base_url"] or not config["model"]:
            self.vlm_status_var.set("Base URL and model are required")
            return
        signature = self._vlm_form_signature()
        self.vlm_status_var.set("Testing...")

        def worker() -> None:
            try:
                models, response = _vlm_test_connection(config, timeout=float(getattr(self.args, "vlm_timeout_seconds", 120.0)))
                self.root.after(0, lambda: self._on_vlm_test_ok(models, response, signature))
            except Exception as exc:
                reason = str(exc)
                self.root.after(0, lambda reason=reason: self._on_vlm_test_failed(reason))

        threading.Thread(target=worker, daemon=True).start()

    def _on_vlm_test_ok(self, models: list[str], response: str, signature: str) -> None:
        if models:
            self.vlm_model_combo.configure(values=models)
        self._vlm_last_test_ok = True
        self._vlm_last_test_signature = signature
        self.vlm_status_var.set("Test passed: " + (response[:80] if response else "empty response"))

    def _on_vlm_test_failed(self, reason: str) -> None:
        self._vlm_last_test_ok = False
        self.vlm_status_var.set("Test failed: " + reason)

    def on_vlm_save(self) -> None:
        config = self._vlm_form_config()
        if not config["base_url"] or not config["model"]:
            self.vlm_status_var.set("Base URL and model are required")
            return
        if not self._vlm_last_test_ok or self._vlm_last_test_signature != self._vlm_form_signature():
            self.vlm_status_var.set("Run Test successfully before saving")
            return
        config["tested_at_ms"] = _now_ms()
        _save_vlm_config(self.args, config)
        self.vlm_status_var.set("Saved")

    def scan_completed_room_objects(self) -> list[dict]:
        root = _room_store_root(self.args)
        items: list[dict] = []
        if not root.exists():
            return items
        for room_dir in sorted([path for path in root.iterdir() if path.is_dir()], key=lambda p: p.name):
            for device_dir in sorted([path for path in room_dir.iterdir() if path.is_dir()], key=lambda p: p.name):
                points_path = device_dir / "points.json"
                if not points_path.exists():
                    continue
                try:
                    store = json.loads(points_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    print(f"[viewer] failed to read {points_path}: {exc}", flush=True)
                    continue
                if not isinstance(store, dict):
                    continue
                _mark_stale_vlm_records(self.args, device_dir, store)
                for record in _object_records(store):
                    if str(record.get("status") or "") != "completed":
                        continue
                    object_id = str(record.get("object_id") or "")
                    images = _object_images(store, object_id)
                    points = _object_points(store, object_id)
                    items.append(
                        {
                            "room_id": str(store.get("room_id") or room_dir.name),
                            "room_name": str(store.get("room_name") or room_dir.name),
                            "device_id": str(store.get("device_id") or device_dir.name),
                            "device_name": str(store.get("device_name") or device_dir.name),
                            "device_model": str(store.get("device_model") or ""),
                            "device_dir": device_dir,
                            "store": store,
                            "object": record,
                            "object_id": object_id,
                            "image_count": len(images),
                            "point_count": len(points),
                        }
                    )
        items.sort(key=lambda item: int(item["object"].get("completed_at_ms") or 0), reverse=True)
        return items

    def refresh_device_tree(self) -> None:
        if not hasattr(self, "device_tree"):
            return
        self.device_tree.delete(*self.device_tree.get_children())
        self._device_tree_item_context.clear()
        self._device_tree_photo_refs = []
        room_items: dict[str, str] = {}
        device_items: dict[tuple[str, str], str] = {}
        objects = self.scan_completed_room_objects()
        for item in objects:
            room_key = item["room_id"]
            if room_key not in room_items:
                room_items[room_key] = self.device_tree.insert(
                    "",
                    tk.END,
                    text=item["room_name"],
                    values=("", "", "", ""),
                    open=True,
                )
            device_key = (item["room_id"], item["device_id"])
            if device_key not in device_items:
                device_label = item["device_name"]
                if item["device_model"]:
                    device_label += f" ({item['device_model']})"
                device_items[device_key] = self.device_tree.insert(
                    room_items[room_key],
                    tk.END,
                    text=device_label,
                    values=("", "", "", ""),
                    open=True,
                )
            record = item["object"]
            completed = _format_local_timestamp_s(int(record.get("completed_at_ms") or _now_ms()))
            vlm_status = str(record.get("vlm_status") or "not run")
            binding = record.get("network_binding")
            if isinstance(binding, dict):
                network_status = str(binding.get("display_name") or binding.get("canonical_device_id") or "paired")
            else:
                network_status = str(record.get("pairing_status") or "not started")
            photo = self._make_tree_thumbnail(item)
            insert_kwargs = {
                "text": item["object_id"],
                "values": (
                    str(record.get("name") or item["object_id"]),
                    completed,
                    str(item["image_count"]),
                    vlm_status,
                    network_status,
                ),
            }
            if photo is not None:
                insert_kwargs["image"] = photo
            object_item = self.device_tree.insert(device_items[device_key], tk.END, **insert_kwargs)
            self._device_tree_item_context[object_item] = item
        self.device_tree_status_var.set(f"{len(objects)} completed device(s)")

    def _make_tree_thumbnail(self, item: dict) -> ImageTk.PhotoImage | None:
        try:
            body = _build_object_thumbnail(self, Path(item["device_dir"]), item["store"], str(item["object_id"]))
            image = Image.open(io.BytesIO(body)).convert("RGB").resize((64, 48), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            self._device_tree_photo_refs.append(photo)
            return photo
        except Exception as exc:
            print(f"[viewer] thumbnail failed for {item.get('object_id')}: {exc}", flush=True)
            return None

    def on_device_tree_double_click(self, _event: tk.Event) -> None:
        item_id = self.device_tree.focus()
        context = self._device_tree_item_context.get(item_id)
        if context is None:
            return
        self.open_object_detail_window(context)

    def retry_selected_vlm(self) -> None:
        item_id = self.device_tree.focus()
        context = self._device_tree_item_context.get(item_id)
        if context is None:
            self.device_tree_status_var.set("Select a completed device object first")
            return
        self.retry_vlm_for_context(context)

    def retry_vlm_for_context(self, context: dict, status_var: tk.StringVar | None = None) -> bool:
        config = _load_vlm_config(self.args)
        if not str(config.get("base_url") or "").strip() or not str(config.get("model") or "").strip():
            message = "VLM config incomplete"
            self.device_tree_status_var.set(message)
            if status_var is not None:
                status_var.set(message)
            return False

        cursor = {
            "room_id": context["room_id"],
            "room_name": context["room_name"],
            "device_id": context["device_id"],
            "device_name": context["device_name"],
            "device_model": context.get("device_model", ""),
            "object_id": context["object_id"],
            "object_session_id": context["object_id"],
        }
        _schedule_vlm_object_analysis(self, cursor, str(context["object_id"]))
        message = "VLM retry started"
        self.device_tree_status_var.set(message)
        if status_var is not None:
            status_var.set(message)
        self.root.after(500, self.refresh_device_tree)
        return True

    def open_object_detail_window(self, context: dict) -> None:
        device_dir = Path(context["device_dir"])
        try:
            store = json.loads((device_dir / "points.json").read_text(encoding="utf-8"))
        except Exception:
            store = context["store"]
        object_id = str(context["object_id"])
        record = _find_object_record(store, object_id) or context["object"]

        window = tk.Toplevel(self.root)
        window.title(str(record.get("name") or object_id))
        window.geometry("1180x780")
        window.photo_refs = []  # type: ignore[attr-defined]

        header = ttk.Frame(window, padding=8)
        header.pack(side=tk.TOP, fill=tk.X)
        title = f"{record.get('name') or object_id} | {context['room_name']} / {context['device_name']}"
        ttk.Label(header, text=title, font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        detail_status_var = tk.StringVar(value="")
        ttk.Button(header, text="Retry VLM", command=lambda: self.retry_vlm_for_context(context, detail_status_var)).pack(side=tk.RIGHT)
        ttk.Button(header, text="Refresh", command=lambda: refresh_detail()).pack(side=tk.RIGHT, padx=(0, 8))

        main = ttk.PanedWindow(window, orient=tk.HORIZONTAL)
        main.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        image_outer = ttk.Frame(main)
        text_outer = ttk.Frame(main)
        main.add(image_outer, weight=3)
        main.add(text_outer, weight=2)

        canvas = tk.Canvas(image_outer, background="#f5f5f5", highlightthickness=0)
        scrollbar = ttk.Scrollbar(image_outer, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        detail_images = _object_detail_images(self, device_dir, store, object_id)
        if not detail_images:
            ttk.Label(inner, text="No mask images available").pack(side=tk.TOP, padx=12, pady=12)
        for image_record, image in detail_images:
            display = image.copy()
            if display.width > 420:
                scale = 420 / float(display.width)
                display = display.resize((420, max(1, int(display.height * scale))), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(display)
            window.photo_refs.append(photo)  # type: ignore[attr-defined]
            ttk.Label(inner, text=str(image_record.get("image_id") or ""), font=("Segoe UI", 10, "bold")).pack(side=tk.TOP, anchor=tk.W, padx=8, pady=(10, 2))
            ttk.Label(inner, image=photo).pack(side=tk.TOP, anchor=tk.W, padx=8)

        meta = ttk.LabelFrame(text_outer, text="Saved Device", padding=8)
        meta.pack(side=tk.TOP, fill=tk.X)
        meta_var = tk.StringVar(value="")
        ttk.Label(meta, textvariable=meta_var, justify=tk.LEFT).pack(side=tk.TOP, fill=tk.X)
        ttk.Label(meta, textvariable=detail_status_var, foreground="#5a5a5a").pack(side=tk.TOP, fill=tk.X, pady=(6, 0))

        pairing_group = ttk.LabelFrame(text_outer, text="Network Pairing", padding=8)
        pairing_group.pack(side=tk.TOP, fill=tk.BOTH, pady=(8, 0))
        pairing_toolbar = ttk.Frame(pairing_group)
        pairing_toolbar.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        pairing_status_var = tk.StringVar(value="")
        ttk.Label(pairing_toolbar, textvariable=pairing_status_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        pairing_candidates: dict[str, dict] = {}
        pairing_tree = ttk.Treeview(
            pairing_group,
            columns=("score", "coverage", "type", "address"),
            show="tree headings",
            height=6,
        )
        pairing_tree.heading("#0", text="Candidate")
        pairing_tree.heading("score", text="Score")
        pairing_tree.heading("coverage", text="Evidence")
        pairing_tree.heading("type", text="Type")
        pairing_tree.heading("address", text="IP / MAC")
        pairing_tree.column("#0", width=260, anchor=tk.W)
        pairing_tree.column("score", width=70, anchor=tk.CENTER)
        pairing_tree.column("coverage", width=80, anchor=tk.CENTER)
        pairing_tree.column("type", width=150, anchor=tk.W)
        pairing_tree.column("address", width=190, anchor=tk.W)
        pairing_tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        def start_pairing_refresh() -> None:
            cursor = {
                "room_id": context["room_id"],
                "room_name": context["room_name"],
                "device_id": context["device_id"],
                "device_name": context["device_name"],
                "device_model": context["device_model"],
                "object_id": object_id,
                "object_session_id": object_id,
            }
            pairing_status_var.set("Matching network devices...")
            _schedule_object_pairing_analysis(self, cursor, object_id)

        def bind_selected_candidate() -> None:
            selected = pairing_tree.selection()
            if not selected:
                pairing_status_var.set("Select a network candidate first")
                return
            candidate = pairing_candidates.get(selected[0])
            if candidate is None:
                return
            canonical_id = str(candidate.get("canonical_device_id") or "")
            try:
                current_store = json.loads((device_dir / "points.json").read_text(encoding="utf-8"))
                current_record = _find_object_record(current_store, object_id)
                if current_record is None:
                    raise ValueError("object not found")
                conflict = _find_binding_conflict(
                    self.args,
                    context["room_id"],
                    canonical_id,
                    device_dir,
                    object_id,
                )
                if conflict is not None:
                    pairing_status_var.set(
                        "Already bound to " + str(conflict.get("object_name") or conflict.get("object_id") or "")
                    )
                    return
                profile = candidate.get("profile") or {}
                current_record["network_binding"] = {
                    "canonical_device_id": canonical_id,
                    "display_name": str(candidate.get("display_name") or canonical_id),
                    "method": "semantic_match_manual_confirmation",
                    "score": int(candidate.get("score") or 0),
                    "evidence_coverage_percent": int(candidate.get("evidence_coverage_percent") or 0),
                    "bound_at_ms": _now_ms(),
                    "profile_snapshot": profile,
                }
                current_record["updated_at_ms"] = _now_ms()
                _save_point_store(device_dir, current_store)
                pairing_status_var.set("Bound to " + str(candidate.get("display_name") or canonical_id))
                refresh_detail()
            except Exception as exc:
                pairing_status_var.set("Bind failed: " + str(exc))

        def unbind_candidate() -> None:
            try:
                current_store = json.loads((device_dir / "points.json").read_text(encoding="utf-8"))
                current_record = _find_object_record(current_store, object_id)
                if current_record is None:
                    raise ValueError("object not found")
                current_record.pop("network_binding", None)
                current_record["updated_at_ms"] = _now_ms()
                _save_point_store(device_dir, current_store)
                pairing_status_var.set("Network binding removed")
                refresh_detail()
            except Exception as exc:
                pairing_status_var.set("Unbind failed: " + str(exc))

        ttk.Button(pairing_toolbar, text="Refresh Match", command=start_pairing_refresh).pack(side=tk.RIGHT)
        ttk.Button(pairing_toolbar, text="Bind Selected", command=bind_selected_candidate).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(pairing_toolbar, text="Unbind", command=unbind_candidate).pack(side=tk.RIGHT, padx=(0, 6))

        desc_group = ttk.LabelFrame(text_outer, text="VLM Description", padding=8)
        desc_group.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(8, 0))
        desc = scrolledtext.ScrolledText(desc_group, wrap=tk.WORD)
        desc.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        def refresh_detail() -> None:
            try:
                current_store = json.loads((device_dir / "points.json").read_text(encoding="utf-8"))
            except Exception:
                current_store = store
            _mark_stale_vlm_records(self.args, device_dir, current_store)
            current_record = _find_object_record(current_store, object_id) or record
            lines = [
                f"Object: {object_id}",
                f"Completed: {_format_local_timestamp_s(int(current_record.get('completed_at_ms') or _now_ms()))}",
                f"Images: {len(_object_images(current_store, object_id))}",
                f"Points: {len(_object_points(current_store, object_id))}",
                f"VLM: {current_record.get('vlm_status') or 'not run'}",
            ]
            if current_record.get("vlm_model"):
                lines.append(f"Model: {current_record.get('vlm_model')}")
            if current_record.get("vlm_started_at_ms"):
                lines.append(f"Started: {_format_local_timestamp_s(int(current_record.get('vlm_started_at_ms')))}")
            if current_record.get("vlm_completed_at_ms"):
                lines.append(f"Finished: {_format_local_timestamp_s(int(current_record.get('vlm_completed_at_ms')))}")
            if current_record.get("vlm_error"):
                lines.append(f"Error: {current_record.get('vlm_error')}")
            binding = current_record.get("network_binding")
            if isinstance(binding, dict):
                lines.append(f"Network: {binding.get('display_name') or binding.get('canonical_device_id')}")
            meta_var.set("\n".join(lines))

            pairing_tree.delete(*pairing_tree.get_children())
            pairing_candidates.clear()
            for candidate in current_record.get("pairing_candidates") or []:
                profile = candidate.get("profile") or {}
                connections = profile.get("connections") or {}
                addresses = [*(connections.get("ip") or []), *(connections.get("mac") or [])]
                item_id = pairing_tree.insert(
                    "",
                    tk.END,
                    text=str(candidate.get("display_name") or candidate.get("canonical_device_id") or ""),
                    values=(
                        f"{int(candidate.get('score') or 0)}%",
                        f"{int(candidate.get('evidence_coverage_percent') or 0)}%",
                        str(profile.get("device_type") or ""),
                        ", ".join(str(value) for value in addresses),
                    ),
                )
                pairing_candidates[item_id] = candidate
            pairing_state = str(current_record.get("pairing_status") or "not started")
            if isinstance(binding, dict):
                pairing_state += " | bound: " + str(binding.get("display_name") or "")
            elif current_record.get("pairing_error"):
                pairing_state += " | " + str(current_record.get("pairing_error"))
            pairing_status_var.set(pairing_state)

            description = str(current_record.get("vlm_description") or "")
            if not description:
                description = "No VLM description yet."
            desc.configure(state=tk.NORMAL)
            desc.delete("1.0", tk.END)
            desc.insert(tk.END, description)
            desc.configure(state=tk.DISABLED)
            self.refresh_device_tree()

        def schedule_detail_refresh() -> None:
            if not window.winfo_exists():
                return
            refresh_detail()
            window.after(2000, schedule_detail_refresh)

        schedule_detail_refresh()

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
        coords = prompt.get("user_point_coords") or prompt.get("point_coords") or []
        labels = prompt.get("user_point_labels") or prompt.get("point_labels") or []
        point_note = ""
        if isinstance(coords, list) and isinstance(labels, list) and len(coords) == len(labels) and coords:
            positive_count = sum(1 for label in labels if int(label) > 0)
            negative_count = len(labels) - positive_count
            point_note = f" +{positive_count}/-{negative_count}"
        if prompt.get("valid", False):
            x = prompt.get("rgb_x", "?")
            y = prompt.get("rgb_y", "?")
            return f"ok({x},{y}){point_note}"
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
        assert self.frame is not None
        rgb_h, rgb_w = self.frame.rgb.shape[:2]
        coords = prompt.get("user_point_coords") or prompt.get("point_coords") or []
        labels = prompt.get("user_point_labels") or prompt.get("point_labels") or []
        if isinstance(coords, list) and isinstance(labels, list) and len(coords) == len(labels):
            for index, (coord, label) in enumerate(zip(coords, labels), start=1):
                if not isinstance(coord, (list, tuple)) or len(coord) != 2:
                    continue
                try:
                    px = float(coord[0])
                    py = float(coord[1])
                    point_label = 1 if int(label) > 0 else 0
                except (TypeError, ValueError):
                    continue
                if px < 0 or px >= rgb_w or py < 0 or py >= rgb_h:
                    continue
                cx = int(np.clip(round(px * self.rgb_scale), 0, max(0, canvas_w - 1)))
                cy = int(np.clip(round(py * self.rgb_scale), 0, max(0, canvas_h - 1)))
                color = "#00e676" if point_label > 0 else "#ff4d4f"
                radius = 8
                self.rgb_canvas.create_oval(
                    cx - radius,
                    cy - radius,
                    cx + radius,
                    cy + radius,
                    fill=color,
                    outline="#101010",
                    width=2,
                )
                self.rgb_canvas.create_text(
                    cx,
                    cy,
                    text=str(index),
                    fill="#101010",
                    anchor=tk.CENTER,
                    font=("Consolas", 8, "bold"),
                )
        try:
            rgb_x = float(prompt["rgb_x"])
            rgb_y = float(prompt["rgb_y"])
        except (KeyError, TypeError, ValueError):
            return
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
        self._closing = True
        if self._network_refresh_after_id is not None:
            try:
                self.root.after_cancel(self._network_refresh_after_id)
            except Exception:
                pass
            self._network_refresh_after_id = None
        if self.discover_runtime is not None:
            self.discover_runtime.stop()
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
