from __future__ import annotations

class DistanceModel:
    def __init__(self, reference_rssi: float = -45.0, path_loss_exponent: float = 2.2) -> None:
        self.reference_rssi = reference_rssi
        self.path_loss_exponent = path_loss_exponent

        self._band_params = {
            "2.4": (reference_rssi, path_loss_exponent),
            "5": (reference_rssi - 3.0, path_loss_exponent + 0.35),
        }

    def rssi_to_distance(self, rssi: float, frequency: float) -> float:
        reference_rssi, n = self._params_for_frequency(frequency)
        exponent = (reference_rssi - rssi) / (10.0 * n)
        distance = 10.0**exponent
        return max(0.1, float(distance))

    def _params_for_frequency(self, frequency: float) -> tuple[float, float]:
        if frequency >= 4900:
            return self._band_params["5"]
        return self._band_params["2.4"]
