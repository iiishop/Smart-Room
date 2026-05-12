from __future__ import annotations

from dataclasses import dataclass
from math import log10


@dataclass(slots=True)
class PathLossParams:
    reference_distance: float = 1.0
    reference_rssi: float = -40.0
    path_loss_exponent: float = 2.0
    wall_attenuation: float = 0.0


class PathLossModel:
    def __init__(self, params: PathLossParams | None = None) -> None:
        self.params = params or PathLossParams()

    @staticmethod
    def _band_adjustment_db(frequency: int) -> float:
        return 0.0 if frequency < 5000 else 4.0

    def rssi_to_distance(self, rssi: float, frequency: int) -> tuple[float, float]:
        band_loss = self._band_adjustment_db(frequency)
        effective_ref = self.params.reference_rssi - band_loss
        n = max(self.params.path_loss_exponent, 0.3)
        exponent = (effective_ref - rssi - self.params.wall_attenuation) / (10.0 * n)
        distance = self.params.reference_distance * (10.0**exponent)
        distance = float(max(distance, 0.1))

        band_conf = 1.0 if frequency < 5000 else 0.9
        signal_gap = abs(rssi - effective_ref)
        variability_penalty = min(signal_gap / 80.0, 0.6)
        confidence = max(0.15, min(1.0, band_conf - variability_penalty))
        return distance, confidence

    def calibrate(self, known_distances: list[tuple[float, float]]) -> PathLossParams:
        if len(known_distances) < 2:
            raise ValueError("known_distances must contain at least 2 (distance, rssi) pairs")

        xs = []
        ys = []
        for distance, rssi in known_distances:
            if distance <= 0:
                raise ValueError("distance must be > 0")
            xs.append(log10(distance / self.params.reference_distance))
            ys.append(rssi)

        n_items = float(len(xs))
        mean_x = sum(xs) / n_items
        mean_y = sum(ys) / n_items
        cov_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        var_x = sum((x - mean_x) ** 2 for x in xs)
        if var_x == 0.0:
            raise ValueError("distance samples must contain at least two distinct values")
        slope = cov_xy / var_x
        intercept = mean_y - slope * mean_x

        n = float(max(0.3, -slope / 10.0))
        calibrated = PathLossParams(
            reference_distance=self.params.reference_distance,
            reference_rssi=float(intercept),
            path_loss_exponent=n,
            wall_attenuation=self.params.wall_attenuation,
        )
        self.params = calibrated
        return calibrated
