from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np


@dataclass(slots=True)
class DepthSeed:
    valid: bool
    x: int
    y: int
    depth_m: float | None
    tolerance_m: float
    mad_m: float
    sample_count: int
    confidence: float
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeviceProposal:
    primary_mask: np.ndarray
    whole_mask: np.ndarray
    depth_component_mask: np.ndarray | None
    cluster_mask: np.ndarray | None
    support_plane_mask: np.ndarray | None
    bbox_xyxy: tuple[int, int, int, int]
    center_pixel: tuple[float, float]
    center_3d_m: tuple[float, float, float] | None
    depth_median_m: float | None
    depth_confidence: float
    segmentation_confidence: float
    source: str
    diagnostics: dict[str, Any] = field(default_factory=dict)


class CursorRGBDDeviceProposer:
    """Build cursor-conditioned device masks from SAM2 and aligned depth.

    The proposer treats the Quest cursor as the primary supervision signal.
    Depth is used to keep the mask tied to the same physical surface or local
    3D cluster as the cursor instead of trusting a detector's object boundary.
    """

    def __init__(
        self,
        *,
        min_depth_m: float = 0.08,
        max_depth_m: float = 8.0,
        min_mask_area: int = 40,
        max_flood_pixels: int = 220_000,
    ) -> None:
        self._min_depth_m = float(min_depth_m)
        self._max_depth_m = float(max_depth_m)
        self._min_mask_area = int(min_mask_area)
        self._max_flood_pixels = int(max_flood_pixels)

    def propose(
        self,
        *,
        rgb_shape: tuple[int, int],
        cursor_xy: tuple[int, int],
        sam_mask: np.ndarray,
        aligned_depth_m: np.ndarray | None = None,
        rgb_intrinsics: np.ndarray | None = None,
    ) -> DeviceProposal:
        h, w = rgb_shape[:2]
        px = int(np.clip(cursor_xy[0], 0, w - 1))
        py = int(np.clip(cursor_xy[1], 0, h - 1))
        sam_mask = _resize_mask(np.asarray(sam_mask, dtype=bool), h, w)
        if not sam_mask.any():
            sam_mask = np.zeros((h, w), dtype=bool)
            sam_mask[py, px] = True

        depth = _prepare_depth(aligned_depth_m, h, w)
        if depth is None:
            return self._rgb_only_proposal(sam_mask, px, py)

        filled_depth, original_valid = _fill_depth_holes(
            depth,
            min_depth_m=self._min_depth_m,
            max_depth_m=self._max_depth_m,
        )
        seed = self.estimate_seed(filled_depth, px, py, original_valid)
        if not seed.valid or seed.depth_m is None:
            return self._rgb_only_proposal(
                sam_mask, px, py,
                diagnostics={"depth_seed": seed.diagnostics, "depth_available": True},
            )

        depth_component = self._depth_component(filled_depth, seed, px, py)
        depth_component = _close_mask(depth_component, radius=3)
        support_plane = self._support_plane_mask(
            filled_depth=filled_depth,
            valid_mask=original_valid,
            rgb_intrinsics=rgb_intrinsics,
            avoid_mask=sam_mask,
        )

        primary = self._fuse_primary_mask(sam_mask, depth_component, px, py)
        whole = self._expand_whole_device(
            sam_mask=sam_mask,
            primary_mask=primary,
            depth_component=depth_component,
            support_plane_mask=support_plane,
            px=px,
            py=py,
        )
        whole = _close_mask(whole, radius=4)
        whole = _fill_small_holes(whole, max_hole_area=max(64, int(whole.sum() * 0.05)))
        whole = _keep_component_near_cursor_or_mask(whole, px, py, primary)

        if whole.sum() < self._min_mask_area:
            whole = primary if primary.sum() >= self._min_mask_area else sam_mask

        bbox = _bbox_from_mask(whole)
        center_pixel = _mask_center(whole)
        center_3d = _center_3d_from_mask(whole, filled_depth, rgb_intrinsics)
        depth_values = depth[whole & original_valid]
        depth_median = float(np.nanmedian(depth_values)) if depth_values.size else seed.depth_m

        depth_overlap = _safe_ratio((whole & depth_component).sum(), max(whole.sum(), 1))
        sam_overlap = _safe_ratio((whole & sam_mask).sum(), max(whole.sum(), 1))
        depth_confidence = float(np.clip(0.55 * seed.confidence + 0.45 * depth_overlap, 0.0, 1.0))
        segmentation_confidence = float(
            np.clip(0.4 * sam_overlap + 0.4 * depth_overlap + 0.2 * seed.confidence, 0.0, 1.0)
        )

        return DeviceProposal(
            primary_mask=primary,
            whole_mask=whole,
            depth_component_mask=depth_component,
            cluster_mask=depth_component,
            support_plane_mask=support_plane,
            bbox_xyxy=bbox,
            center_pixel=center_pixel,
            center_3d_m=center_3d,
            depth_median_m=depth_median,
            depth_confidence=depth_confidence,
            segmentation_confidence=segmentation_confidence,
            source="rgbd_cursor_sam2",
            diagnostics={
                "depth_seed": {
                    "depth_m": seed.depth_m,
                    "tolerance_m": seed.tolerance_m,
                    "mad_m": seed.mad_m,
                    "sample_count": seed.sample_count,
                    "confidence": seed.confidence,
                    **seed.diagnostics,
                },
                "areas": {
                    "sam": int(sam_mask.sum()),
                    "primary": int(primary.sum()),
                    "whole": int(whole.sum()),
                    "depth_component": int(depth_component.sum()),
                    "support_plane": int(support_plane.sum()) if support_plane is not None else 0,
                },
                "overlap": {
                    "sam_in_whole": round(float(sam_overlap), 4),
                    "depth_in_whole": round(float(depth_overlap), 4),
                },
            },
        )

    def estimate_seed(
        self,
        depth: np.ndarray,
        px: int,
        py: int,
        valid_mask: np.ndarray | None = None,
    ) -> DepthSeed:
        h, w = depth.shape
        px = int(np.clip(px, 0, w - 1))
        py = int(np.clip(py, 0, h - 1))
        valid = _valid_depth_mask(depth, self._min_depth_m, self._max_depth_m)
        if valid_mask is not None:
            valid = valid & np.asarray(valid_mask, dtype=bool)

        best_values: np.ndarray | None = None
        used_radius = 0
        for radius in (2, 4, 8, 16, 32):
            y0, y1 = max(0, py - radius), min(h, py + radius + 1)
            x0, x1 = max(0, px - radius), min(w, px + radius + 1)
            local = depth[y0:y1, x0:x1]
            local_valid = valid[y0:y1, x0:x1]
            values = local[local_valid]
            if values.size >= 3:
                best_values = values
                used_radius = radius
                break

        if best_values is None or best_values.size == 0:
            return DepthSeed(
                valid=False,
                x=px,
                y=py,
                depth_m=None,
                tolerance_m=0.0,
                mad_m=0.0,
                sample_count=0,
                confidence=0.0,
                diagnostics={"reason": "no_valid_depth_near_cursor"},
            )

        median = float(np.median(best_values))
        mad = float(np.median(np.abs(best_values - median)))
        tolerance = max(0.04, 0.03 * median, 2.5 * mad)
        density = min(1.0, float(best_values.size) / 25.0)
        confidence = float(np.clip(density * (1.0 - min(mad / max(tolerance, 1e-6), 0.8)), 0.05, 1.0))
        return DepthSeed(
            valid=True,
            x=px,
            y=py,
            depth_m=median,
            tolerance_m=tolerance,
            mad_m=mad,
            sample_count=int(best_values.size),
            confidence=confidence,
            diagnostics={"search_radius_px": used_radius},
        )

    def refine_with_sam_mask(
        self,
        *,
        proposal: DeviceProposal,
        sam_refined_mask: np.ndarray,
        aligned_depth_m: np.ndarray | None,
        cursor_xy: tuple[int, int],
        rgb_intrinsics: np.ndarray | None = None,
    ) -> DeviceProposal:
        h, w = proposal.whole_mask.shape
        px = int(np.clip(cursor_xy[0], 0, w - 1))
        py = int(np.clip(cursor_xy[1], 0, h - 1))
        sam_refined = _resize_mask(np.asarray(sam_refined_mask, dtype=bool), h, w)

        depth_mask = proposal.depth_component_mask
        if depth_mask is not None and depth_mask.any():
            fused = sam_refined & _dilate_mask(depth_mask, radius=4)
            if fused.sum() < max(self._min_mask_area, int(0.08 * sam_refined.sum())):
                fused = (sam_refined & _dilate_mask(proposal.primary_mask, radius=8)) | proposal.primary_mask
        else:
            fused = sam_refined

        fused = fused | proposal.primary_mask
        if proposal.support_plane_mask is not None and proposal.support_plane_mask.any():
            removable_plane = proposal.support_plane_mask & ~_dilate_mask(proposal.primary_mask, radius=10)
            fused = fused & ~removable_plane
        fused = _close_mask(fused, radius=3)
        fused = _fill_small_holes(fused, max_hole_area=max(64, int(fused.sum() * 0.04)))
        fused = _keep_component_near_cursor_or_mask(fused, px, py, proposal.primary_mask)
        if fused.sum() < self._min_mask_area:
            fused = proposal.whole_mask

        depth = _prepare_depth(aligned_depth_m, h, w)
        if depth is not None:
            filled_depth, valid = _fill_depth_holes(
                depth,
                min_depth_m=self._min_depth_m,
                max_depth_m=self._max_depth_m,
            )
            center_3d = _center_3d_from_mask(fused, filled_depth, rgb_intrinsics)
            values = depth[fused & valid]
            depth_median = float(np.nanmedian(values)) if values.size else proposal.depth_median_m
        else:
            center_3d = proposal.center_3d_m
            depth_median = proposal.depth_median_m

        bbox = _bbox_from_mask(fused)
        whole_area = max(int(fused.sum()), 1)
        depth_overlap = 0.0
        if proposal.depth_component_mask is not None:
            depth_overlap = _safe_ratio((fused & proposal.depth_component_mask).sum(), whole_area)
        sam_overlap = _safe_ratio((fused & sam_refined).sum(), whole_area)
        seg_conf = float(
            np.clip(
                max(proposal.segmentation_confidence, 0.4 * sam_overlap + 0.4 * depth_overlap + 0.2),
                0.0,
                1.0,
            )
        )
        diagnostics = dict(proposal.diagnostics)
        diagnostics["sam_refinement"] = {
            "sam_refined_area": int(sam_refined.sum()),
            "fused_area": int(fused.sum()),
            "sam_overlap": round(float(sam_overlap), 4),
            "depth_overlap": round(float(depth_overlap), 4),
        }

        return DeviceProposal(
            primary_mask=proposal.primary_mask,
            whole_mask=fused,
            depth_component_mask=proposal.depth_component_mask,
            cluster_mask=proposal.cluster_mask,
            support_plane_mask=proposal.support_plane_mask,
            bbox_xyxy=bbox,
            center_pixel=_mask_center(fused),
            center_3d_m=center_3d,
            depth_median_m=depth_median,
            depth_confidence=proposal.depth_confidence,
            segmentation_confidence=seg_conf,
            source=proposal.source + "+sam2_refined",
            diagnostics=diagnostics,
        )

    def _rgb_only_proposal(
        self,
        sam_mask: np.ndarray,
        px: int,
        py: int,
        diagnostics: dict[str, Any] | None = None,
    ) -> DeviceProposal:
        mask = _close_mask(sam_mask, radius=2)
        mask = _keep_component_near_cursor_or_mask(mask, px, py, sam_mask)
        bbox = _bbox_from_mask(mask)
        return DeviceProposal(
            primary_mask=mask,
            whole_mask=mask,
            depth_component_mask=None,
            cluster_mask=None,
            support_plane_mask=None,
            bbox_xyxy=bbox,
            center_pixel=_mask_center(mask),
            center_3d_m=None,
            depth_median_m=None,
            depth_confidence=0.0,
            segmentation_confidence=0.45 if mask.any() else 0.0,
            source="sam2_rgb_only",
            diagnostics=diagnostics or {"depth_available": False, "areas": {"sam": int(sam_mask.sum())}},
        )

    def _depth_component(
        self,
        filled_depth: np.ndarray,
        seed: DepthSeed,
        px: int,
        py: int,
    ) -> np.ndarray:
        assert seed.depth_m is not None
        h, w = filled_depth.shape
        valid = _valid_depth_mask(filled_depth, self._min_depth_m, self._max_depth_m)
        z0 = float(seed.depth_m)
        broad = valid & (np.abs(filled_depth - z0) <= max(seed.tolerance_m * 2.2, 0.10))

        start = _nearest_true_pixel(broad, px, py, max_radius=24)
        if start is None:
            return np.zeros((h, w), dtype=bool)

        sx, sy = start
        visited = np.zeros((h, w), dtype=bool)
        component = np.zeros((h, w), dtype=bool)
        queue: deque[tuple[int, int]] = deque([(sx, sy)])
        visited[sy, sx] = True
        accepted = 0
        absolute_tol = max(seed.tolerance_m * 4.0, 0.22, 0.10 * z0)
        local_tol = max(0.05, 0.035 * z0, seed.tolerance_m)

        while queue and accepted < self._max_flood_pixels:
            x, y = queue.popleft()
            z = float(filled_depth[y, x])
            if not np.isfinite(z) or abs(z - z0) > absolute_tol:
                continue
            component[y, x] = True
            accepted += 1
            for nx in (x - 1, x, x + 1):
                for ny in (y - 1, y, y + 1):
                    if nx == x and ny == y:
                        continue
                    if nx < 0 or ny < 0 or nx >= w or ny >= h or visited[ny, nx]:
                        continue
                    visited[ny, nx] = True
                    nz = float(filled_depth[ny, nx])
                    if not np.isfinite(nz):
                        continue
                    if abs(nz - z) <= local_tol and abs(nz - z0) <= absolute_tol:
                        queue.append((nx, ny))

        return component

    def _fuse_primary_mask(
        self,
        sam_mask: np.ndarray,
        depth_component: np.ndarray,
        px: int,
        py: int,
    ) -> np.ndarray:
        expanded_depth = _dilate_mask(depth_component, radius=4)
        primary = sam_mask & expanded_depth
        min_expected = max(self._min_mask_area, int(0.08 * max(int(sam_mask.sum()), 1)))
        if primary.sum() < min_expected:
            primary = sam_mask & _dilate_mask(depth_component, radius=9)
        if primary.sum() < self._min_mask_area:
            primary = depth_component & _dilate_mask(sam_mask, radius=9)
        if primary.sum() < self._min_mask_area:
            primary = sam_mask
        return _keep_component_near_cursor_or_mask(primary, px, py, sam_mask)

    def _expand_whole_device(
        self,
        *,
        sam_mask: np.ndarray,
        primary_mask: np.ndarray,
        depth_component: np.ndarray,
        support_plane_mask: np.ndarray | None,
        px: int,
        py: int,
    ) -> np.ndarray:
        candidate = depth_component | primary_mask | (sam_mask & _dilate_mask(depth_component, radius=10))
        if support_plane_mask is not None and support_plane_mask.any():
            candidate = candidate & ~(support_plane_mask & ~_dilate_mask(primary_mask, radius=10))
        candidate = _close_mask(candidate, radius=5)
        return _keep_component_near_cursor_or_mask(candidate, px, py, primary_mask)

    def _support_plane_mask(
        self,
        *,
        filled_depth: np.ndarray,
        valid_mask: np.ndarray,
        rgb_intrinsics: np.ndarray | None,
        avoid_mask: np.ndarray,
    ) -> np.ndarray | None:
        if rgb_intrinsics is None:
            return None
        valid = valid_mask & _valid_depth_mask(filled_depth, self._min_depth_m, self._max_depth_m)
        if valid.sum() < 600:
            return None
        h, w = filled_depth.shape
        ys, xs = np.where(valid)
        if xs.size > 4000:
            indices = np.linspace(0, xs.size - 1, 4000).astype(np.int64)
            xs_s = xs[indices]
            ys_s = ys[indices]
        else:
            xs_s, ys_s = xs, ys
        points = _pixels_to_points(xs_s, ys_s, filled_depth[ys_s, xs_s], rgb_intrinsics)
        plane = _ransac_plane(points, iterations=96, distance_threshold=0.018)
        if plane is None:
            return None
        normal, offset = plane
        all_points = _pixels_to_points(xs, ys, filled_depth[ys, xs], rgb_intrinsics)
        distances = np.abs(all_points @ normal + offset)
        inlier_pixels = np.zeros((h, w), dtype=bool)
        inlier_pixels[ys[distances <= 0.02], xs[distances <= 0.02]] = True
        if inlier_pixels.sum() < max(800, int(0.08 * h * w)):
            return None
        overlap = _safe_ratio((inlier_pixels & avoid_mask).sum(), max(avoid_mask.sum(), 1))
        if overlap > 0.45:
            return None
        return _close_mask(inlier_pixels, radius=3)


def _prepare_depth(depth: np.ndarray | None, h: int, w: int) -> np.ndarray | None:
    if depth is None:
        return None
    arr = np.asarray(depth, dtype=np.float32)
    if arr.ndim != 2 or arr.size == 0:
        return None
    if arr.shape != (h, w):
        arr = cv2.resize(arr, (w, h), interpolation=cv2.INTER_NEAREST)
    return arr


def _fill_depth_holes(
    depth: np.ndarray,
    *,
    min_depth_m: float,
    max_depth_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    valid = _valid_depth_mask(depth, min_depth_m, max_depth_m)
    if not valid.any():
        return depth.copy(), valid
    filled = depth.astype(np.float32, copy=True)
    if valid.all():
        return filled, valid
    median = float(np.nanmedian(filled[valid]))
    filled[~valid] = median
    invalid_u8 = (~valid).astype(np.uint8)
    if invalid_u8.mean() < 0.85:
        filled = cv2.inpaint(filled, invalid_u8, inpaintRadius=3, flags=cv2.INPAINT_NS)
    return filled.astype(np.float32), valid


def _valid_depth_mask(depth: np.ndarray, min_depth_m: float, max_depth_m: float) -> np.ndarray:
    return np.isfinite(depth) & (depth >= min_depth_m) & (depth <= max_depth_m)


def _resize_mask(mask: np.ndarray, h: int, w: int) -> np.ndarray:
    if mask.shape == (h, w):
        return mask.astype(bool, copy=False)
    resized = cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
    return resized.astype(bool)


def _bbox_from_mask(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(mask)
    if xs.size == 0:
        return (0, 0, 0, 0)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _mask_center(mask: np.ndarray) -> tuple[float, float]:
    ys, xs = np.where(mask)
    if xs.size == 0:
        return (0.0, 0.0)
    return float(xs.mean()), float(ys.mean())


def _dilate_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0 or not mask.any():
        return mask.astype(bool, copy=True)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    return cv2.dilate(mask.astype(np.uint8), kernel, iterations=1).astype(bool)


def _close_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0 or not mask.any():
        return mask.astype(bool, copy=True)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    closed = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel)
    return closed.astype(bool)


def _fill_small_holes(mask: np.ndarray, max_hole_area: int) -> np.ndarray:
    if not mask.any():
        return mask.astype(bool, copy=True)
    inv = (~mask).astype(np.uint8)
    labels_count, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    filled = mask.copy()
    h, w = mask.shape
    for label in range(1, labels_count):
        x, y, bw, bh, area = stats[label]
        touches_border = x == 0 or y == 0 or (x + bw) >= w or (y + bh) >= h
        if not touches_border and area <= max_hole_area:
            filled[labels == label] = True
    return filled


def _keep_component_near_cursor_or_mask(
    mask: np.ndarray,
    px: int,
    py: int,
    anchor_mask: np.ndarray,
) -> np.ndarray:
    if not mask.any():
        return mask.astype(bool, copy=True)
    labels_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if labels_count <= 1:
        return mask.astype(bool, copy=True)
    h, w = mask.shape
    px = int(np.clip(px, 0, w - 1))
    py = int(np.clip(py, 0, h - 1))
    cursor_label = labels[py, px]
    if cursor_label > 0:
        return labels == cursor_label

    best_label = 0
    best_score = -1.0
    anchor = anchor_mask.astype(bool)
    for label in range(1, labels_count):
        component = labels == label
        area = int(stats[label, cv2.CC_STAT_AREA])
        overlap = int((component & anchor).sum())
        ys, xs = np.where(component)
        if xs.size == 0:
            continue
        dist = float(np.hypot(float(xs.mean() - px), float(ys.mean() - py)))
        score = overlap * 4.0 + min(area, 10_000) * 0.01 - dist
        if score > best_score:
            best_score = score
            best_label = label
    return labels == best_label if best_label > 0 else mask.astype(bool, copy=True)


def _nearest_true_pixel(mask: np.ndarray, px: int, py: int, max_radius: int) -> tuple[int, int] | None:
    h, w = mask.shape
    px = int(np.clip(px, 0, w - 1))
    py = int(np.clip(py, 0, h - 1))
    if mask[py, px]:
        return px, py
    for radius in range(1, max_radius + 1):
        y0, y1 = max(0, py - radius), min(h, py + radius + 1)
        x0, x1 = max(0, px - radius), min(w, px + radius + 1)
        ys, xs = np.where(mask[y0:y1, x0:x1])
        if xs.size:
            xs = xs + x0
            ys = ys + y0
            nearest = int(np.argmin((xs - px) ** 2 + (ys - py) ** 2))
            return int(xs[nearest]), int(ys[nearest])
    return None


def _pixels_to_points(
    xs: np.ndarray,
    ys: np.ndarray,
    depths: np.ndarray,
    intrinsics: np.ndarray,
) -> np.ndarray:
    fx = float(intrinsics[0, 0])
    fy = float(intrinsics[1, 1])
    cx = float(intrinsics[0, 2])
    cy = float(intrinsics[1, 2])
    z = depths.astype(np.float32)
    x = (xs.astype(np.float32) - cx) * z / max(fx, 1e-6)
    y = (ys.astype(np.float32) - cy) * z / max(fy, 1e-6)
    return np.stack([x, y, z], axis=1).astype(np.float32)


def _center_3d_from_mask(
    mask: np.ndarray,
    depth: np.ndarray,
    intrinsics: np.ndarray | None,
) -> tuple[float, float, float] | None:
    if intrinsics is None or not mask.any():
        return None
    valid = mask & np.isfinite(depth) & (depth > 0)
    if valid.sum() < 3:
        return None
    ys, xs = np.where(valid)
    points = _pixels_to_points(xs, ys, depth[ys, xs], intrinsics)
    center = np.median(points, axis=0)
    return float(center[0]), float(center[1]), float(center[2])


def _ransac_plane(
    points: np.ndarray,
    *,
    iterations: int,
    distance_threshold: float,
) -> tuple[np.ndarray, float] | None:
    if points.shape[0] < 100:
        return None
    rng = np.random.default_rng(42)
    best_normal: np.ndarray | None = None
    best_offset = 0.0
    best_inliers = 0
    n = points.shape[0]
    for _ in range(iterations):
        ids = rng.choice(n, size=3, replace=False)
        p0, p1, p2 = points[ids]
        normal = np.cross(p1 - p0, p2 - p0)
        norm = float(np.linalg.norm(normal))
        if norm < 1e-6:
            continue
        normal = normal / norm
        offset = -float(normal @ p0)
        distances = np.abs(points @ normal + offset)
        inliers = int((distances < distance_threshold).sum())
        if inliers > best_inliers:
            best_inliers = inliers
            best_normal = normal
            best_offset = offset
    if best_normal is None or best_inliers < max(80, int(0.12 * n)):
        return None
    return best_normal.astype(np.float32), float(best_offset)


def _safe_ratio(numerator: float | int, denominator: float | int) -> float:
    denom = float(denominator)
    if denom <= 0:
        return 0.0
    return float(numerator) / denom
