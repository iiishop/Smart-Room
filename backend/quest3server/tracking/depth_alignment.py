"""RGB–Depth alignment for Quest 3.

Quest 3's RGB camera and depth sensor are physically offset with different
intrinsics.  This module reprojects each depth pixel into the RGB frame
so downstream code can query "what is the depth at this RGB pixel?".

Reference
---------
QuestRealityCapture (t-34400):
  https://github.com/t-34400/QuestRealityCapture
  "cast each pixel of the depth map to world coordinates using the depth
   reprojection matrix, and then reproject those points into the color
   camera's coordinate system using the color camera intrinsics/extrinsics."
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def align_depth_to_rgb(
    depth_meters: np.ndarray,       # H_d × W_d  float32, in metres
    depth_reproj: np.ndarray,       # 4×4  depth → world  reprojection matrix
    rgb_intrinsics: np.ndarray,     # 3×3  RGB camera intrinsic matrix
    rgb_h: int,
    rgb_w: int,
) -> np.ndarray | None:
    """Produce a depth map aligned to the RGB camera frame.

    Parameters
    ----------
    depth_meters : np.ndarray
        Raw depth frame from Quest EnvironmentDepthManager.  Shape (H, W),
        values in metres.  Invalid pixels are typically 0 or NaN.
    depth_reproj : np.ndarray
        4×4 matrix that maps (u, v, depth) → world XYZ.  Obtained from
        the Unity shader global ``_EnvironmentDepthReprojectionMatrices``.
    rgb_intrinsics : np.ndarray
        3×3 RGB camera intrinsic matrix in pixel units:
        [[fx,  0, cx],
         [ 0, fy, cy],
         [ 0,  0,  1]]
    rgb_h, rgb_w : int
        Dimensions of the RGB frame (typically 640×360 or 1280×960).

    Returns
    -------
    np.ndarray or None
        Aligned depth map of shape (rgb_h, rgb_w), dtype float32, in metres.
        Returns None if the inputs are invalid.
    """
    # ── validate inputs ────────────────────────────────────────────
    if depth_meters is None or depth_meters.size == 0:
        return None
    if depth_reproj.shape != (4, 4):
        logger.warning("depth_reproj expected 4×4, got %s", depth_reproj.shape)
        return None
    if rgb_intrinsics.shape != (3, 3):
        logger.warning("rgb_intrinsics expected 3×3, got %s", rgb_intrinsics.shape)
        return None

    dh, dw = depth_meters.shape
    if dh < 2 or dw < 2:
        return None

    # ── Step 1: depth pixel coords → world XYZ ─────────────────────
    # Create pixel grid for the depth frame
    vv, uu = np.mgrid[0:dh, 0:dw]
    ones = np.ones_like(uu, dtype=np.float32)
    depth_flat = depth_meters.ravel()

    # Homogeneous depth-frame pixel coords: (u, v, 1, 1/depth)
    # Use 1/depth because depth_reproj is the *reprojection* matrix
    # (inverse projection).  Sign convention follows Unity's convention
    # where the matrix maps from NDC-like coords to world.
    pix_homo = np.stack([
        uu.ravel().astype(np.float32),
        vv.ravel().astype(np.float32),
        ones.ravel(),
        ones.ravel() / np.maximum(depth_flat, 1e-6),
    ], axis=1)  # (N, 4)

    # world = depth_reproj @ pixel_homo
    world_homo = pix_homo @ depth_reproj.T  # (N, 4)
    # Perspective divide
    w = world_homo[:, 3:4]
    w_safe = np.where(np.abs(w) < 1e-10, 1e-10, w)
    world_xyz = world_homo[:, :3] / w_safe  # (N, 3)

    # ── Step 2: world XYZ → RGB pixel coords ───────────────────────
    fx, fy = rgb_intrinsics[0, 0], rgb_intrinsics[1, 1]
    cx, cy = rgb_intrinsics[0, 2], rgb_intrinsics[1, 2]

    X, Y, Z = world_xyz[:, 0], world_xyz[:, 1], world_xyz[:, 2]
    # Only points in front of the camera are valid
    valid = Z > 0.01

    u_rgb = np.full_like(X, -1, dtype=np.float32)
    v_rgb = np.full_like(Y, -1, dtype=np.float32)

    u_rgb[valid] = (fx * X[valid] / Z[valid]) + cx
    v_rgb[valid] = (fy * Y[valid] / Z[valid]) + cy

    # ── Step 3: scatter depth values into the RGB frame ────────────
    # Round to nearest pixel
    u_int = np.round(u_rgb).astype(np.int32)
    v_int = np.round(v_rgb).astype(np.int32)

    in_bounds = (u_int >= 0) & (u_int < rgb_w) & (v_int >= 0) & (v_int < rgb_h)
    valid = valid & in_bounds

    aligned = np.full((rgb_h, rgb_w), np.nan, dtype=np.float32)

    # If multiple depth pixels land on the same RGB pixel, take the
    # *nearest* (smallest depth), which is what the user actually sees.
    for i in np.where(valid)[0]:
        dv = depth_flat[i]
        vi, ui = v_int[i], u_int[i]
        if np.isnan(aligned[vi, ui]) or dv < aligned[vi, ui]:
            aligned[vi, ui] = dv

    # ── Step 4: fill small holes with nearest-neighbour ────────────
    nan_mask = np.isnan(aligned)
    if nan_mask.any() and not nan_mask.all():
        # Use OpenCV inpainting with a small radius to fill scattered NaNs
        aligned_u8 = np.where(nan_mask, 0, aligned).astype(np.float32)
        mask_u8 = nan_mask.astype(np.uint8)
        aligned = cv2.inpaint(
            aligned_u8, mask_u8, inpaintRadius=2, flags=cv2.INPAINT_NS,
        ).astype(np.float32)

    return aligned


def intrinsics_from_focal_principal(
    fx: float, fy: float, cx: float, cy: float,
) -> np.ndarray:
    """Build a 3×3 intrinsic matrix from focal lengths and principal point."""
    return np.array([
        [fx, 0, cx],
        [0, fy, cy],
        [0,  0,  1],
    ], dtype=np.float32)


def query_depth_at_pixel(
    aligned_depth: np.ndarray,
    px: int,
    py: int,
) -> float | None:
    """Return the depth (metres) at a given RGB pixel, or None."""
    if aligned_depth is None:
        return None
    h, w = aligned_depth.shape
    if px < 0 or px >= w or py < 0 or py >= h:
        return None
    val = aligned_depth[py, px]
    if np.isnan(val) or val <= 0:
        return None
    return float(val)


def render_topdown(
    aligned_depth: np.ndarray,
    K: np.ndarray,
    cursor_px: int | None = None,
    cursor_py: int | None = None,
    img_w: int = 640,
    img_h: int = 480,
    max_depth: float = 5.0,
) -> np.ndarray | None:
    """Render a bird's-eye (top-down) view of the aligned depth data.

    X axis = horizontal position in camera space (left→right).
    Y axis = depth / Z (near→far, top→bottom).

    Each valid depth pixel becomes a coloured dot; the cursor position
    is marked with a red crosshair.

    Returns a BGR image (numpy array) or None.
    """
    if aligned_depth is None or aligned_depth.size == 0:
        return None

    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]
    if fx <= 0 or fy <= 0:
        return None

    h, w = aligned_depth.shape

    # Subsample for performance (max ~5000 points)
    step = max(1, int(np.sqrt(h * w / 5000)))
    rows = np.arange(0, h, step)
    cols = np.arange(0, w, step)
    dv, du = np.meshgrid(rows, cols, indexing="ij")
    depths = aligned_depth[::step, ::step]

    valid = (depths > 0.1) & (depths < max_depth) & ~np.isnan(depths)
    if not valid.any():
        return None

    d_vals = depths[valid]
    u_vals = du[valid]
    v_vals = dv[valid]

    # Camera-space coords
    X = (u_vals.astype(np.float32) - cx) * d_vals / fx
    Z = d_vals

    # Normalise to image space
    x_min, x_max = float(np.percentile(X, 1)), float(np.percentile(X, 99))
    z_min, z_max = float(np.percentile(Z, 1)), float(np.percentile(Z, 99))
    x_range = max(x_max - x_min, 0.1)
    z_range = max(z_max - z_min, 0.1)

    canvas = np.full((img_h, img_w, 3), 30, dtype=np.uint8)  # dark grey bg

    # Depth → hue colour mapping (near=red, far=blue)
    d_norm = np.clip((d_vals - z_min) / z_range, 0, 1)
    hue = (1.0 - d_norm) * 120  # 120° (green) → 0° (red)
    sat = np.ones_like(hue) * 200
    val = np.ones_like(hue) * 220
    hsv = np.stack([
        hue.astype(np.uint8),
        sat.astype(np.uint8),
        val.astype(np.uint8),
    ], axis=1).reshape(-1, 1, 3)
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR).reshape(-1, 3)

    # Plot all points at once via vectorized indexing
    px_img = ((X - x_min) / x_range * (img_w - 40) + 20).astype(np.int32)
    py_img = ((Z - z_min) / z_range * (img_h - 40) + 20).astype(np.int32)
    valid_idx = (px_img >= 0) & (px_img < img_w) & (py_img >= 0) & (py_img < img_h)
    canvas[py_img[valid_idx], px_img[valid_idx]] = bgr[valid_idx]

    # Draw cursor crosshair
    if cursor_px is not None and cursor_py is not None:
        cd = aligned_depth[int(np.clip(cursor_py, 0, h - 1)),
                           int(np.clip(cursor_px, 0, w - 1))]
        if not np.isnan(cd) and cd > 0.1:
            cX = (cursor_px - cx) * cd / fx
            cZ = cd
            ci = int((cX - x_min) / x_range * (img_w - 40) + 20)
            cj = int((cZ - z_min) / z_range * (img_h - 40) + 20)
            arm = 12
            red = (0, 0, 255)
            cv2.line(canvas, (max(0, ci - arm), cj),
                     (min(img_w - 1, ci + arm), cj), red, 2)
            cv2.line(canvas, (ci, max(0, cj - arm)),
                     (ci, min(img_h - 1, cj + arm)), red, 2)
            cv2.circle(canvas, (ci, cj), 5, red, 2)

    # Axes labels
    cv2.putText(canvas, f"X: {x_min:.1f} .. {x_max:.1f} m",
                (20, img_h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)
    cv2.putText(canvas, f"Z: {z_min:.1f} .. {z_max:.1f} m",
                (20, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)

    return canvas
