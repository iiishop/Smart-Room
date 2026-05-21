from __future__ import annotations

import json
import struct

import cv2
import numpy as np
from fastapi.testclient import TestClient

from quest3server.main import app, get_vision_runtime, set_vision_runtime_for_testing
from quest3server.vision.pipeline import VisionPipelineError
from quest3server.vision.runtime import Quest3VisionRuntime
from quest3server.vision.types import GpuMemoryStats, TrackedMask, VisionFrameResult


class _FakePipeline:
    def __init__(self) -> None:
        self.is_active = False
        self.prompt: str | None = None
        self.frames: list[int] = []

    def start_session(self, prompt: str) -> None:
        normalized = prompt.strip()
        if not normalized:
            raise VisionPipelineError("prompt must not be empty")
        self.prompt = normalized
        self.is_active = True

    def stop_session(self) -> None:
        self.prompt = None
        self.is_active = False

    def process_frame(
        self,
        *,
        frame_id: int,
        timestamp_ms: int,
        image_bgr: np.ndarray,
    ) -> VisionFrameResult | None:
        if not self.is_active or self.prompt is None:
            return None
        self.frames.append(frame_id)
        return VisionFrameResult(
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
            frame_width=int(image_bgr.shape[1]),
            frame_height=int(image_bgr.shape[0]),
            prompt=self.prompt,
            source="fake-vision",
            mode="detection" if len(self.frames) == 1 else "propagation",
            process_time_ms=12.5,
            gpu_memory_mb=GpuMemoryStats(allocated=24.0, max_allocated=48.0),
            objects=[
                TrackedMask(
                    object_id=1,
                    label=self.prompt,
                    score=0.99,
                    box_xyxy=(0, 0, 1, 1),
                    area=4,
                    mask_rle={"size": [2, 2], "counts": [0, 4]},
                )
            ],
        )


def _make_rgb_packet(*, frame_id: int, timestamp_ms: int) -> bytes:
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    image[0, 0] = (0, 255, 0)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    jpeg = encoded.tobytes()
    header = struct.pack(
        "<4sI q I I I",
        b"RGB1",
        frame_id,
        timestamp_ms,
        2,
        2,
        len(jpeg),
    )
    return header + jpeg


def test_vision_session_and_streaming_api() -> None:
    original_runtime = get_vision_runtime()
    fake_pipeline = _FakePipeline()
    runtime = Quest3VisionRuntime(fake_pipeline)
    set_vision_runtime_for_testing(runtime)

    try:
        with TestClient(app) as client:
            initial = client.get("/api/vision")
            assert initial.status_code == 200
            assert initial.json()["available"] is True
            assert initial.json()["active"] is False

            started = client.post("/api/vision/session", json={"prompt": "chair"})
            assert started.status_code == 200
            assert started.json()["active"] is True
            assert started.json()["prompt"] == "chair"

            with client.websocket_connect("/ws/rgb") as rgb_ws:
                rgb_ws.send_bytes(_make_rgb_packet(frame_id=7, timestamp_ms=1234))

            with client.websocket_connect("/ws/vision") as vision_ws:
                payload = json.loads(vision_ws.receive_text())
                assert payload["frame_id"] == 7
                assert payload["prompt"] == "chair"
                assert payload["source"] == "fake-vision"
                assert payload["mode"] == "detection"
                assert payload["process_time_ms"] == 12.5
                assert payload["gpu_memory_mb"] == {
                    "allocated": 24.0,
                    "max_allocated": 48.0,
                }
                assert payload["objects"][0]["label"] == "chair"

            status = client.get("/api/status")
            assert status.status_code == 200
            body = status.json()
            assert body["last_rgb_frame_id"] == 7
            assert body["last_rgb_size"] == "2x2"
            assert body["vision"]["latest"]["frame_id"] == 7
            assert body["vision"]["latest"]["mode"] == "detection"

            latest_rgb = client.get("/api/latest-rgb")
            assert latest_rgb.status_code == 200
            assert latest_rgb.headers["content-type"] == "image/jpeg"
            assert fake_pipeline.frames == [7]

            stopped = client.delete("/api/vision/session")
            assert stopped.status_code == 200
            assert stopped.json()["active"] is False
    finally:
        set_vision_runtime_for_testing(original_runtime)


def test_vision_start_returns_503_when_pipeline_unavailable() -> None:
    original_runtime = get_vision_runtime()
    set_vision_runtime_for_testing(Quest3VisionRuntime(None))

    try:
        with TestClient(app) as client:
            response = client.post("/api/vision/session", json={"prompt": "chair"})
            assert response.status_code == 503
            assert "unavailable" in response.json()["detail"].lower()
    finally:
        set_vision_runtime_for_testing(original_runtime)
