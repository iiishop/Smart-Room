from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class CursorPromptConfig:
    nearest_depth_radius_px: int = 10


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


def world_to_rgb_camera(point_world: np.ndarray, rgb_meta: dict) -> np.ndarray:
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
    return (point_world.astype(np.float32) - rgb_pos) @ quaternion_to_matrix(rgb_quat)


def project_rgb_camera_point(point_rgb: np.ndarray, rgb_meta: dict) -> tuple[int, int, float] | None:
    z = float(point_rgb[2])
    if not np.isfinite(z) or z <= 1e-4:
        return None

    fx = float(rgb_meta["focal_length_x"])
    fy = float(rgb_meta["focal_length_y"])
    cx = float(rgb_meta["principal_point_x"])
    cy = float(rgb_meta["principal_point_y"])
    height = int(rgb_meta["resolution_h"])
    u = int(round(float(point_rgb[0]) * fx / z + cx))
    sensor_y = float(point_rgb[1]) * fy / z + cy
    v = int(round((height - 1) - sensor_y))
    return u, v, z


def _nearest_valid_depth(depth: np.ndarray, x: int, y: int, radius: int) -> tuple[float | None, int | None, int | None]:
    h, w = depth.shape
    if 0 <= x < w and 0 <= y < h and np.isfinite(depth[y, x]) and depth[y, x] > 0:
        return float(depth[y, x]), x, y

    radius = max(0, int(radius))
    x0 = max(0, x - radius)
    x1 = min(w, x + radius + 1)
    y0 = max(0, y - radius)
    y1 = min(h, y + radius + 1)
    crop = depth[y0:y1, x0:x1]
    valid_y, valid_x = np.where(np.isfinite(crop) & (crop > 0))
    if valid_x.size == 0:
        return None, None, None
    dx = valid_x + x0 - x
    dy = valid_y + y0 - y
    idx = int(np.argmin(dx * dx + dy * dy))
    px = int(valid_x[idx] + x0)
    py = int(valid_y[idx] + y0)
    return float(depth[py, px]), px, py


def _read_cursor_payload(cursor_payload: dict | str | bytes | None) -> dict:
    if cursor_payload is None:
        return {}
    if isinstance(cursor_payload, bytes):
        cursor_payload = cursor_payload.decode("utf-8")
    if isinstance(cursor_payload, str):
        return json.loads(cursor_payload)
    return dict(cursor_payload)


def build_cursor_prompt(
    meta: dict,
    cursor_payload: dict | str | bytes | None,
    depth: np.ndarray | None = None,
    config: CursorPromptConfig = CursorPromptConfig(),
) -> dict:
    cursor = _read_cursor_payload(cursor_payload)
    if not cursor:
        return {"valid": False, "reason": "missing_cursor_payload"}
    if not bool(cursor.get("is_hitting", cursor.get("isHitting", False))):
        return {"valid": False, "reason": "cursor_not_hitting", "cursor": cursor}

    try:
        world = np.array(
            [
                float(cursor["hit_world_x"]),
                float(cursor["hit_world_y"]),
                float(cursor["hit_world_z"]),
            ],
            dtype=np.float32,
        )
    except KeyError:
        return {"valid": False, "reason": "missing_hit_world", "cursor": cursor}

    rgb_meta = meta["rgb"]
    rgb_point = world_to_rgb_camera(world, rgb_meta)
    projected = project_rgb_camera_point(rgb_point, rgb_meta)
    if projected is None:
        return {"valid": False, "reason": "cursor_behind_rgb_camera", "cursor": cursor}

    x, y, camera_z = projected
    width = int(rgb_meta["resolution_w"])
    height = int(rgb_meta["resolution_h"])
    if x < 0 or x >= width or y < 0 or y >= height:
        return {
            "valid": False,
            "reason": "cursor_outside_rgb",
            "rgb_x": x,
            "rgb_y": y,
            "rgb_camera_z_m": camera_z,
            "cursor": cursor,
        }

    sampled_depth = None
    sampled_x = None
    sampled_y = None
    if depth is not None:
        sampled_depth, sampled_x, sampled_y = _nearest_valid_depth(
            depth.astype(np.float32, copy=False),
            x,
            y,
            config.nearest_depth_radius_px,
        )

    prompt = {
        "valid": True,
        "reason": "ok",
        "rgb_x": int(x),
        "rgb_y": int(y),
        "point_coords": [[int(x), int(y)]],
        "point_labels": [1],
        "rgb_camera_xyz_m": [float(rgb_point[0]), float(rgb_point[1]), float(rgb_point[2])],
        "rgb_camera_z_m": float(camera_z),
        "world_xyz_m": [float(world[0]), float(world[1]), float(world[2])],
        "cursor": cursor,
    }
    if sampled_depth is not None:
        prompt["depth_sample_m"] = float(sampled_depth)
        prompt["depth_sample_x"] = int(sampled_x)
        prompt["depth_sample_y"] = int(sampled_y)
    label = int(cursor.get("label", cursor.get("point_label", 1)))
    label = 1 if label > 0 else 0
    prompt["point_labels"] = [label]
    prompt["sam_point_coords"] = [[int(x), int(y)]]
    prompt["sam_point_labels"] = [label]
    return prompt


def main() -> None:
    parser = argparse.ArgumentParser(description="Project Quest cursor hit metadata to RGB prompt coordinates")
    parser.add_argument("--meta", type=Path, required=True)
    parser.add_argument("--cursor", type=Path, required=True)
    parser.add_argument("--depth", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--nearest-depth-radius-px", type=int, default=10)
    args = parser.parse_args()

    meta = json.loads(args.meta.read_text(encoding="utf-8"))
    cursor = json.loads(args.cursor.read_text(encoding="utf-8"))
    depth = np.load(args.depth) if args.depth is not None else None
    prompt = build_cursor_prompt(
        meta,
        cursor,
        depth,
        CursorPromptConfig(nearest_depth_radius_px=args.nearest_depth_radius_px),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(prompt, indent=2), encoding="utf-8")
    print(json.dumps({"valid": prompt.get("valid", False), "reason": prompt.get("reason")}), flush=True)


if __name__ == "__main__":
    main()
