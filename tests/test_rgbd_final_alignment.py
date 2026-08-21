from __future__ import annotations

import numpy as np

from quest3server.tracking.rgbd_final_alignment import (
    align_final_rgbd_payload,
    raw_depth_to_linear_m,
    rgb_intrinsics_from_meta,
)


def _synthetic_meta() -> dict:
    return {
        "rgb": {
            "resolution_w": 4,
            "resolution_h": 4,
            "focal_length_x": 1.0,
            "focal_length_y": 1.0,
            "principal_point_x": 1.5,
            "principal_point_y": 1.5,
            "pose_position_x": 0.0,
            "pose_position_y": 0.0,
            "pose_position_z": 0.0,
            "pose_rotation_x": 0.0,
            "pose_rotation_y": 0.0,
            "pose_rotation_z": 0.0,
            "pose_rotation_w": 1.0,
            "camera_position": "Left",
        },
        "depth": {
            "resolution_w": 2,
            "resolution_h": 2,
            "zbuffer_x": 1.0,
            "zbuffer_y": 0.0,
            "fov_left": 1.0,
            "fov_right": 1.0,
            "fov_top": 1.0,
            "fov_bottom": 1.0,
            "near_z": 0.1,
            "far_z": 10.0,
            "pose_position_x": 0.0,
            "pose_position_y": 0.0,
            "pose_position_z": 0.0,
            "pose_rotation_x": 0.0,
            "pose_rotation_y": 0.0,
            "pose_rotation_z": 0.0,
            "pose_rotation_w": 1.0,
            "selected_eye": 0,
            "descriptor_reprojection_matrix": [
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
            ],
        },
    }


def test_raw_depth_to_linear_matches_final_script_formula() -> None:
    depth_meta = {"zbuffer_x": 2.0, "zbuffer_y": 1.0}
    raw = np.array([[0.5, 1.0]], dtype=np.float32)

    depth_m = raw_depth_to_linear_m(raw, depth_meta)

    np.testing.assert_allclose(depth_m, np.array([[2.0, 1.0]], dtype=np.float32))


def test_align_final_rgbd_payload_projects_depth_into_rgb_frame() -> None:
    meta = _synthetic_meta()
    rgb_bgr = np.zeros((4, 4, 3), dtype=np.uint8)
    raw_depth = np.ones((2, 2), dtype=np.float32)

    alignment = align_final_rgbd_payload(
        rgb_bgr=rgb_bgr,
        raw_depth=raw_depth,
        meta=meta,
        min_depth=0.2,
        max_depth=8.0,
    )

    assert alignment.aligned_depth_m.shape == (4, 4)
    assert alignment.valid_mask.sum() == 4
    assert alignment.summary["source"] == "quest3_rgbd_capture_final"
    assert alignment.summary["projected_pixels"] == 4
    assert alignment.point_cloud_rgb_camera_m.shape == (4, 3)
    np.testing.assert_allclose(alignment.aligned_depth_m[1:3, 1:3], 1.0)


def test_rgb_intrinsics_from_final_meta() -> None:
    K = rgb_intrinsics_from_meta(_synthetic_meta()["rgb"])

    np.testing.assert_allclose(
        K,
        np.array([[1.0, 0.0, 1.5], [0.0, 1.0, 1.5], [0.0, 0.0, 1.0]], dtype=np.float32),
    )
