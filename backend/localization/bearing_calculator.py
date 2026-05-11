from __future__ import annotations

import math
from typing import Optional

from .types import DeviceBearing, DevicePosition, NavigationState, Vec3


class BearingCalculator:
    def compute_bearing(
        self,
        quest_state: NavigationState,
        device: DevicePosition,
    ) -> DeviceBearing:
        v_world = device.world_position - quest_state.pose.position
        distance = v_world.norm()

        inv_orient = quest_state.pose.orientation.inverse()
        v_local = inv_orient.rotate(v_world)

        azimuth_rad = math.atan2(v_local.x, v_local.z)
        horizontal_dist = math.sqrt(v_local.x * v_local.x + v_local.z * v_local.z)
        elevation_rad = math.atan2(v_local.y, horizontal_dist) if horizontal_dist > 1e-9 else 0.0

        confidence = self._compute_confidence(quest_state, device)

        return DeviceBearing(
            device_id=device.device_id,
            azimuth_deg=math.degrees(azimuth_rad),
            elevation_deg=math.degrees(elevation_rad),
            distance_m=distance,
            confidence=confidence,
            timestamp_ms=quest_state.timestamp_ms,
        )

    def compute_bearings(
        self,
        quest_state: NavigationState,
        devices: list[DevicePosition],
    ) -> list[DeviceBearing]:
        return [self.compute_bearing(quest_state, d) for d in devices]

    def _compute_confidence(
        self,
        quest_state: NavigationState,
        device: DevicePosition,
    ) -> float:
        position_cov_diag = 0.0
        if quest_state.covariance and len(quest_state.covariance) >= 9:
            pos_cov = [
                max(quest_state.covariance[0], 0.0),
                max(quest_state.covariance[7], 0.0),
                max(quest_state.covariance[14], 0.0),
            ]
            position_cov_diag = sum(pos_cov) / 3.0

        total_uncertainty = math.sqrt(position_cov_diag + (1.0 - device.confidence) ** 2)
        confidence = max(0.0, min(1.0, 1.0 - total_uncertainty))

        return confidence
