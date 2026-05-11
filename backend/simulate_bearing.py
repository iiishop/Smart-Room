"""
Simulation test for Task A4: Coordinate Alignment & Device Bearing Calculation.

Verifies:
1. Calibration accuracy with 1 and multiple reference points
2. Bearing computation against ground truth
3. Azimuth error <= 20 degrees under realistic conditions
"""
from __future__ import annotations

import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from localization import (
    BearingCalculator,
    CalibrationPoint,
    CoordinateAligner,
    DevicePosition,
    NavigationState,
    Pose,
    Quaternion,
    Vec3,
)
from localization.types import _quat_mul


def deg_to_rad(deg: float) -> float:
    return math.radians(deg)


def quat_from_yaw_pitch_roll(yaw: float, pitch: float, roll: float) -> Quaternion:
    q_yaw = Quaternion.from_yaw(yaw)
    half_p = pitch * 0.5
    q_pitch = Quaternion(float(math.sin(half_p)), 0.0, 0.0, float(math.cos(half_p)))
    half_r = roll * 0.5
    q_roll = Quaternion(0.0, 0.0, float(math.sin(half_r)), float(math.cos(half_r)))
    return _quat_mul(q_yaw, _quat_mul(q_pitch, q_roll))


def test_calibration_single_point():
    print("=== Test: Single-point calibration ===")
    aligner = CoordinateAligner()

    slam_pos = Vec3(1.0, 0.0, 2.0)
    world_pos = Vec3(3.0, 0.0, 5.0)
    heading = 45.0

    aligner.add_calibration_point(CalibrationPoint(
        slam_position=slam_pos,
        world_position=world_pos,
        slam_heading_deg=heading,
    ))
    result = aligner.calibrate()

    assert result.num_points == 1

    converted = aligner.slam_to_world(slam_pos)
    error = (converted - world_pos).norm()
    print(f"  Slam={slam_pos.to_tuple()} -> World={converted.to_tuple()}")
    print(f"  Expected={world_pos.to_tuple()}, Error={error:.6f} m")
    assert error < 0.01, f"Calibration error too large: {error}"

    roundtrip = aligner.world_to_slam(world_pos)
    rerror = (roundtrip - slam_pos).norm()
    print(f"  Round-trip error: {rerror:.6f} m")
    assert rerror < 0.01

    print("  PASSED\n")
    return True


def test_calibration_multi_point():
    print("=== Test: Multi-point calibration (3 points) ===")
    aligner = CoordinateAligner()

    points_data = [
        (Vec3(0.0, 0.0, 0.0), Vec3(5.0, 0.0, 10.0), 0.0),
        (Vec3(1.0, 0.0, 0.0), Vec3(6.0, 0.0, 10.0), 0.0),
        (Vec3(0.0, 0.0, 1.0), Vec3(5.0, 0.0, 11.0), 0.0),
    ]
    for slam, world, heading in points_data:
        aligner.add_calibration_point(CalibrationPoint(
            slam_position=slam,
            world_position=world,
            slam_heading_deg=heading,
        ))

    result = aligner.calibrate()
    print(f"  Translation: {result.translation.to_tuple()}")
    print(f"  Rotation yaw: {result.rotation_yaw_deg:.2f} deg")
    print(f"  Residual: {result.residual_m:.6f} m")

    assert result.num_points == 3
    assert result.residual_m < 0.01, f"Residual too large: {result.residual_m}"

    for slam, world, _ in points_data:
        converted = aligner.slam_to_world(slam)
        error = (converted - world).norm()
        assert error < 0.01, f"Point error too large: {error}"

    print("  PASSED\n")
    return True


def test_calibration_multi_point_with_noise():
    print("=== Test: Multi-point calibration with sensor noise === ")
    aligner = CoordinateAligner()

    import random
    random.seed(42)
    noise_std = 0.05

    true_translation = Vec3(5.0, 0.0, 10.0)

    def gauss():
        return random.gauss(0.0, noise_std)

    for i in range(5):
        slam = Vec3(float(i * 0.5), 0.0, float(i * 0.3))
        world = slam + true_translation
        world = Vec3(
            world.x + gauss(),
            world.y + gauss(),
            world.z + gauss(),
        )
        aligner.add_calibration_point(CalibrationPoint(
            slam_position=slam,
            world_position=world,
            slam_heading_deg=0.0,
        ))

    result = aligner.calibrate()
    print(f"  True translation: {true_translation.to_tuple()}")
    print(f"  Estimated: {result.translation.to_tuple()}")
    print(f"  Residual: {result.residual_m:.6f} m")

    assert result.residual_m < noise_std * 2, f"Residual too large with noise: {result.residual_m}"

    print("  PASSED\n")
    return True


def test_bearing_calculation_accuracy():
    print("=== Test: Bearing calculation accuracy (azimuth error <= 20 deg) ===")
    calculator = BearingCalculator()

    test_cases = [
        {
            "name": "Device directly ahead",
            "quest_pos": Vec3(0, 1.5, 0),
            "quest_yaw": 0.0,
            "device_pos": Vec3(0, 1.5, 5.0),
            "expected_az": 0.0,
            "expected_el": 0.0,
            "expected_dist": 5.0,
        },
        {
            "name": "Device 30 deg right",
            "quest_pos": Vec3(0, 1.5, 0),
            "quest_yaw": 0.0,
            "device_pos": Vec3(2.887, 1.5, 5.0),
            "expected_az": 30.0,
            "expected_el": 0.0,
            "expected_dist": math.sqrt(2.887**2 + 5.0**2),
        },
        {
            "name": "Device 45 deg left",
            "quest_pos": Vec3(0, 1.5, 0),
            "quest_yaw": 0.0,
            "device_pos": Vec3(-5.0, 1.5, 5.0),
            "expected_az": -45.0,
            "expected_el": 0.0,
            "expected_dist": math.sqrt(50.0),
        },
        {
            "name": "Device 90 deg right",
            "quest_pos": Vec3(0, 1.5, 0),
            "quest_yaw": 0.0,
            "device_pos": Vec3(3.0, 1.5, 0.0),
            "expected_az": 90.0,
            "expected_el": 0.0,
            "expected_dist": 3.0,
        },
        {
            "name": "Device behind (180 deg)",
            "quest_pos": Vec3(0, 1.5, 0),
            "quest_yaw": 0.0,
            "device_pos": Vec3(0, 1.5, -5.0),
            "expected_az": 180.0,
            "expected_el": 0.0,
            "expected_dist": 5.0,
        },
        {
            "name": "Device above (elevated)",
            "quest_pos": Vec3(0, 1.5, 0),
            "quest_yaw": 0.0,
            "device_pos": Vec3(0, 3.5, 5.0),
            "expected_az": 0.0,
            "expected_el": math.degrees(math.atan2(2.0, 5.0)),
            "expected_dist": math.sqrt(29.0),
        },
        {
            "name": "Device with quest rotated 30 deg left",
            "quest_pos": Vec3(2, 1.5, 3),
            "quest_yaw": 30.0,
            "device_pos": Vec3(2, 1.5, 8.0),
            "expected_az": -30.0,
            "expected_el": 0.0,
            "expected_dist": 5.0,
        },
        {
            "name": "Device far away",
            "quest_pos": Vec3(0, 1.5, 0),
            "quest_yaw": 0.0,
            "device_pos": Vec3(10.0, 1.5, 10.0),
            "expected_az": 45.0,
            "expected_el": 0.0,
            "expected_dist": math.sqrt(200.0),
        },
    ]

    max_az_error = 0.0
    max_el_error = 0.0
    max_dist_error = 0.0

    for tc in test_cases:
        quest_yaw_rad = deg_to_rad(tc["quest_yaw"])
        quest_state = NavigationState(
            device_id="quest3",
            timestamp_ms=1000,
            pose=Pose(
                position=tc["quest_pos"],
                orientation=quat_from_yaw_pitch_roll(quest_yaw_rad, 0.0, 0.0),
            ),
        )

        device = DevicePosition(
            device_id="device_1",
            world_position=tc["device_pos"],
            confidence=0.9,
        )

        bearing = calculator.compute_bearing(quest_state, device)

        az_error = abs(bearing.azimuth_deg - tc["expected_az"])
        el_error = abs(bearing.elevation_deg - tc["expected_el"])
        dist_error = abs(bearing.distance_m - tc["expected_dist"])

        if az_error > 180:
            az_error = 360 - az_error

        max_az_error = max(max_az_error, az_error)
        max_el_error = max(max_el_error, el_error)
        max_dist_error = max(max_dist_error, dist_error)

        status = "OK" if az_error <= 20.0 else "FAIL"
        print(f"  [{status}] {tc['name']}")
        print(f"    Expected: az={tc['expected_az']:.1f}, el={tc['expected_el']:.1f}, d={tc['expected_dist']:.2f}")
        print(f"    Got:      az={bearing.azimuth_deg:.1f}, el={bearing.elevation_deg:.1f}, d={bearing.distance_m:.2f}")
        print(f"    Errors:   az_err={az_error:.2f}, el_err={el_error:.2f}, dist_err={dist_error:.2f}")

        assert az_error <= 20.0, (
            f"Azimuth error {az_error:.2f} exceeds 20 deg threshold "
            f"for '{tc['name']}'"
        )

    print(f"\n  Max errors: az={max_az_error:.2f} deg, el={max_el_error:.2f} deg, dist={max_dist_error:.4f} m")
    assert max_az_error <= 20.0
    print("  PASSED\n")
    return True


def test_full_pipeline():
    print("=== Test: Full pipeline (calibrate -> track -> bearing) === ")
    aligner = CoordinateAligner()

    slam_pos = Vec3(0.0, 0.0, 0.0)
    world_pos = Vec3(5.0, 0.0, 10.0)
    aligner.add_calibration_point(CalibrationPoint(
        slam_position=slam_pos,
        world_position=world_pos,
        slam_heading_deg=0.0,
    ))
    aligner.calibrate()

    devices = [
        DevicePosition("device_A", Vec3(8.0, 1.0, 15.0), confidence=0.95),
        DevicePosition("device_B", Vec3(2.0, 1.0, 8.0), confidence=0.85),
        DevicePosition("device_C", Vec3(6.0, 1.5, 9.0), confidence=0.75),
    ]

    calculator = BearingCalculator()

    waypoints = [
        (Vec3(0.0, 1.6, 0.0), 0.0),
        (Vec3(1.0, 1.6, 1.0), 15.0),
        (Vec3(2.0, 1.6, 3.0), -30.0),
        (Vec3(3.0, 1.6, 2.0), 45.0),
    ]

    for i, (slam_pos_wp, heading) in enumerate(waypoints):
        world_quest_pos = aligner.slam_to_world(slam_pos_wp)
        quest_state = NavigationState(
            device_id="quest3",
            timestamp_ms=1000 + i * 100,
            pose=Pose(
                position=world_quest_pos,
                orientation=quat_from_yaw_pitch_roll(deg_to_rad(heading), 0.0, 0.0),
            ),
        )

        bearings = calculator.compute_bearings(quest_state, devices)
        print(f"  Waypoint {i}: quest at {world_quest_pos.to_tuple()}, heading={heading} deg")
        for b in bearings:
            print(f"    {b.device_id}: az={b.azimuth_deg:6.1f} deg, el={b.elevation_deg:5.1f} deg, dist={b.distance_m:.2f} m, conf={b.confidence:.2f}")

    print("  PASSED\n")
    return True


def test_mqtt_publisher():
    print("=== Test: MQTT Publisher topic format (no broker needed) === ")
    from localization import DeviceBearing, DevicePosition, MqttPublisher, Vec3

    publisher = MqttPublisher()

    assert publisher.POS_TOPIC == "/wifi/localization/{device_id}/pos"
    assert publisher.BEARING_TOPIC == "/wifi/localization/{device_id}/bearing"

    pos_topic = publisher.POS_TOPIC.format(device_id="test_device")
    bearing_topic = publisher.BEARING_TOPIC.format(device_id="test_device")
    assert pos_topic == "/wifi/localization/test_device/pos"
    assert bearing_topic == "/wifi/localization/test_device/bearing"

    assert not publisher.is_connected

    print("  Topic format verified OK")
    print("  PASSED\n")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("Task A4: Coordinate Alignment & Bearing Calculation - Test Suite")
    print("=" * 60)
    print()

    all_passed = True
    tests = [
        test_calibration_single_point,
        test_calibration_multi_point,
        test_calibration_multi_point_with_noise,
        test_bearing_calculation_accuracy,
        test_full_pipeline,
        test_mqtt_publisher,
    ]

    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"  FAILED: {e}\n")
            all_passed = False

    print("=" * 60)
    if all_passed:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
    print("=" * 60)

    sys.exit(0 if all_passed else 1)
