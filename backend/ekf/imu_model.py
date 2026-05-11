from __future__ import annotations

import numpy as np

from .ekf_engine import EKFFusionEngine


def imu_predict(
    engine: EKFFusionEngine,
    accel: np.ndarray,
    gyro: np.ndarray,
    dt: float,
) -> None:
    engine.predict_imu(accel, gyro, dt)
