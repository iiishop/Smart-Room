from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class IMUReading:
    timestamp_s: float
    accel_x: float
    accel_y: float
    accel_z: float
    gyro_x: float
    gyro_y: float
    gyro_z: float

    @classmethod
    def from_array(cls, t: float, accel, gyro) -> "IMUReading":
        return cls(t, accel[0], accel[1], accel[2], gyro[0], gyro[1], gyro[2])


@dataclass
class SLAMPose:
    timestamp_s: float
    position_x: float
    position_y: float
    position_z: float
    quat_w: float
    quat_x: float
    quat_y: float
    quat_z: float
    position_std: float = 0.05
    orientation_std_rad: float = 0.05


@dataclass
class WiFiAPInfo:
    ap_id: str
    position_x: float
    position_y: float
    position_z: float
    reference_rssi: float = -30.0
    reference_distance: float = 1.0
    path_loss_exponent: float = 2.0
    rssi_std: float = 3.0
    rtt_std: float = 0.5


@dataclass
class WiFiRSSIObservation:
    timestamp_s: float
    ap_id: str
    rssi: float


@dataclass
class WiFiRTTObservation:
    timestamp_s: float
    ap_id: str
    distance_m: float


@dataclass
class NavigationState:
    position_x: float = 0.0
    position_y: float = 0.0
    position_z: float = 0.0
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    velocity_z: float = 0.0
    quat_w: float = 1.0
    quat_x: float = 0.0
    quat_y: float = 0.0
    quat_z: float = 0.0
    timestamp_s: float = field(default_factory=time.time)

    position_cov: list[float] = field(default_factory=lambda: [1.0] * 3)
    velocity_cov: list[float] = field(default_factory=lambda: [1.0] * 3)
    orientation_cov_rad: list[float] = field(default_factory=lambda: [0.1] * 3)

    def to_json(self) -> dict:
        return {
            "x": self.position_x,
            "y": self.position_y,
            "z": self.position_z,
            "vx": self.velocity_x,
            "vy": self.velocity_y,
            "vz": self.velocity_z,
            "qw": self.quat_w,
            "qx": self.quat_x,
            "qy": self.quat_y,
            "qz": self.quat_z,
            "timestamp": self.timestamp_s,
        }

    @classmethod
    def from_json(cls, data: dict) -> "NavigationState":
        return cls(
            position_x=data["x"],
            position_y=data["y"],
            position_z=data["z"],
            velocity_x=data["vx"],
            velocity_y=data["vy"],
            velocity_z=data["vz"],
            quat_w=data["qw"],
            quat_x=data["qx"],
            quat_y=data["qy"],
            quat_z=data["qz"],
            timestamp_s=data["timestamp"],
        )
