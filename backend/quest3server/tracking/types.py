from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TrackState(str, Enum):
    """Tracking engine state machine.

    Sources:
      - SAM2 Video Predictor docs: https://github.com/facebookresearch/sam2
        propagate_in_video returns masks; empty masks signal lost tracking.
    """

    IDLE = "idle"
    TRACKING = "tracking"
    LOST = "lost"


@dataclass(slots=True)
class TrackingResult:
    """Per-frame tracking output sent to the Quest 3 client."""

    object_id: int
    state: TrackState
    label: str
    score: float
    box_xyxy: tuple[int, int, int, int]
    center_pixel: tuple[float, float] = field(default=(0.0, 0.0))

    def to_payload(self) -> dict:
        x0, y0, x1, y1 = self.box_xyxy
        return {
            "object_id": self.object_id,
            "state": self.state.value,
            "label": self.label,
            "score": round(float(self.score), 4),
            "box_xyxy": [x0, y0, x1, y1],
            "center_pixel": list(self.center_pixel),
        }
