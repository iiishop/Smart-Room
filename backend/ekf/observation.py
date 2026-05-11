from __future__ import annotations

import numpy as np

from .ekf_engine import EKFFusionEngine


def wifi_rssi_update(
    engine: EKFFusionEngine,
    ap_position: np.ndarray,
    rssi: float,
    reference_rssi: float,
    reference_distance: float,
    path_loss_exponent: float,
    rssi_std: float,
) -> None:
    p = engine.x[0:3]
    delta = p - ap_position
    dist = float(np.linalg.norm(delta))
    eps = 1e-6

    if dist < eps:
        dist = eps

    rssi_pred = reference_rssi - 10.0 * path_loss_exponent * np.log10(dist / reference_distance)
    innovation = rssi - rssi_pred

    d_rssi_d_dist = -10.0 * path_loss_exponent / (dist * np.log(10.0))
    d_dist_d_pos = delta / dist

    H = np.zeros((1, 15), dtype=np.float64)
    H[0, 0:3] = d_rssi_d_dist * d_dist_d_pos

    R = np.array([[rssi_std**2]], dtype=np.float64)
    engine._kalman_update(np.array([innovation]), H, R, [0, 1, 2])


def wifi_rtt_update(
    engine: EKFFusionEngine,
    ap_position: np.ndarray,
    distance_m: float,
    rtt_std: float,
) -> None:
    p = engine.x[0:3]
    delta = p - ap_position
    dist = float(np.linalg.norm(delta))
    eps = 1e-6

    if dist < eps:
        dist = eps

    innovation = distance_m - dist

    H = np.zeros((1, 15), dtype=np.float64)
    H[0, 0:3] = delta / dist

    R = np.array([[rtt_std**2]], dtype=np.float64)
    engine._kalman_update(np.array([innovation]), H, R, [0, 1, 2])


def slam_prior_update(
    engine: EKFFusionEngine,
    slam_position: np.ndarray,
    slam_quaternion: np.ndarray,
    position_std: float,
    orientation_std_rad: float,
) -> None:
    R_pos = np.eye(3, dtype=np.float64) * (position_std ** 2)
    R_rot = np.eye(3, dtype=np.float64) * (orientation_std_rad ** 2)
    engine.update_pose(slam_position, slam_quaternion, R_pos, R_rot)
