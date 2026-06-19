from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "viewer"))

from quest3_rgbd_align_viewer import FrameData, run_device_segmentation


class _StubSegmenter:
    ready = True

    def __init__(self) -> None:
        self.reset_calls = 0

    def reset_for_image(self, rgb: np.ndarray) -> None:
        self.reset_calls += 1

    def re_predict(self, point_coords: np.ndarray, point_labels: np.ndarray, box=None) -> np.ndarray:
        assert point_coords.shape == (1, 2)
        assert point_labels.tolist() == [1]
        mask = np.zeros((80, 100), dtype=bool)
        mask[25:55, 35:65] = True
        return mask


def test_run_device_segmentation_supports_world_anchors_without_cursor(tmp_path: Path) -> None:
    frame = FrameData(
        frame_dir=tmp_path,
        meta={
            "rgb": {
                "resolution_w": 100,
                "resolution_h": 80,
                "focal_length_x": 100.0,
                "focal_length_y": 100.0,
                "principal_point_x": 50.0,
                "principal_point_y": 40.0,
                "pose_position_x": 0.0,
                "pose_position_y": 0.0,
                "pose_position_z": 0.0,
                "pose_rotation_x": 0.0,
                "pose_rotation_y": 0.0,
                "pose_rotation_z": 0.0,
                "pose_rotation_w": 1.0,
            },
            "depth": {"resolution_w": 10, "resolution_h": 10},
        },
        rgb=np.zeros((80, 100, 3), dtype=np.uint8),
        depth_ndc=np.zeros((10, 10), dtype=np.float32),
        depth_m=np.zeros((10, 10), dtype=np.float32),
        aligned_depth=np.full((80, 100), 2.0, dtype=np.float32),
        overlay_rgb=np.zeros((80, 100, 3), dtype=np.uint8),
        any2full_depth=None,
        any2full_overlay_rgb=None,
        any2full_path=None,
        device_mask=None,
        device_overlay_rgb=None,
        device_mask_path=None,
        device_info=None,
        device_contour_3d=None,
        cloud_points=np.zeros((0, 3), dtype=np.float32),
        cloud_colors=np.zeros((0, 3), dtype=np.uint8),
        projected_depth_count=0,
        any2full_depth_count=0,
        alignment_mode="sdk_reprojection",
    )
    args = SimpleNamespace(
        disable_device_segmentation=False,
        segment_cache_dir=tmp_path,
        cursor_nearest_depth_radius_px=1,
        seg_depth_local_jump_m=0.06,
        seg_depth_local_jump_rel=0.05,
        seg_depth_global_span_m=0.55,
        seg_depth_max_radius_px=170,
        seg_depth_bbox_pad_px=10,
        seg_depth_max_component_area_ratio=0.08,
        seg_depth_ignore_texture_edges=False,
        seg_refine_depth_span_m=1.20,
        seg_enable_depth_component_union=False,
        seg_refine_open_px=2,
        seg_refine_close_px=5,
    )
    anchors = [{"x": 0.0, "y": 0.0, "z": 2.0, "label": 1}]

    out = run_device_segmentation(frame, args, _StubSegmenter(), anchors=anchors, re_predict=False)

    assert out.device_mask is not None
    assert out.device_overlay_rgb is not None
    assert out.device_info is not None
    assert out.device_info["anchors_used"] == 1
    assert out.device_info["contour_3d_points"] > 0
    assert out.device_mask_path is not None
