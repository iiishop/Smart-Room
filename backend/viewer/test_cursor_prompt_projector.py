import unittest

import numpy as np

from cursor_prompt_projector import build_cursor_prompt, project_rgb_camera_point


def _meta() -> dict:
    return {
        "rgb": {
            "resolution_w": 1280,
            "resolution_h": 1280,
            "focal_length_x": 800.0,
            "focal_length_y": 800.0,
            "principal_point_x": 640.0,
            "principal_point_y": 640.0,
            "pose_position_x": 0.0,
            "pose_position_y": 0.0,
            "pose_position_z": 0.0,
            "pose_rotation_x": 0.0,
            "pose_rotation_y": 0.0,
            "pose_rotation_z": 0.0,
            "pose_rotation_w": 1.0,
        }
    }


class CursorPromptProjectorTests(unittest.TestCase):
    def test_image_rows_use_top_left_origin(self) -> None:
        center = project_rgb_camera_point(np.array([0.0, 0.0, 2.0]), _meta()["rgb"])
        above = project_rgb_camera_point(np.array([0.0, 0.5, 2.0]), _meta()["rgb"])
        below = project_rgb_camera_point(np.array([0.0, -0.5, 2.0]), _meta()["rgb"])

        self.assertEqual(center[:2], (640, 639))
        self.assertLess(above[1], center[1])
        self.assertGreater(below[1], center[1])

    def test_identity_pose_world_point_projects_to_expected_pixel(self) -> None:
        cursor = {
            "is_hitting": True,
            "hit_world_x": 0.25,
            "hit_world_y": 0.5,
            "hit_world_z": 2.0,
            "label": 0,
        }

        prompt = build_cursor_prompt(_meta(), cursor)

        self.assertTrue(prompt["valid"])
        self.assertEqual(prompt["rgb_x"], 740)
        self.assertEqual(prompt["rgb_y"], 439)
        self.assertEqual(prompt["sam_point_labels"], [0])


if __name__ == "__main__":
    unittest.main()
