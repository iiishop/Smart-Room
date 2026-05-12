from __future__ import annotations

import numpy as np

from .models import RawPosition, SmoothedPosition


class EKFTracker:
    """Kalman Filter for 2D position tracking (CV model).

    State vector: [x, y, vx, vy]
    Observation vector: [x, y]
    """

    def __init__(
        self,
        dt: float = 0.1,
        process_noise: float = 0.01,
        measurement_noise: float = 1.0,
        initial_covariance: float = 10.0,
    ) -> None:
        self.dt = dt

        self._x = np.zeros((4, 1))
        self._P = np.eye(4) * initial_covariance

        self._F = np.array([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ], dtype=np.float64)

        self._H = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0],
        ], dtype=np.float64)

        q = process_noise
        dt2 = dt * dt / 2.0
        dt3 = dt * dt * dt / 3.0
        self._Q = q * np.array([
            [dt3, 0, dt2, 0],
            [0, dt3, 0, dt2],
            [dt2, 0, dt, 0],
            [0, dt2, 0, dt],
        ], dtype=np.float64)

        self._R = np.eye(2) * measurement_noise

        self._initialized = False
        self._missed_updates = 0
        self._total_updates = 0
        self._timestamp: float = 0.0

    def predict(self, timestamp: float | None = None) -> SmoothedPosition:
        if timestamp is not None:
            self._timestamp = timestamp

        self._x = self._F @ self._x
        self._P = self._F @ self._P @ self._F.T + self._Q
        self._missed_updates += 1

        return self.get_state()

    def update(self, measurement: RawPosition, timestamp: float | None = None) -> SmoothedPosition:
        z = np.array([[measurement.x], [measurement.y]], dtype=np.float64)
        R = self._R / max(measurement.confidence, 0.1)

        if timestamp is not None:
            self._timestamp = timestamp

        if not self._initialized:
            self._x[0, 0] = measurement.x
            self._x[1, 0] = measurement.y
            self._x[2, 0] = 0.0
            self._x[3, 0] = 0.0
            self._initialized = True
            self._missed_updates = 0
            self._total_updates += 1
            return self.get_state()

        self._x = self._F @ self._x
        self._P = self._F @ self._P @ self._F.T + self._Q

        y = z - self._H @ self._x
        S = self._H @ self._P @ self._H.T + R
        K = self._P @ self._H.T @ np.linalg.inv(S)

        self._x = self._x + K @ y
        self._P = (np.eye(4) - K @ self._H) @ self._P

        self._missed_updates = 0
        self._total_updates += 1
        return self.get_state()

    def get_state(self) -> SmoothedPosition:
        cov_flat = [float(self._P[0, 0]), float(self._P[1, 1]),
                     float(self._P[2, 2]), float(self._P[3, 3])]
        uncertainty = np.sqrt(self._P[0, 0] + self._P[1, 1])
        confidence = float(max(0.05, 1.0 / (1.0 + uncertainty) - self._missed_updates * 0.05))

        return SmoothedPosition(
            x=float(self._x[0, 0]),
            y=float(self._x[1, 0]),
            vx=float(self._x[2, 0]),
            vy=float(self._x[3, 0]),
            covariance=cov_flat,
            confidence=confidence,
            timestamp=self._timestamp,
            source="ekf",
        )

    def reset(self) -> None:
        self._x = np.zeros((4, 1))
        self._P = np.eye(4) * 10.0
        self._initialized = False
        self._missed_updates = 0
        self._total_updates = 0
        self._timestamp = 0.0
