from __future__ import annotations

import cv2
import numpy as np


def world_to_rgb_pixel(
    world_points: np.ndarray,
    camera_pose_world: np.ndarray,
    rgb_intrinsics: np.ndarray,
    rgb_h: int,
    rgb_w: int,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Project world points to RGB pixel coordinates."""
    if world_points.size == 0:
        return None

    cam_pose_inv = np.linalg.inv(camera_pose_world.astype(np.float32))
    ones = np.ones((world_points.shape[0], 1), dtype=np.float32)
    world_h = np.hstack([world_points.astype(np.float32), ones])
    cam_h = world_h @ cam_pose_inv.T
    cam_pts = cam_h[:, :3]

    fx, fy = float(rgb_intrinsics[0, 0]), float(rgb_intrinsics[1, 1])
    cx, cy = float(rgb_intrinsics[0, 2]), float(rgb_intrinsics[1, 2])
    sensor_h = max(int(rgb_h), 1)

    in_front = cam_pts[:, 2] > 0.01
    if not in_front.any():
        return None

    u = np.rint(cam_pts[:, 0] * fx / cam_pts[:, 2] + cx).astype(np.int32)
    sensor_y = cam_pts[:, 1] * fy / cam_pts[:, 2] + cy
    v = np.rint((sensor_h - 1) - sensor_y).astype(np.int32)
    in_frame = (u >= 0) & (u < rgb_w) & (v >= 0) & (v < rgb_h) & in_front
    return np.column_stack([u, v]), in_frame


def rgb_pixel_to_world(
    pixels: np.ndarray,
    depth_map: np.ndarray,
    camera_pose_world: np.ndarray,
    rgb_intrinsics: np.ndarray,
    depth_scale_m: float = 1.0,
) -> np.ndarray | None:
    """Back-project RGB pixels with depth to world coordinates."""
    if pixels.size == 0:
        return None

    px = pixels[:, 0].astype(np.int32)
    py = pixels[:, 1].astype(np.int32)
    h, w = depth_map.shape
    valid = (px >= 0) & (px < w) & (py >= 0) & (py < h)
    if not valid.any():
        return None

    px = px[valid]
    py = py[valid]
    depths = depth_map[py, px].astype(np.float32) * np.float32(depth_scale_m)
    depth_ok = np.isfinite(depths) & (depths > 0)
    if not depth_ok.any():
        return None

    px = px[depth_ok]
    py = py[depth_ok]
    depths = depths[depth_ok]

    fx, fy = float(rgb_intrinsics[0, 0]), float(rgb_intrinsics[1, 1])
    cx, cy = float(rgb_intrinsics[0, 2]), float(rgb_intrinsics[1, 2])
    sensor_y = (h - 1) - py.astype(np.float32)
    cam_x = (px.astype(np.float32) - cx) * depths / max(fx, 1e-6)
    cam_y = (sensor_y - cy) * depths / max(fy, 1e-6)
    cam_pts = np.column_stack([cam_x, cam_y, depths])

    ones = np.ones((cam_pts.shape[0], 1), dtype=np.float32)
    cam_h = np.hstack([cam_pts, ones])
    world_h = cam_h @ camera_pose_world.astype(np.float32).T
    return world_h[:, :3].astype(np.float32)


def extract_world_contour(
    mask: np.ndarray,
    depth_map: np.ndarray,
    camera_pose_world: np.ndarray,
    rgb_intrinsics: np.ndarray,
    max_points: int = 500,
) -> list[list[float]]:
    """Extract a simplified world-space contour from a mask."""
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []

    longest = max(contours, key=lambda contour: cv2.arcLength(contour, closed=True))
    pixels = longest.reshape(-1, 2)
    if pixels.shape[0] > max_points:
        indices = np.linspace(0, pixels.shape[0] - 1, max_points, dtype=np.int32)
        pixels = pixels[indices]

    world_pts = rgb_pixel_to_world(pixels, depth_map, camera_pose_world, rgb_intrinsics)
    if world_pts is None:
        return []
    return world_pts.astype(float).tolist()
