from __future__ import annotations

import numpy as np

from quest3server.vision.pipeline import VisionPipelineError
from quest3server.vision.rle import encode_binary_mask
from quest3server.vision.types import GpuMemoryStats, TrackedMask, VisionFrameResult


class FakeSubprocessPipeline:
    def __init__(self) -> None:
        self.is_active = False
        self.prompt: str | None = None
        self.source = "fake-subprocess-pipeline"
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

        height, width = image_bgr.shape[:2]
        mask = np.zeros((height, width), dtype=bool)
        mask[max(0, height // 4) : max(1, (3 * height) // 4), max(0, width // 4) : max(1, (3 * width) // 4)] = True
        return VisionFrameResult(
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
            frame_width=width,
            frame_height=height,
            prompt=self.prompt,
            source=self.source,
            mode="detection",
            process_time_ms=7.25,
            gpu_memory_mb=GpuMemoryStats(allocated=12.0, max_allocated=24.0),
            objects=[
                TrackedMask(
                    object_id=1,
                    label=self.prompt,
                    score=0.95,
                    box_xyxy=(width // 4, height // 4, (3 * width) // 4, (3 * height) // 4),
                    area=int(mask.sum()),
                    mask_rle=encode_binary_mask(mask),
                )
            ],
        )


def create_pipeline():
    return FakeSubprocessPipeline()
