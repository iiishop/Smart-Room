from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


VisionProcessingMode = Literal["detection", "propagation"]


@dataclass(slots=True)
class DetectionCandidate:
    object_id: int
    label: str
    score: float
    box_xyxy: tuple[int, int, int, int]


@dataclass(slots=True)
class TrackedMask:
    object_id: int
    label: str
    score: float
    box_xyxy: tuple[int, int, int, int]
    area: int
    mask_rle: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "label": self.label,
            "score": round(float(self.score), 6),
            "box_xyxy": list(self.box_xyxy),
            "area": int(self.area),
            "mask_rle": self.mask_rle,
        }


@dataclass(slots=True)
class GpuMemoryStats:
    allocated: float
    max_allocated: float

    def to_payload(self) -> dict[str, float]:
        return {
            "allocated": round(float(self.allocated), 3),
            "max_allocated": round(float(self.max_allocated), 3),
        }


@dataclass(slots=True)
class VisionFrameResult:
    frame_id: int
    timestamp_ms: int
    frame_width: int
    frame_height: int
    prompt: str
    objects: list[TrackedMask]
    source: str
    mode: VisionProcessingMode = "detection"
    process_time_ms: float | None = None
    gpu_memory_mb: GpuMemoryStats | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "frame_id": int(self.frame_id),
            "timestamp_ms": int(self.timestamp_ms),
            "frame_width": int(self.frame_width),
            "frame_height": int(self.frame_height),
            "prompt": self.prompt,
            "source": self.source,
            "mode": self.mode,
            "process_time_ms": None
            if self.process_time_ms is None
            else round(float(self.process_time_ms), 3),
            "gpu_memory_mb": None
            if self.gpu_memory_mb is None
            else self.gpu_memory_mb.to_payload(),
            "objects": [item.to_payload() for item in self.objects],
        }
