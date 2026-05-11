from __future__ import annotations

import math
from typing import Optional

from .types import CalibrationPoint, CalibrationResult, Pose, Quaternion, Vec3


class CoordinateAligner:
    def __init__(self):
        self._calibration_points: list[CalibrationPoint] = []
        self._result: Optional[CalibrationResult] = None
        self._current_pose: Optional[Pose] = None

    @property
    def is_calibrated(self) -> bool:
        return self._result is not None

    @property
    def result(self) -> Optional[CalibrationResult]:
        return self._result

    @property
    def current_pose(self) -> Optional[Pose]:
        return self._current_pose

    def add_calibration_point(self, point: CalibrationPoint) -> None:
        self._calibration_points.append(point)

    def calibrate(self) -> CalibrationResult:
        if len(self._calibration_points) == 0:
            raise ValueError("At least one calibration point is required")

        points = self._calibration_points

        if len(points) == 1:
            result = self._calibrate_single(points[0])
        else:
            result = self._calibrate_multi(points)

        self._result = result
        return result

    def _calibrate_single(self, point: CalibrationPoint) -> CalibrationResult:
        rot_quat = Quaternion.from_yaw(math.radians(point.slam_heading_deg))
        p_slam_rotated = rot_quat.rotate(point.slam_position)
        translation = point.world_position - p_slam_rotated

        residual = 0.0

        return CalibrationResult(
            translation=translation,
            rotation_yaw_deg=point.slam_heading_deg,
            num_points=1,
            residual_m=residual,
        )

    def _calibrate_multi(self, points: list[CalibrationPoint]) -> CalibrationResult:
        if all(p.slam_heading_deg == 0.0 for p in points):
            return self._calibrate_multi_no_heading(points)
        else:
            return self._calibrate_multi_with_heading(points)

    def _calibrate_multi_no_heading(self, points: list[CalibrationPoint]) -> CalibrationResult:
        n = len(points)

        centroid_slam = Vec3(0, 0, 0)
        centroid_world = Vec3(0, 0, 0)
        for p in points:
            centroid_slam = centroid_slam + p.slam_position
            centroid_world = centroid_world + p.world_position
        centroid_slam = Vec3(centroid_slam.x / n, centroid_slam.y / n, centroid_slam.z / n)
        centroid_world = Vec3(centroid_world.x / n, centroid_world.y / n, centroid_world.z / n)

        slam_centered = [p.slam_position - centroid_slam for p in points]
        world_centered = [p.world_position - centroid_world for p in points]

        H_xx = sum(a.x * b.x + a.z * b.z for a, b in zip(slam_centered, world_centered))
        H_xz = sum(a.x * b.z - a.z * b.x for a, b in zip(slam_centered, world_centered))

        yaw = math.atan2(H_xz, H_xx)
        rot_quat = Quaternion.from_yaw(yaw)

        p_slam_centroid_rot = rot_quat.rotate(centroid_slam)
        translation = centroid_world - p_slam_centroid_rot

        residual = 0.0
        for p in points:
            p_pred = rot_quat.rotate(p.slam_position) + translation
            diff = p.world_position - p_pred
            residual += diff.norm()
        residual /= n

        return CalibrationResult(
            translation=translation,
            rotation_yaw_deg=math.degrees(yaw),
            num_points=n,
            residual_m=residual,
        )

    def _calibrate_multi_with_heading(self, points: list[CalibrationPoint]) -> CalibrationResult:
        headings_rad = [math.radians(p.slam_heading_deg) for p in points]
        mean_heading = sum(headings_rad) / len(headings_rad)
        mean_yaw = math.atan2(
            sum(math.sin(h) for h in headings_rad),
            sum(math.cos(h) for h in headings_rad),
        )
        rot_quat = Quaternion.from_yaw(mean_yaw)

        translations = []
        for p in points:
            p_rot = rot_quat.rotate(p.slam_position)
            t = p.world_position - p_rot
            translations.append(t)

        translation = Vec3(
            sum(t.x for t in translations) / len(translations),
            sum(t.y for t in translations) / len(translations),
            sum(t.z for t in translations) / len(translations),
        )

        residual = 0.0
        for p in points:
            p_pred = rot_quat.rotate(p.slam_position) + translation
            diff = p.world_position - p_pred
            residual += diff.norm()
        residual /= len(points)

        return CalibrationResult(
            translation=translation,
            rotation_yaw_deg=math.degrees(mean_yaw),
            num_points=len(points),
            residual_m=residual,
        )

    def slam_to_world(self, slam_position: Vec3) -> Vec3:
        if self._result is None:
            raise RuntimeError("Calibration not performed. Call calibrate() first.")
        rotated = self._result.rotation_quat.rotate(slam_position)
        return rotated + self._result.translation

    def world_to_slam(self, world_position: Vec3) -> Vec3:
        if self._result is None:
            raise RuntimeError("Calibration not performed. Call calibrate() first.")
        inv_quat = self._result.rotation_quat.inverse()
        translated = world_position - self._result.translation
        return inv_quat.rotate(translated)

    def update_current_pose(self, pose: Pose) -> None:
        self._current_pose = pose

    def reset(self) -> None:
        self._calibration_points.clear()
        self._result = None
        self._current_pose = None
