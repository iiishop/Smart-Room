from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .types import IMUReading, SLAMPose, WiFiRSSIObservation, WiFiRTTObservation, WiFiAPInfo


@dataclass
class TrajectoryPoint:
    t: float
    pos: np.ndarray
    vel: np.ndarray
    quat: np.ndarray
    accel_world: np.ndarray
    omega_body: np.ndarray


def _quat_multiply(q: np.ndarray, p: np.ndarray) -> np.ndarray:
    qw, qx, qy, qz = q
    pw, px, py, pz = p
    return np.array([
        qw * pw - qx * px - qy * py - qz * pz,
        qw * px + qx * pw + qy * pz - qz * py,
        qw * py - qx * pz + qy * pw + qz * px,
        qw * pz + qx * py - qy * px + qz * pw,
    ])


def _quat_conjugate(q: np.ndarray) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]])


def _quat_rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    qv = np.array([0.0, v[0], v[1], v[2]])
    result = _quat_multiply(_quat_multiply(q, qv), _quat_conjugate(q))
    return result[1:4]


def _quat_normalize(q: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(q)
    if norm < 1e-15:
        return np.array([1.0, 0.0, 0.0, 0.0])
    return q / norm


def _quat_from_euler(roll, pitch, yaw) -> np.ndarray:
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    return np.array([
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    ])


def generate_ground_truth_trajectory(
    duration_s: float = 30.0,
    imu_freq_hz: float = 200.0,
) -> list[TrajectoryPoint]:
    points: list[TrajectoryPoint] = []
    dt = 1.0 / imu_freq_hz
    n = int(duration_s * imu_freq_hz)

    pos = np.array([0.0, 0.0, 1.5])
    vel = np.array([0.3, 0.15, 0.0])
    quat = np.array([1.0, 0.0, 0.0, 0.0])

    for i in range(n):
        t = i * dt
        yaw = 0.1 * math.sin(0.3 * t)
        quat = _quat_from_euler(0.0, 0.0, yaw)
        quat = _quat_normalize(quat)

        radius = 3.0
        omega_orbit = 0.2
        target_pos = np.array([
            radius * math.cos(omega_orbit * t),
            radius * math.sin(omega_orbit * t),
            1.5 + 0.1 * math.sin(0.5 * t),
        ])
        vel_target = np.array([
            -radius * omega_orbit * math.sin(omega_orbit * t),
            radius * omega_orbit * math.cos(omega_orbit * t),
            0.05 * math.cos(0.5 * t),
        ])
        accel_target = np.array([
            -radius * omega_orbit**2 * math.cos(omega_orbit * t),
            -radius * omega_orbit**2 * math.sin(omega_orbit * t),
            -0.025 * math.sin(0.5 * t),
        ])

        omega_body = np.array([0.0, 0.0, 0.1 * 0.3 * math.cos(0.3 * t)])

        points.append(TrajectoryPoint(
            t=t, pos=target_pos.copy(), vel=vel_target.copy(),
            quat=quat.copy(), accel_world=accel_target.copy(),
            omega_body=omega_body.copy(),
        ))

    return points


class IMUSimulator:
    def __init__(
        self,
        accel_noise_std: float = 0.05,
        gyro_noise_std: float = 0.001,
        accel_bias: tuple[float, float, float] = (0.01, -0.005, 0.003),
        gyro_bias: tuple[float, float, float] = (0.001, -0.0005, 0.0002),
        gravity: float = 9.80665,
    ):
        self.accel_noise_std = accel_noise_std
        self.gyro_noise_std = gyro_noise_std
        self.accel_bias = np.array(accel_bias)
        self.gyro_bias = np.array(gyro_bias)
        self.gravity = np.array([0.0, 0.0, gravity])

    def simulate_imu(
        self,
        point: TrajectoryPoint,
    ) -> IMUReading:
        q_conj = _quat_conjugate(point.quat)
        a_world = point.accel_world + self.gravity
        a_body = _quat_rotate(q_conj, a_world)
        a_body += self.accel_bias
        a_body += self.accel_noise_std * np.random.randn(3)

        omega = point.omega_body + self.gyro_bias
        omega += self.gyro_noise_std * np.random.randn(3)

        return IMUReading.from_array(point.t, a_body, omega)

    def simulate_slam(
        self,
        point: TrajectoryPoint,
        pos_std: float = 0.03,
        orient_std_rad: float = 0.02,
    ) -> SLAMPose:
        pos = point.pos + pos_std * np.random.randn(3)
        rng = orient_std_rad * np.random.randn(3)
        noise_q = _quat_from_euler(rng[0], rng[1], rng[2])
        quat = _quat_multiply(point.quat, noise_q)
        quat = _quat_normalize(quat)
        return SLAMPose(
            timestamp_s=point.t,
            position_x=float(pos[0]),
            position_y=float(pos[1]),
            position_z=float(pos[2]),
            quat_w=float(quat[0]),
            quat_x=float(quat[1]),
            quat_y=float(quat[2]),
            quat_z=float(quat[3]),
            position_std=pos_std,
            orientation_std_rad=orient_std_rad,
        )

    def simulate_wifi_rssi(
        self,
        point: TrajectoryPoint,
        ap: WiFiAPInfo,
    ) -> WiFiRSSIObservation:
        delta = point.pos - np.array([ap.position_x, ap.position_y, ap.position_z])
        dist = float(np.linalg.norm(delta))
        eps = 1e-6
        if dist < eps:
            dist = eps
        rssi = ap.reference_rssi - 10.0 * ap.path_loss_exponent * math.log10(
            dist / ap.reference_distance
        )
        rssi += ap.rssi_std * np.random.randn()
        return WiFiRSSIObservation(timestamp_s=point.t, ap_id=ap.ap_id, rssi=float(rssi))

    def simulate_wifi_rtt(
        self,
        point: TrajectoryPoint,
        ap: WiFiAPInfo,
    ) -> WiFiRTTObservation:
        delta = point.pos - np.array([ap.position_x, ap.position_y, ap.position_z])
        dist = float(np.linalg.norm(delta))
        dist += ap.rtt_std * np.random.randn()
        return WiFiRTTObservation(timestamp_s=point.t, ap_id=ap.ap_id, distance_m=float(dist))
