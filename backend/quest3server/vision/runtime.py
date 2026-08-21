from __future__ import annotations

import asyncio
import json

import cv2
import numpy as np
import torch
from fastapi import WebSocket

from ..log_manager import add_python_log
from .pipeline import GroundedSamTrackingPipeline, VisionPipelineError
from .providers import DEFAULT_VISION_PROMPT
from .types import VisionFrameResult


class Quest3VisionRuntime:
    def __init__(self, pipeline: GroundedSamTrackingPipeline | None) -> None:
        self._pipeline = pipeline
        self._latest_result: VisionFrameResult | None = None
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._processed_frames = 0
        self._dropped_frames = 0
        self._last_error: str | None = None

    @property
    def is_available(self) -> bool:
        return self._pipeline is not None

    @property
    def is_active(self) -> bool:
        return self.is_available and self._pipeline.is_active

    def snapshot(self) -> dict:
        latest = None if self._latest_result is None else self._latest_result.to_payload()
        return {
            "available": self.is_available,
            "active": self.is_active,
            "prompt": None if self._pipeline is None else self._pipeline.prompt,
            "source": self._pipeline_value("source"),
            "detect_interval": self._pipeline_value("detect_interval"),
            "max_objects": self._pipeline_value("max_objects"),
            "processed_frames": self._processed_frames,
            "dropped_frames": self._dropped_frames,
            "last_error": self._last_error,
            "metrics": self._metrics_snapshot(),
            "latest": latest,
        }

    def start_session(self, prompt: str) -> dict:
        pipeline = self._require_pipeline()
        pipeline.start_session(prompt)
        self._latest_result = None
        self._last_error = None
        return self.snapshot()

    def stop_session(self) -> dict:
        if self._pipeline is not None:
            self._pipeline.stop_session()
        self._latest_result = None
        self._last_error = None
        return self.snapshot()

    async def process_rgb_frame(
        self,
        *,
        frame_id: int,
        timestamp_ms: int,
        jpeg_bytes: bytes,
    ) -> VisionFrameResult | None:
        pipeline = self._pipeline
        if pipeline is None:
            return None

        if not pipeline.is_active:
            try:
                pipeline.start_session(DEFAULT_VISION_PROMPT)
                add_python_log(
                    "info",
                    f"Vision session auto-started (prompt: {DEFAULT_VISION_PROMPT})",
                )
                self._last_error = None
            except VisionPipelineError as exc:
                self._last_error = str(exc)
                add_python_log("warning", f"Vision auto-start failed: {exc}")
                return None

        if self._lock.locked():
            self._dropped_frames += 1
            return None

        async with self._lock:
            result = await asyncio.to_thread(
                self._run_pipeline,
                pipeline,
                frame_id,
                timestamp_ms,
                jpeg_bytes,
            )
            if result is None:
                return None
            self._processed_frames += 1
            self._latest_result = result
            await self._broadcast(result.to_payload())
            return result

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        if self._latest_result is not None:
            await websocket.send_text(json.dumps(self._latest_result.to_payload()))

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    def latest_payload(self) -> dict | None:
        return None if self._latest_result is None else self._latest_result.to_payload()

    def _run_pipeline(
        self,
        pipeline: GroundedSamTrackingPipeline,
        frame_id: int,
        timestamp_ms: int,
        jpeg_bytes: bytes,
    ) -> VisionFrameResult | None:
        decoded = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            raise VisionPipelineError("failed to decode RGB JPEG for vision pipeline")
        try:
            with torch.inference_mode():
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    return pipeline.process_frame(
                        frame_id=frame_id,
                        timestamp_ms=timestamp_ms,
                        image_bgr=decoded,
                    )
        except VisionPipelineError as exc:
            self._last_error = str(exc)
            add_python_log("warning", f"Vision pipeline error: {exc}")
            return None
        except Exception as exc:
            self._last_error = str(exc)
            add_python_log("error", f"Vision pipeline crash: {exc}")
            return None

    async def _broadcast(self, payload: dict) -> None:
        if not self._clients:
            return
        data = json.dumps(payload)
        stale: list[WebSocket] = []
        for client in list(self._clients):
            try:
                await asyncio.wait_for(client.send_text(data), timeout=0.05)
            except Exception:
                stale.append(client)
        for client in stale:
            self._clients.discard(client)

    def _require_pipeline(self) -> GroundedSamTrackingPipeline:
        if self._pipeline is None:
            raise VisionPipelineError(
                "Quest3 vision pipeline is unavailable. Set QUEST3_VISION_ENABLED=1 and install optional model dependencies."
            )
        return self._pipeline

    def _metrics_snapshot(self) -> dict:
        latest = self._latest_result
        return {
            "processed_frames": self._processed_frames,
            "dropped_frames": self._dropped_frames,
            "last_frame_id": None if latest is None else latest.frame_id,
            "last_timestamp_ms": None if latest is None else latest.timestamp_ms,
            "last_mode": None if latest is None else latest.mode,
            "last_process_time_ms": None
            if latest is None or latest.process_time_ms is None
            else round(float(latest.process_time_ms), 3),
            "last_gpu_memory_mb": None
            if latest is None or latest.gpu_memory_mb is None
            else latest.gpu_memory_mb.to_payload(),
            "last_object_count": 0 if latest is None else len(latest.objects),
        }

    def _pipeline_value(self, name: str):
        if self._pipeline is None:
            return None
        return getattr(self._pipeline, name, None)
