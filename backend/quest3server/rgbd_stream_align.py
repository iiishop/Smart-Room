"""Real-time RGB-D alignment from Quest 3 streaming data.

Mirrors backend/tools/quest3_rgbd_align_viewer.py math, adapted for
in-memory streaming frames (no disk I/O).
"""

from __future__ import annotations

import numpy as np
import cv2

from quest3server.tracking.rgbd_final_alignment import (
    unproject_depth_to_world,
    world_to_rgb_camera,
    project_rgb,
    make_overlay,
)


def build_rgb_meta(rgb_intrinsics: dict, rgb_shape: tuple[int, int]) -> dict:
    """Build the 'rgb' sub-dict that align_final_rgbd_payload expects."""
    return {
        "focal_length_x": float(rgb_intrinsics.get("fx", 0)),
        "focal_length_y": float(rgb_intrinsics.get("fy", 0)),
        "principal_point_x": float(rgb_intrinsics.get("cx", 0)),
        "principal_point_y": float(rgb_intrinsics.get("cy", 0)),
        "resolution_w": rgb_shape[1],
        "resolution_h": rgb_shape[0],
        "pose_position_x": float(rgb_intrinsics.get("pose_position_x", 0)),
        "pose_position_y": float(rgb_intrinsics.get("pose_position_y", 0)),
        "pose_position_z": float(rgb_intrinsics.get("pose_position_z", 0)),
        "pose_rotation_x": float(rgb_intrinsics.get("pose_rotation_x", 0)),
        "pose_rotation_y": float(rgb_intrinsics.get("pose_rotation_y", 0)),
        "pose_rotation_z": float(rgb_intrinsics.get("pose_rotation_z", 0)),
        "pose_rotation_w": float(rgb_intrinsics.get("pose_rotation_w", 1)),
    }


def build_depth_meta(
    depth_descriptor: dict,
    depth_shape: tuple[int, int],
    zbuffer_params: list[float] | None = None,
) -> dict:
    """Build the 'depth' sub-dict that align_final_rgbd_payload expects."""
    zbp = (
        depth_descriptor.get("zbuffer_params")
        or [depth_descriptor.get(f"zbuffer_{k}", 1.0 if k == "x" else 0.0)
            for k in ("x", "y", "z", "w")]
    )
    # Prefer explicitly sent zbuffer fields
    zb_x = float(depth_descriptor.get("zbuffer_x", zbp[0]))
    zb_y = float(depth_descriptor.get("zbuffer_y", zbp[1]))
    zb_z = float(depth_descriptor.get("zbuffer_z", zbp[2]))
    zb_w = float(depth_descriptor.get("zbuffer_w", zbp[3]))

    return {
        "resolution_w": depth_shape[1],
        "resolution_h": depth_shape[0],
        "pose_position_x": float(depth_descriptor.get("pose_position_x", 0)),
        "pose_position_y": float(depth_descriptor.get("pose_position_y", 0)),
        "pose_position_z": float(depth_descriptor.get("pose_position_z", 0)),
        "pose_rotation_x": float(depth_descriptor.get("pose_rotation_x", 0)),
        "pose_rotation_y": float(depth_descriptor.get("pose_rotation_y", 0)),
        "pose_rotation_z": float(depth_descriptor.get("pose_rotation_z", 0)),
        "pose_rotation_w": float(depth_descriptor.get("pose_rotation_w", 1)),
        "fov_left": float(depth_descriptor.get("fov_left", 0)),
        "fov_right": float(depth_descriptor.get("fov_right", 0)),
        "fov_top": float(depth_descriptor.get("fov_top", 0)),
        "fov_bottom": float(depth_descriptor.get("fov_bottom", 0)),
        "near_z": float(depth_descriptor.get("near_z", 0.1)),
        "far_z": float(depth_descriptor.get("far_z", float("inf"))),
        "zbuffer_x": zb_x,
        "zbuffer_y": zb_y,
        "zbuffer_z": zb_z,
        "zbuffer_w": zb_w,
    }


def align_streaming_rgbd(
    rgb_bgr: np.ndarray,
    depth_raw: np.ndarray,          # float32, [0,1] NDC
    rgb_intrinsics: dict,
    depth_descriptor: dict,
    *,
    min_depth: float = 0.2,
    max_depth: float = 8.0,
) -> np.ndarray | None:
    """Align streaming depth to RGB frame. Returns aligned_depth in metres, shape matches RGB.

    Uses the same reprojection math as the viewer's align_depth_to_rgb_sdk.
    """
    if rgb_bgr is None or depth_raw is None:
        return None
    if depth_raw.size == 0:
        return None

    rgb_h, rgb_w = rgb_bgr.shape[:2]
    depth_h, depth_w = depth_raw.shape
    if depth_h < 2 or depth_w < 2:
        return None

    rgb_meta = build_rgb_meta(rgb_intrinsics, (rgb_h, rgb_w))
    depth_meta = build_depth_meta(depth_descriptor, (depth_h, depth_w))

    depth_m = np.asarray(depth_raw, dtype=np.float32).reshape(depth_h, depth_w)
    # Depth is already linear meters from streaming, not NDC
    valid = np.isfinite(depth_m) & (depth_m >= min_depth) & (depth_m <= max_depth)

    points_world = unproject_depth_to_world(depth_m, depth_meta, valid)
    if points_world.size == 0:
        return None

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

    aligned = np.full((rgb_h, rgb_w), np.inf, dtype=np.float32)
    if u.size:
        np.minimum.at(aligned, (v, u), points_rgb[:, 2].astype(np.float32))
    aligned[~np.isfinite(aligned)] = 0.0
    return aligned


def make_overlay_jpeg(
    rgb_rgb: np.ndarray,
    aligned_depth: np.ndarray,
    min_depth: float = 0.2,
    max_depth: float = 8.0,
    jpeg_quality: int = 85,
) -> bytes | None:
    """Create a depth-coloured overlay on RGB and encode as JPEG bytes."""
    if aligned_depth is None or not np.any(aligned_depth > 0):
        # No valid depth — return RGB as-is
        _, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb_rgb, cv2.COLOR_RGB2BGR),
                               [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        return buf.tobytes()

    overlay_rgb = make_overlay(rgb_rgb, aligned_depth, min_depth, max_depth)
    _, buf = cv2.imencode(".jpg", cv2.cvtColor(overlay_rgb, cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
    return buf.tobytes()
