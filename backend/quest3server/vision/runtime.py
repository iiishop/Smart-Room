from __future__ import annotations

import asyncio
import json

import cv2
import numpy as np
from fastapi import WebSocket

from ..log_manager import add_python_log
from .pipeline import GroundedSamTrackingPipeline, VisionPipelineError
from .types import VisionFrameResult


class Quest3VisionRuntime:
    def __init__(self, pipeline: GroundedSamTrackingPipeline | None) -> None:
        self._pipeline = pipeline
        self._latest_result: VisionFrameResult | None = None
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
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
            "dropped_frames": self._dropped_frames,
            "last_error": self._last_error,
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
        if pipeline is None or not pipeline.is_active:
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
