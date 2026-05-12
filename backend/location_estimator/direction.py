from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from location_estimator.models import APPosition


@dataclass(frozen=True)
class _SampleView:
    bssid: str
    rssi: float


class DirectionEstimator:
    def __init__(self, ap_positions: dict[str, APPosition]) -> None:
        self.ap_positions = ap_positions

    def estimate(self, samples: Iterable[Any]) -> tuple[float, float]:
        sample_views = [view for view in (self._to_view(item) for item in samples) if view is not None]
        if not sample_views:
            return 0.0, 0.0

        strongest = max(sample_views, key=lambda item: item.rssi)
        strongest_angle = self._ap_angle(strongest.bssid)
        if strongest_angle is None:
            return 0.0, 0.0

        vector_x = 0.0
        vector_y = 0.0
        usable_count = 0

        min_rssi = min(sample.rssi for sample in sample_views)
        max_rssi = max(sample.rssi for sample in sample_views)
        rssi_span = max(max_rssi - min_rssi, 1.0)

        for sample in sample_views:
            angle = self._ap_angle(sample.bssid)
            if angle is None:
                continue
            weight = max(0.0, (sample.rssi - min_rssi) / rssi_span)
            if weight == 0.0 and sample.bssid == strongest.bssid:
                weight = 1.0
            rad = math.radians(angle)
            vector_x += weight * math.cos(rad)
            vector_y += weight * math.sin(rad)
            usable_count += 1

        if usable_count == 0:
            return strongest_angle, 0.1

        weighted_angle = (math.degrees(math.atan2(vector_y, vector_x)) + 360.0) % 360.0
        vector_norm = min(1.0, math.hypot(vector_x, vector_y) / max(usable_count, 1))
        ap_coverage = min(1.0, usable_count / max(len(self.ap_positions), 1))
        confidence = max(0.05, min(1.0, 0.6 * vector_norm + 0.4 * ap_coverage))
        return weighted_angle, confidence

    def _ap_angle(self, bssid: str) -> float | None:
        ap = self.ap_positions.get(bssid)
        if ap is None:
            return None
        angle = (math.degrees(math.atan2(ap.x, ap.y)) + 360.0) % 360.0
        return angle

    @staticmethod
    def _to_view(sample: Any) -> _SampleView | None:
        if isinstance(sample, dict):
            bssid = sample.get("bssid")
            rssi = sample.get("rssi")
        else:
            bssid = getattr(sample, "bssid", None)
            rssi = getattr(sample, "rssi", None)

        if not isinstance(bssid, str) or rssi is None:
            return None
        try:
            return _SampleView(bssid=bssid, rssi=float(rssi))
        except (TypeError, ValueError):
            return None
