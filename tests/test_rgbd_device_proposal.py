from __future__ import annotations

import numpy as np

from quest3server.tracking.rgbd_proposal import CursorRGBDDeviceProposer
from quest3server.tracking.types import TrackState, TrackingResult


def test_rgbd_proposal_prefers_cursor_depth_component_over_neighbor() -> None:
    h, w = 80, 100
    depth = np.full((h, w), 2.4, dtype=np.float32)
    depth[20:58, 12:42] = 1.0
    depth[22:58, 52:84] = 1.45
    sam_mask = np.zeros((h, w), dtype=bool)
    sam_mask[18:60, 10:86] = True

    proposer = CursorRGBDDeviceProposer(min_mask_area=20)
    proposal = proposer.propose(
        rgb_shape=(h, w),
        cursor_xy=(25, 35),
        sam_mask=sam_mask,
        aligned_depth_m=depth,
        rgb_intrinsics=np.array(
            [[80.0, 0.0, 50.0], [0.0, 80.0, 40.0], [0.0, 0.0, 1.0]],
            dtype=np.float32,
        ),
    )

    assert proposal.whole_mask[35, 25]
    assert not proposal.whole_mask[35, 65]
    assert proposal.depth_median_m is not None
    assert abs(proposal.depth_median_m - 1.0) < 0.08
    assert proposal.center_3d_m is not None
    assert proposal.source.startswith("rgbd_cursor_sam2")


def test_rgbd_proposal_falls_back_to_rgb_when_depth_missing() -> None:
    sam_mask = np.zeros((40, 40), dtype=bool)
    sam_mask[10:25, 11:24] = True
    proposal = CursorRGBDDeviceProposer().propose(
        rgb_shape=(40, 40),
        cursor_xy=(15, 15),
        sam_mask=sam_mask,
        aligned_depth_m=None,
        rgb_intrinsics=None,
    )

    assert proposal.whole_mask.sum() >= sam_mask.sum()
    assert proposal.source == "sam2_rgb_only"
    assert proposal.depth_confidence == 0.0


def test_tracking_result_payload_includes_extended_fields() -> None:
    result = TrackingResult(
        object_id=1,
        state=TrackState.TRACKING,
        label="lab device",
        score=0.7,
        box_xyxy=(1, 2, 10, 12),
        center_pixel=(5.0, 6.0),
        mask_rle={"size": [2, 2], "counts": [0, 4]},
        mask_area=4,
        center_3d_m=(0.1, 0.2, 1.3),
        depth_median_m=1.3,
        depth_confidence=0.8,
        segmentation_source="rgbd_cursor_sam2",
        segmentation_confidence=0.9,
        parts=[{"part_id": 1, "kind": "indicator_light"}],
        visual_evidence={"provider": "local"},
    )

    payload = result.to_payload()

    assert payload["mask_rle"]["counts"] == [0, 4]
    assert payload["center_3d_m"] == [0.1, 0.2, 1.3]
    assert payload["depth_median_m"] == 1.3
    assert payload["segmentation_source"] == "rgbd_cursor_sam2"
    assert payload["parts"][0]["kind"] == "indicator_light"
