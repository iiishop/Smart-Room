from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class EKFConfig:
    accel_noise_std: float = 0.05
    gyro_noise_std: float = 0.001
    accel_bias_noise_std: float = 1e-5
    gyro_bias_noise_std: float = 1e-6
    init_pos_std: float = 0.5
    init_vel_std: float = 0.1
    init_attitude_rad_std: float = 0.05
    init_gyro_bias_std: float = 1e-3
    init_accel_bias_std: float = 0.01
    gravity: float = 9.80665

    def default_state(self) -> np.ndarray:
        x = np.zeros(16, dtype=np.float64)
        x[6] = 1.0
        return x

    def default_covariance(self) -> np.ndarray:
        P = np.zeros((15, 15), dtype=np.float64)
        stds = np.array([
            self.init_pos_std, self.init_pos_std, self.init_pos_std,
            self.init_vel_std, self.init_vel_std, self.init_vel_std,
            self.init_attitude_rad_std, self.init_attitude_rad_std, self.init_attitude_rad_std,
            self.init_gyro_bias_std, self.init_gyro_bias_std, self.init_gyro_bias_std,
            self.init_accel_bias_std, self.init_accel_bias_std, self.init_accel_bias_std,
        ])
        np.fill_diagonal(P, stds**2)
        return P


def _skew(v: np.ndarray) -> np.ndarray:
    return np.array([
        [0.0, -v[2], v[1]],
        [v[2], 0.0, -v[0]],
        [-v[1], v[0], 0.0],
    ], dtype=np.float64)


def _quat_multiply(q: np.ndarray, p: np.ndarray) -> np.ndarray:
    qw, qx, qy, qz = q
    pw, px, py, pz = p
    return np.array([
        qw * pw - qx * px - qy * py - qz * pz,
        qw * px + qx * pw + qy * pz - qz * py,
        qw * py - qx * pz + qy * pw + qz * px,
        qw * pz + qx * py - qy * px + qz * pw,
    ], dtype=np.float64)


def _quat_conjugate(q: np.ndarray) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]], dtype=np.float64)


def _quat_rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    qv = np.array([0.0, v[0], v[1], v[2]], dtype=np.float64)
    result = _quat_multiply(_quat_multiply(q, qv), _quat_conjugate(q))
    return result[1:4]


def _quat_to_matrix(q: np.ndarray) -> np.ndarray:
    qw, qx, qy, qz = q
    return np.array([
        [1 - 2 * (qy**2 + qz**2), 2 * (qx * qy - qw * qz), 2 * (qx * qz + qw * qy)],
        [2 * (qx * qy + qw * qz), 1 - 2 * (qx**2 + qz**2), 2 * (qy * qz - qw * qx)],
        [2 * (qx * qz - qw * qy), 2 * (qy * qz + qw * qx), 1 - 2 * (qx**2 + qy**2)],
    ], dtype=np.float64)


def _quat_normalize(q: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(q)
    if norm < 1e-15:
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    return q / norm


def _exp_so3(w: np.ndarray) -> np.ndarray:
    angle = np.linalg.norm(w)
    if angle < 1e-10:
        return np.eye(3, dtype=np.float64)
    axis = w / angle
    K = _skew(axis)
    return np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * (K @ K)


def _quat_from_rotvec(w: np.ndarray) -> np.ndarray:
    angle = np.linalg.norm(w)
    if angle < 1e-10:
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    axis = w / angle
    half = angle * 0.5
    return np.array([np.cos(half), np.sin(half) * axis[0],
                     np.sin(half) * axis[1], np.sin(half) * axis[2]], dtype=np.float64)


def _quat_to_rotvec(q: np.ndarray) -> np.ndarray:
    q = _quat_normalize(q)
    if q[0] >= 1.0 - 1e-10:
        return np.zeros(3, dtype=np.float64)
    half_angle = np.arccos(q[0])
    sin_half = np.sin(half_angle)
    if abs(sin_half) < 1e-15:
        return np.zeros(3, dtype=np.float64)
    return 2.0 * half_angle * q[1:4] / sin_half


def _error_state_to_nominal(
    nom: np.ndarray, err: np.ndarray
) -> np.ndarray:
    corrected = nom.copy()
    corrected[0:3] += err[0:3]
    corrected[3:6] += err[3:6]
    delta_q = _quat_from_rotvec(err[6:9])
    corrected[6:10] = _quat_multiply(delta_q, nom[6:10])
    corrected[6:10] = _quat_normalize(corrected[6:10])
    corrected[10:13] += err[9:12]
    corrected[13:16] += err[12:15]
    return corrected


class EKFFusionEngine:
    def __init__(self, config: EKFConfig | None = None):
        self.config = config or EKFConfig()
        self.x = self.config.default_state()
        self.P = self.config.default_covariance()
        self.last_timestamp_s: float | None = None

    def reset(self) -> None:
        self.x = self.config.default_state()
        self.P = self.config.default_covariance()
        self.last_timestamp_s = None

    def get_state(self) -> tuple[np.ndarray, np.ndarray]:
        return self.x.copy(), self.P.copy()

    def set_state(self, x: np.ndarray, P: np.ndarray) -> None:
        self.x = x.copy()
        self.P = P.copy()

    def predict_imu(
        self, accel: np.ndarray, gyro: np.ndarray, dt: float
    ) -> None:
        cfg = self.config
        p = self.x[0:3]
        v = self.x[3:6]
        q = self.x[6:10]
        bg = self.x[10:13]
        ba = self.x[13:16]

        omega = gyro - bg
        a_body = accel - ba
        gravity = np.array([0.0, 0.0, -cfg.gravity], dtype=np.float64)
        a_world = _quat_rotate(q, a_body) + gravity
        p_new = p + v * dt + 0.5 * a_world * dt * dt
        v_new = v + a_world * dt
        omega_norm = np.linalg.norm(omega)
        if omega_norm < 1e-15:
            q_new = q.copy()
        else:
            ex = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
            wx, wy, wz = omega[0] * dt * 0.5, omega[1] * dt * 0.5, omega[2] * dt * 0.5
            w_norm = np.sqrt(wx * wx + wy * wy + wz * wz)
            if w_norm < 1e-15:
                q_delta = ex
            else:
                sin_w = np.sin(w_norm) / w_norm
                q_delta = np.array([np.cos(w_norm), wx * sin_w, wy * sin_w, wz * sin_w])
            q_new = _quat_multiply(q, q_delta)
        q_new = _quat_normalize(q_new)
        bg_new = bg.copy()
        ba_new = ba.copy()

        self.x[0:3] = p_new
        self.x[3:6] = v_new
        self.x[6:10] = q_new
        self.x[10:13] = bg_new
        self.x[13:16] = ba_new

        R = _quat_to_matrix(q)
        a_body_skew = _skew(a_body)

        F = np.eye(15, dtype=np.float64)
        F[0:3, 3:6] = np.eye(3) * dt
        F[0:3, 6:9] = -0.5 * R @ a_body_skew * dt * dt
        F[0:3, 12:15] = -0.5 * R * dt * dt
        F[3:6, 6:9] = -R @ a_body_skew * dt
        F[3:6, 12:15] = -R * dt
        F[6:9, 6:9] = _exp_so3(-omega * dt)
        F[6:9, 9:12] = -np.eye(3) * dt

        G = np.zeros((15, 12), dtype=np.float64)
        G[3:6, 0:3] = R * dt
        G[3:6, 3:6] = 0.5 * R @ a_body_skew * dt * dt
        G[6:9, 3:6] = np.eye(3) * dt
        G[9:12, 6:9] = np.eye(3) * dt
        G[12:15, 9:12] = np.eye(3) * dt

        Q_imu = np.zeros((12, 12), dtype=np.float64)
        accel_noise = cfg.accel_noise_std**2
        gyro_noise = cfg.gyro_noise_std**2
        accel_bias_noise = cfg.accel_bias_noise_std**2
        gyro_bias_noise = cfg.gyro_bias_noise_std**2
        Q_imu[0:3, 0:3] = np.eye(3) * accel_noise
        Q_imu[3:6, 3:6] = np.eye(3) * gyro_noise
        Q_imu[6:9, 6:9] = np.eye(3) * gyro_bias_noise
        Q_imu[9:12, 9:12] = np.eye(3) * accel_bias_noise

        self.P = F @ self.P @ F.T + G @ Q_imu @ G.T

    def update_position(self, z_pos: np.ndarray, R_pos: np.ndarray) -> None:
        H = np.zeros((3, 15), dtype=np.float64)
        H[0:3, 0:3] = np.eye(3)
        self._kalman_update(z_pos - self.x[0:3], H, R_pos, [0, 1, 2])

    def update_velocity(self, z_vel: np.ndarray, R_vel: np.ndarray) -> None:
        H = np.zeros((3, 15), dtype=np.float64)
        H[0:3, 3:6] = np.eye(3)
        self._kalman_update(z_vel - self.x[3:6], H, R_vel, [3, 4, 5])

    def update_pose(self, z_pos: np.ndarray, z_quat: np.ndarray,
                    R_pos: np.ndarray, R_rot: np.ndarray) -> None:
        self.update_position(z_pos, R_pos)

        q_meas = _quat_normalize(z_quat)
        q_est = _quat_normalize(self.x[6:10])
        q_err = _quat_multiply(q_meas, _quat_conjugate(q_est))
        err_rotvec = _quat_to_rotvec(q_err)

        H = np.zeros((3, 15), dtype=np.float64)
        H[0:3, 6:9] = np.eye(3)
        self._kalman_update(err_rotvec, H, R_rot, [6, 7, 8])

    def _kalman_update(self, innovation: np.ndarray, H: np.ndarray,
                       R: np.ndarray, error_indices: list[int]) -> None:
        PHt = self.P @ H.T
        S = H @ PHt + R
        try:
            K = np.linalg.solve(S.T, PHt.T).T
        except np.linalg.LinAlgError:
            K = PHt @ np.linalg.pinv(S)

        dx = np.asarray(K @ innovation, dtype=np.float64).ravel()
        self.x = _error_state_to_nominal(self.x, dx)
        I_KH = np.eye(15) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ R @ K.T

    def get_navigation_state(self) -> dict:
        diag = np.diag(self.P)
        return {
            "x": float(self.x[0]),
            "y": float(self.x[1]),
            "z": float(self.x[2]),
            "vx": float(self.x[3]),
            "vy": float(self.x[4]),
            "vz": float(self.x[5]),
            "qw": float(self.x[6]),
            "qx": float(self.x[7]),
            "qy": float(self.x[8]),
            "qz": float(self.x[9]),
            "timestamp": 0.0,
        }
