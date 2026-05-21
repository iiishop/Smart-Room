from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
class VisionFrameResult:
    frame_id: int
    timestamp_ms: int
    frame_width: int
    frame_height: int
    prompt: str
    objects: list[TrackedMask]
    source: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "frame_id": int(self.frame_id),
            "timestamp_ms": int(self.timestamp_ms),
            "frame_width": int(self.frame_width),
            "frame_height": int(self.frame_height),
            "prompt": self.prompt,
            "source": self.source,
            "objects": [item.to_payload() for item in self.objects],
        }
