from __future__ import annotations

import sys
import types

import numpy as np

from quest3server.vision.providers import Sam2VideoRepropagatingTracker
from quest3server.vision.types import DetectionCandidate


class _FakeSam2VideoPredictor:
    last_instance: "_FakeSam2VideoPredictor | None" = None

    def __init__(self) -> None:
        self.init_state_calls: list[dict[str, object]] = []
        self.add_calls: list[tuple[int, int]] = []
        self.propagate_calls: list[tuple[int | None, int | None, int]] = []
        self.object_ids: list[int] = []

    @classmethod
    def from_pretrained(cls, model_name: str) -> "_FakeSam2VideoPredictor":
        instance = cls()
        instance.model_name = model_name
        cls.last_instance = instance
        return instance

    def init_state(self, video_path, frame_load_config=None):
        self.init_state_calls.append(
            {
                "video_path": video_path,
                "frame_load_config": frame_load_config,
            }
        )
        return {
            "video_path": video_path,
            "frame_load_config": frame_load_config,
        }

    def add_new_points_or_box(self, state, frame_idx: int, obj_id: int, box):
        self.add_calls.append((frame_idx, obj_id))
        if obj_id not in self.object_ids:
            self.object_ids.append(obj_id)
        return frame_idx, [obj_id], self._mask_batch([obj_id])

    def propagate_in_video(
        self,
        state,
        start_frame_idx: int | None = None,
        max_frame_num_to_track: int | None = None,
    ):
        frame_idx = 0 if start_frame_idx is None else start_frame_idx
        max_frames = int(state["frame_load_config"]["max_frames"])
        self.propagate_calls.append((start_frame_idx, max_frame_num_to_track, max_frames))
        yield frame_idx, list(self.object_ids), self._mask_batch(self.object_ids)

    @staticmethod
    def _mask_batch(object_ids: list[int]) -> np.ndarray:
        masks: list[np.ndarray] = []
        for offset, object_id in enumerate(object_ids):
            mask = np.zeros((1, 4, 4), dtype=np.float32)
            mask[0, 1 : 3 + (offset % 2), 1:3] = float(object_id)
            masks.append(mask)
        return np.asarray(masks, dtype=np.float32)


def test_sam2_tracker_keeps_state_and_adds_objects_incrementally(monkeypatch) -> None:
    fake_package = types.ModuleType("sam2")
    fake_video_module = types.ModuleType("sam2.sam2_video_predictor")
    fake_video_module.SAM2VideoPredictor = _FakeSam2VideoPredictor
    fake_pil_module = types.ModuleType("PIL")
    fake_pil_image_module = types.ModuleType("PIL.Image")

    class _FakeImage:
        @staticmethod
        def fromarray(array):
            return array

    fake_pil_image_module.fromarray = _FakeImage.fromarray
    fake_pil_module.Image = fake_pil_image_module
    monkeypatch.setitem(sys.modules, "sam2", fake_package)
    monkeypatch.setitem(sys.modules, "sam2.sam2_video_predictor", fake_video_module)
    monkeypatch.setitem(sys.modules, "PIL", fake_pil_module)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_pil_image_module)

    tracker = Sam2VideoRepropagatingTracker(model_name="fake-sam2.1")
    predictor = _FakeSam2VideoPredictor.last_instance
    assert predictor is not None

    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    initial_detection = DetectionCandidate(
        object_id=1,
        label="chair",
        score=0.9,
        box_xyxy=(1, 1, 3, 3),
    )
    initial_mask = np.zeros((4, 4), dtype=bool)
    initial_mask[1:3, 1:3] = True

    tracker.bootstrap(frame, [initial_detection], [initial_mask])
    first_tracked = tracker.track(frame)

    added_detection = DetectionCandidate(
        object_id=2,
        label="table",
        score=0.8,
        box_xyxy=(0, 0, 2, 2),
    )
    added_mask = np.zeros((4, 4), dtype=bool)
    added_mask[0:2, 0:2] = True
    added = tracker.add_objects([added_detection], [added_mask])
    second_tracked = tracker.track(frame)

    assert [object_id for object_id, _ in first_tracked] == [1]
    assert [object_id for object_id, _ in added] == [2]
    assert [object_id for object_id, _ in second_tracked] == [1, 2]

    assert len(predictor.init_state_calls) == 1
    assert predictor.add_calls == [(0, 1), (1, 2)]
    assert predictor.propagate_calls == [(1, 1, 2), (2, 1, 3)]
