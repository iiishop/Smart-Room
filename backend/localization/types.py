from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def __add__(self, other: Vec3) -> Vec3:
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Vec3) -> Vec3:
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def dot(self, other: Vec3) -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def norm(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def normalized(self) -> Vec3:
        n = self.norm()
        if n < 1e-12:
            return Vec3(0.0, 0.0, 0.0)
        return Vec3(self.x / n, self.y / n, self.z / n)

    def to_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass
class Quaternion:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0

    @classmethod
    def from_yaw(cls, yaw_rad: float) -> Quaternion:
        half = yaw_rad * 0.5
        return cls(0.0, float(math.sin(half)), 0.0, float(math.cos(half)))

    def rotate(self, v: Vec3) -> Vec3:
        qv = Quaternion(v.x, v.y, v.z, 0.0)
        q_conj = Quaternion(-self.x, -self.y, -self.z, self.w)
        r = _quat_mul(_quat_mul(self, qv), q_conj)
        return Vec3(r.x, r.y, r.z)

    def to_rotation_matrix(self) -> list[list[float]]:
        xx, yy, zz = self.x * self.x, self.y * self.y, self.z * self.z
        xy, xz, yz = self.x * self.y, self.x * self.z, self.y * self.z
        wx, wy, wz = self.w * self.x, self.w * self.y, self.w * self.z
        return [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
            [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
            [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
        ]

    def inverse(self) -> Quaternion:
        return Quaternion(-self.x, -self.y, -self.z, self.w)


def _quat_mul(a: Quaternion, b: Quaternion) -> Quaternion:
    return Quaternion(
        x=a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
        y=a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
        z=a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
        w=a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
    )


@dataclass
class Pose:
    position: Vec3 = field(default_factory=Vec3)
    orientation: Quaternion = field(default_factory=Quaternion)


@dataclass
class NavigationState:
    device_id: str
    timestamp_ms: int
    pose: Pose
    velocity: Vec3 = field(default_factory=Vec3)
    covariance: list[float] = field(default_factory=lambda: [0.0] * 36)


@dataclass
class DevicePosition:
    device_id: str
    world_position: Vec3
    confidence: float = 1.0
    timestamp_ms: int = 0


@dataclass
class DeviceBearing:
    device_id: str
    azimuth_deg: float
    elevation_deg: float
    distance_m: float
    confidence: float
    timestamp_ms: int = 0


@dataclass
class CalibrationPoint:
    slam_position: Vec3
    world_position: Vec3
    slam_heading_deg: float = 0.0


@dataclass
class CalibrationResult:
    translation: Vec3
    rotation_yaw_deg: float
    num_points: int
    residual_m: float
    rotation_quat: Quaternion = field(default_factory=Quaternion)

    def __post_init__(self):
        half = math.radians(self.rotation_yaw_deg) * 0.5
        self.rotation_quat = Quaternion(0.0, float(math.sin(half)), 0.0, float(math.cos(half)))
