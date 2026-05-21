from __future__ import annotations

import numpy as np

from quest3server.vision.pipeline import GroundedSamTrackingPipeline
from quest3server.vision.types import DetectionCandidate


class _FakeDetector:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._call_count = 0

    def detect(self, image_bgr: np.ndarray, prompt: str) -> list[DetectionCandidate]:
        self.calls.append(prompt)
        self._call_count += 1
        if self._call_count == 1:
            return [
                DetectionCandidate(
                    object_id=101,
                    label="chair",
                    score=0.92,
                    box_xyxy=(1, 1, 2, 2),
                )
            ]
        return [
            DetectionCandidate(
                object_id=101,
                label="chair",
                score=0.92,
                box_xyxy=(1, 1, 2, 2),
            ),
            DetectionCandidate(
                object_id=202,
                label="table",
                score=0.81,
                box_xyxy=(0, 0, 1, 1),
            ),
        ]


class _StaticDetector:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def detect(self, image_bgr: np.ndarray, prompt: str) -> list[DetectionCandidate]:
        self.calls.append(prompt)
        return [
            DetectionCandidate(
                object_id=101,
                label="chair",
                score=0.92,
                box_xyxy=(1, 1, 2, 2),
            )
        ]


class _FakeSegmenter:
    def segment(
        self, image_bgr: np.ndarray, detections: list[DetectionCandidate]
    ) -> list[np.ndarray]:
        return [np.array([[0, 0, 0], [0, 1, 1], [0, 1, 1]], dtype=bool)]


class _FakeTracker:
    def __init__(self) -> None:
        self.reset_calls = 0
        self.bootstrap_calls = 0
        self.track_calls = 0
        self.add_object_calls = 0

    def reset(self) -> None:
        self.reset_calls += 1

    def bootstrap(
        self,
        image_bgr: np.ndarray,
        detections: list[DetectionCandidate],
        masks: list[np.ndarray],
    ) -> list[np.ndarray]:
        self.bootstrap_calls += 1
        return masks

    def track(self, image_bgr: np.ndarray) -> list[tuple[int, np.ndarray]]:
        self.track_calls += 1
        return [
            (
                1,
                np.array([[0, 0, 0], [0, 1, 0], [0, 1, 1]], dtype=bool),
            )
        ]

    def add_objects(
        self,
        detections: list[DetectionCandidate],
        masks: list[np.ndarray],
    ) -> list[tuple[int, np.ndarray]]:
        self.add_object_calls += 1
        return [(detection.object_id, mask) for detection, mask in zip(detections, masks, strict=True)]


def test_grounded_pipeline_seeds_then_tracks() -> None:
    detector = _StaticDetector()
    tracker = _FakeTracker()
    pipeline = GroundedSamTrackingPipeline(
        detector=detector,
        segmenter=_FakeSegmenter(),
        tracker=tracker,
        max_objects=4,
    )
    frame = np.zeros((3, 3, 3), dtype=np.uint8)

    pipeline.start_session("chair")

    first = pipeline.process_frame(
        frame_id=10,
        timestamp_ms=1000,
        image_bgr=frame,
    )
    second = pipeline.process_frame(
        frame_id=11,
        timestamp_ms=1033,
        image_bgr=frame,
    )

    assert first is not None
    assert first.prompt == "chair"
    assert first.objects[0].object_id == 1
    assert first.objects[0].label == "chair"
    assert first.objects[0].box_xyxy == (1, 1, 2, 2)
    assert first.objects[0].area == 4
    assert first.objects[0].mask_rle["size"] == [3, 3]

    assert second is not None
    assert second.frame_id == 11
    assert second.objects[0].area == 3
    assert first.mode == "detection"
    assert second.mode == "propagation"
    assert first.process_time_ms is not None
    assert second.process_time_ms is not None

    assert detector.calls == ["chair"]
    assert tracker.bootstrap_calls == 1
    assert tracker.track_calls == 1
    assert tracker.add_object_calls == 0


def test_grounded_pipeline_adds_new_objects_without_resetting_ids() -> None:
    detector = _FakeDetector()
    tracker = _FakeTracker()
    pipeline = GroundedSamTrackingPipeline(
        detector=detector,
        segmenter=_FakeSegmenter(),
        tracker=tracker,
        detect_interval=11,
        max_objects=4,
    )
    frame = np.zeros((3, 3, 3), dtype=np.uint8)

    pipeline.start_session("chair")
    first = pipeline.process_frame(
        frame_id=10,
        timestamp_ms=1000,
        image_bgr=frame,
    )
    second = pipeline.process_frame(
        frame_id=11,
        timestamp_ms=1033,
        image_bgr=frame,
    )

    assert first is not None
    assert [item.object_id for item in first.objects] == [1]

    assert second is not None
    assert [item.object_id for item in second.objects] == [1, 2]
    assert [item.label for item in second.objects] == ["chair", "table"]
    assert second.mode == "detection"
    assert tracker.reset_calls == 1
    assert tracker.bootstrap_calls == 1
    assert tracker.track_calls == 1
    assert tracker.add_object_calls == 1
    assert detector.calls == ["chair", "chair"]


def test_grounded_pipeline_respects_detect_interval_and_forces_first_frame() -> None:
    detector = _StaticDetector()
    tracker = _FakeTracker()
    pipeline = GroundedSamTrackingPipeline(
        detector=detector,
        segmenter=_FakeSegmenter(),
        tracker=tracker,
        detect_interval=3,
        max_objects=4,
    )
    frame = np.zeros((3, 3, 3), dtype=np.uint8)

    pipeline.start_session("chair")

    first = pipeline.process_frame(frame_id=1, timestamp_ms=1000, image_bgr=frame)
    second = pipeline.process_frame(frame_id=2, timestamp_ms=1033, image_bgr=frame)
    third = pipeline.process_frame(frame_id=3, timestamp_ms=1066, image_bgr=frame)
    fourth = pipeline.process_frame(frame_id=4, timestamp_ms=1099, image_bgr=frame)

    assert first is not None
    assert second is not None
    assert third is not None
    assert fourth is not None
    assert [first.mode, second.mode, third.mode, fourth.mode] == [
        "detection",
        "propagation",
        "detection",
        "propagation",
    ]
    assert detector.calls == ["chair", "chair"]
    assert tracker.bootstrap_calls == 1
    assert tracker.track_calls == 3
    assert tracker.add_object_calls == 0
