from __future__ import annotations

import math

import numpy as np
from filterpy.kalman import ExtendedKalmanFilter


class PositionEKF:
    def __init__(self, dt: float = 1.0, process_noise: float = 0.15, measurement_noise: float = 0.8) -> None:
        self._ekf = ExtendedKalmanFilter(dim_x=4, dim_z=2)
        self._initialized = False

        self._ekf.x = np.zeros((4, 1), dtype=float)
        self._ekf.F = np.array(
            [
                [1.0, 0.0, dt, 0.0],
                [0.0, 1.0, 0.0, dt],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=float,
        )
        self._ekf.P = np.eye(4, dtype=float) * 25.0
        self._ekf.Q = np.eye(4, dtype=float) * process_noise
        self._ekf.R = np.diag([measurement_noise, math.radians(15.0) ** 2]).astype(float)

    def predict(self) -> None:
        self._ekf.predict()

    def update(self, distance: float, angle_deg: float) -> None:
        if not self._initialized:
            angle_rad = math.radians(float(angle_deg))
            self._ekf.x[0, 0] = float(distance) * math.sin(angle_rad)
            self._ekf.x[1, 0] = float(distance) * math.cos(angle_rad)
            self._initialized = True

        z = np.array([[float(distance)], [math.radians(float(angle_deg))]], dtype=float)
        self._ekf.update(z=z, HJacobian=self._h_jacobian, Hx=self._h_x)

    def state(self) -> tuple[float, float, float, float]:
        x, y, vx, vy = self._ekf.x.flatten().tolist()
        return float(x), float(y), float(vx), float(vy)

    @staticmethod
    def _h_x(state: np.ndarray) -> np.ndarray:
        x = float(state[0, 0])
        y = float(state[1, 0])
        distance = math.hypot(x, y)
        bearing = math.atan2(x, y)
        return np.array([[distance], [bearing]], dtype=float)

    @staticmethod
    def _h_jacobian(state: np.ndarray) -> np.ndarray:
        x = float(state[0, 0])
        y = float(state[1, 0])
        distance_sq = max(x * x + y * y, 1e-6)
        distance = math.sqrt(distance_sq)

        return np.array(
            [
                [x / distance, y / distance, 0.0, 0.0],
                [y / distance_sq, -x / distance_sq, 0.0, 0.0],
            ],
            dtype=float,
        )
