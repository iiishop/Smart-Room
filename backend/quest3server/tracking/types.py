from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
    mask_rle: dict[str, Any] | None = None
    mask_area: int = 0
    center_3d_m: tuple[float, float, float] | None = None
    depth_median_m: float | None = None
    depth_confidence: float | None = None
    segmentation_source: str = "rgb"
    segmentation_confidence: float | None = None
    parts: list[dict[str, Any]] = field(default_factory=list)
    visual_evidence: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict:
        x0, y0, x1, y1 = self.box_xyxy
        payload = {
            "object_id": self.object_id,
            "state": self.state.value,
            "label": self.label,
            "score": round(float(self.score), 4),
            "box_xyxy": [x0, y0, x1, y1],
            "center_pixel": list(self.center_pixel),
        }
        if self.mask_rle is not None:
            payload["mask_rle"] = self.mask_rle
            payload["mask_area"] = int(self.mask_area)
        if self.center_3d_m is not None:
            payload["center_3d_m"] = [
                round(float(value), 4) for value in self.center_3d_m
            ]
        if self.depth_median_m is not None:
            payload["depth_median_m"] = round(float(self.depth_median_m), 4)
        if self.depth_confidence is not None:
            payload["depth_confidence"] = round(float(self.depth_confidence), 4)
        if self.segmentation_source:
            payload["segmentation_source"] = self.segmentation_source
        if self.segmentation_confidence is not None:
            payload["segmentation_confidence"] = round(
                float(self.segmentation_confidence), 4
            )
        if self.parts:
            payload["parts"] = self.parts
        if self.visual_evidence:
            payload["visual_evidence"] = self.visual_evidence
        if self.diagnostics:
            payload["diagnostics"] = self.diagnostics
        return payload
