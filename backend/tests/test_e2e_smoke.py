from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import urllib.request
from websocket import create_connection

from quest3server.main import app, get_vision_runtime, set_vision_runtime_for_testing
from quest3server.vision.pipeline import VisionPipelineError
from quest3server.vision.rle import encode_binary_mask
from quest3server.vision.runtime import Quest3VisionRuntime
from quest3server.vision.types import GpuMemoryStats, TrackedMask, VisionFrameResult

BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent


class _FakeFullPipeline:
    def __init__(self) -> None:
        self.is_active = False
        self.prompt: str | None = None
        self._frames: list[int] = []
        self.source = "fake-full-pipeline"
        self.detect_interval = 30
        self.max_objects = 8

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
        self._frames.append(frame_id)
        height, width = image_bgr.shape[:2]
        mask = np.zeros((height, width), dtype=bool)
        mask[height // 4 : 3 * height // 4, width // 4 : 3 * width // 4] = True
        return VisionFrameResult(
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
            frame_width=width,
            frame_height=height,
            prompt=self.prompt,
            source=self.source,
            mode="detection",
            process_time_ms=18.25,
            gpu_memory_mb=GpuMemoryStats(allocated=128.0, max_allocated=256.0),
            objects=[
                TrackedMask(
                    object_id=1,
                    label=self.prompt,
                    score=0.99,
                    box_xyxy=(width // 4, height // 4, 3 * width // 4, 3 * height // 4),
                    area=int(mask.sum()),
                    mask_rle=encode_binary_mask(mask),
                )
            ],
        )


def _decode_rle_mask(mask_rle: dict[str, Any]) -> np.ndarray:
    size = mask_rle["size"]
    counts = mask_rle["counts"]
    height, width = int(size[0]), int(size[1])
    total = height * width
    values = np.zeros(total, dtype=np.uint8)
    write_index = 0
    current = 0
    for run_length in counts:
        if run_length < 0:
            raise ValueError("negative run length")
        if current != 0:
            values[write_index : write_index + run_length] = current
        write_index += run_length
        current = 0 if current == 1 else 1
    if write_index != total:
        raise ValueError("run lengths do not cover mask size")
    return values.reshape((height, width))


def _sample_mask_pixels(
    mask_rle: dict[str, Any],
    sample_count: int,
    frame_width: int | None = None,
    frame_height: int | None = None,
) -> list[tuple[int, int, float, float]]:
    size = mask_rle["size"]
    counts = mask_rle["counts"]
    mask_height, mask_width = int(size[0]), int(size[1])
    fw = frame_width if frame_width and frame_width > 0 else mask_width
    fh = frame_height if frame_height and frame_height > 0 else mask_height

    total_foreground = sum(counts[i] for i in range(1, len(counts), 2))
    if total_foreground <= 0:
        return []

    n = min(sample_count, total_foreground)
    targets = []
    if n == 1:
        targets = [total_foreground // 2]
    else:
        for i in range(n):
            t = round((i / (n - 1)) * (total_foreground - 1))
            targets.append(max(0, min(t, total_foreground - 1)))

    samples: list[tuple[int, int, float, float]] = []
    target_cursor = 0
    seen_fg = 0
    flat_index = 0
    is_fg = False

    for run_length in counts:
        if run_length < 0:
            return []
        if not is_fg:
            flat_index += run_length
            is_fg = True
            continue
        run_start = seen_fg
        run_end = seen_fg + run_length
        while target_cursor < len(targets) and targets[target_cursor] < run_end:
            ordinal_in_run = targets[target_cursor] - run_start
            pixel_index = flat_index + ordinal_in_run
            py = pixel_index // mask_width
            px = pixel_index % mask_width
            vu = (px + 0.5) / fw
            vv = 1.0 - ((py + 0.5) / fh)
            samples.append((px, py, vu, vv))
            target_cursor += 1
        seen_fg = run_end
        flat_index += run_length
        is_fg = False

    return samples


def _simulate_world_point(
    vu: float,
    vv: float,
    depth_m: float,
    fx: float = 500.0,
    fy: float = 500.0,
    cx: float = 320.0,
    cy: float = 180.0,
) -> tuple[float, float, float]:
    x = (vu * cx * 2 - cx) * depth_m / fx
    y = ((1.0 - vv) * cy * 2 - cy) * depth_m / fy
    z = depth_m
    return (x, y, z)


def _make_rgb_packet(
    *,
    frame_id: int,
    timestamp_ms: int,
    width: int = 640,
    height: int = 360,
) -> bytes:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    jpeg = encoded.tobytes()
    header = struct.pack("<4sI q I I I", b"RGB1", frame_id, timestamp_ms, width, height, len(jpeg))
    return header + jpeg


def _make_depth_packet(
    *,
    frame_id: int,
    timestamp_ms: int,
    width: int = 640,
    height: int = 360,
) -> bytes:
    floats = np.zeros(height * width, dtype=np.float32).tobytes()
    header = struct.pack(
        "<4sI q I I I I I",
        b"DEP1",
        frame_id,
        timestamp_ms,
        width,
        height,
        width,
        1,
        len(floats),
    )
    return header + floats


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server_ready(base_url: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=0.5) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    raise AssertionError(f"quest3server did not become ready: {last_error}")


def _run_pipeline_and_get_vision_payload(client, rgb_ws, vision_ws, pipeline) -> dict[str, Any]:
    pipeline.start_session("chair")
    client.post("/api/vision/session", json={"prompt": "chair"})
    rgb_ws.send_bytes(_make_rgb_packet(frame_id=42, timestamp_ms=9999))
    payload = json.loads(vision_ws.receive_text())
    pipeline.stop_session()
    client.delete("/api/vision/session")
    return payload


class TestE2ESmoke:
    def test_full_pipeline_rgb_to_vision_mask_validated(self) -> None:
        original = get_vision_runtime()
        pipeline = _FakeFullPipeline()
        set_vision_runtime_for_testing(Quest3VisionRuntime(pipeline))

        try:
            from fastapi.testclient import TestClient

            with TestClient(app) as client:
                with client.websocket_connect("/ws/rgb") as rgb_ws, \
                     client.websocket_connect("/ws/vision") as vision_ws:
                    payload = _run_pipeline_and_get_vision_payload(client, rgb_ws, vision_ws, pipeline)

                assert payload["frame_id"] == 42
                assert payload["timestamp_ms"] == 9999
                assert payload["frame_width"] == 640
                assert payload["frame_height"] == 360
                assert payload["prompt"] == "chair"
                assert payload["source"] == "fake-full-pipeline"
                assert payload["mode"] == "detection"
                assert payload["process_time_ms"] == 18.25
                assert payload["gpu_memory_mb"] == {
                    "allocated": 128.0,
                    "max_allocated": 256.0,
                }
                assert len(payload["objects"]) == 1

                obj = payload["objects"][0]
                assert obj["object_id"] == 1
                assert obj["label"] == "chair"
                assert obj["score"] == 0.99
                assert obj["area"] > 0

                mask_rle = obj["mask_rle"]
                assert mask_rle["size"] == [360, 640]
                assert len(mask_rle["counts"]) >= 2

                decoded = _decode_rle_mask(mask_rle)
                assert decoded.shape == (360, 640)
                assert decoded[180, 320] == 1
                assert decoded[0, 0] == 0

                samples = _sample_mask_pixels(mask_rle, sample_count=3)
                assert len(samples) == 3
                for px, py, vu, vv in samples:
                    assert 0.0 <= vu <= 1.0
                    assert 0.0 <= vv <= 1.0
                    assert decoded[py, px] == 1
        finally:
            set_vision_runtime_for_testing(original)

    def test_camera_intrinsics_via_heartbeat_and_api(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            with client.websocket_connect("/ws/heartbeat") as hb_ws:
                intrinsics = {
                    "type": "camera_intrinsics",
                    "fx": 600.0,
                    "fy": 600.0,
                    "cx": 320.0,
                    "cy": 180.0,
                    "projection_matrix": [1.0] * 16,
                }
                hb_ws.send_text(json.dumps(intrinsics))
                ack = hb_ws.receive_text()
                assert ack == "ack"

            status = client.get("/api/status")
            assert status.status_code == 200
            body = status.json()
            ci = body["camera_intrinsics"]
            assert ci["fx"] == 600.0
            assert ci["fy"] == 600.0
            assert ci["cx"] == 320.0
            assert ci["cy"] == 180.0
            assert ci["projection_matrix"] == [1.0] * 16
            assert "vision_metrics" in body
            assert "vision_latest" in body

            vision = client.get("/api/vision")
            assert vision.status_code == 200
            ci2 = vision.json()["camera_intrinsics"]
            assert ci2["fx"] == 600.0

    def test_depth_frame_ingestion(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            with client.websocket_connect("/ws/depth") as depth_ws:
                depth_ws.send_bytes(_make_depth_packet(frame_id=10, timestamp_ms=5555))

            status = client.get("/api/status")
            body = status.json()
            assert body["last_depth_frame_id"] == 10
            assert body["last_depth_size"] == "640x360"
            assert body["last_seen_utc"] is not None

    def test_heartbeat_client_log_forwarding(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            with client.websocket_connect("/ws/heartbeat") as hb_ws:
                log_msg = {
                    "type": "client_log",
                    "source": "unity",
                    "level": "INFO",
                    "message": "Vision object_id=1 label=chair hits=3/3",
                    "timestamp_ms": 1234567890,
                }
                hb_ws.send_text(json.dumps(log_msg))
                ack = hb_ws.receive_text()
                assert ack == "ack"

            logs_resp = client.get("/api/logs", params={"limit": 10})
            assert logs_resp.status_code == 200
            logs_body = logs_resp.json()
            log_messages = [entry["message"] for entry in logs_body["logs"]]
            assert any("Vision object_id=1" in msg for msg in log_messages)

    def test_mask_rle_roundtrip(self) -> None:
        mask = np.zeros((360, 640), dtype=bool)
        mask[90:270, 160:480] = True
        rle = encode_binary_mask(mask)
        decoded = _decode_rle_mask(rle)
        assert np.array_equal(mask, decoded)

    def test_viewport_to_world_simulation(self) -> None:
        mask = np.zeros((360, 640), dtype=bool)
        mask[90:270, 160:480] = True
        rle = encode_binary_mask(mask)
        samples = _sample_mask_pixels(rle, sample_count=5, frame_width=640, frame_height=360)

        assert len(samples) == 5

        depth_map = np.full((360, 640), 2.5, dtype=np.float32)
        for px, py, vu, vv in samples:
            depth_m = float(depth_map[py, px])
            wx, wy, wz = _simulate_world_point(vu, vv, depth_m)
            assert abs(wx) < 10.0
            assert abs(wy) < 10.0
            assert wz == depth_m

    def test_app_health_and_status(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            health = client.get("/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

            status = client.get("/api/status")
            assert status.status_code == 200
            body = status.json()
            assert "connected" in body
            assert "vision" in body
            assert "camera_intrinsics" in body

            latest_rgb = client.get("/api/latest-rgb")
            assert latest_rgb.status_code in (200, 204)

    def test_real_quest3server_process_streams_vision_fields(self) -> None:
        port = _find_free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        pythonpath_parts = [str(BACKEND_DIR), str(TESTS_DIR)]
        existing_pythonpath = env.get("PYTHONPATH", "").strip()
        if existing_pythonpath:
            pythonpath_parts.append(existing_pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(
            pythonpath_parts
        )
        env["QUEST3_VISION_PIPELINE_FACTORY"] = (
            "quest3server_test_pipeline:create_pipeline"
        )

        process = subprocess.Popen(
            [
                "uv",
                "run",
                "python",
                "-m",
                "uvicorn",
                "quest3server.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=str(BACKEND_DIR),
            env=env,
        )

        try:
            _wait_for_server_ready(base_url)
        except Exception:
            process.terminate()
            process.wait(timeout=5)
            raise

        try:
            payload = json.dumps({"prompt": "chair"}).encode("utf-8")
            request = urllib.request.Request(
                f"{base_url}/api/vision/session",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=2.0) as response:
                started = json.loads(response.read().decode("utf-8"))
            assert started["active"] is True
            assert started["prompt"] == "chair"

            vision_ws = create_connection(f"ws://127.0.0.1:{port}/ws/vision", timeout=2.0)
            rgb_ws = create_connection(f"ws://127.0.0.1:{port}/ws/rgb", timeout=2.0)
            try:
                rgb_ws.send_binary(_make_rgb_packet(frame_id=77, timestamp_ms=4567))
                message = vision_ws.recv()
            finally:
                rgb_ws.close()
                vision_ws.close()

            vision_payload = json.loads(message)
            assert vision_payload["frame_id"] == 77
            assert vision_payload["mode"] == "detection"
            assert vision_payload["process_time_ms"] == 7.25
            assert vision_payload["gpu_memory_mb"] == {
                "allocated": 12.0,
                "max_allocated": 24.0,
            }
            assert vision_payload["objects"][0]["label"] == "chair"
        finally:
            process.terminate()
            process.wait(timeout=5)
