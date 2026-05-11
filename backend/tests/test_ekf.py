from __future__ import annotations

import math
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, r"D:\Smart-Room\backend")

from ekf import (
    EKFFusionEngine,
    EKFConfig,
    WiFiAPInfo,
    IMUSimulator,
    generate_ground_truth_trajectory,
    imu_predict,
    wifi_rssi_update,
    wifi_rtt_update,
    slam_prior_update,
    MQTTStatePublisher,
    NavigationState,
)


def _yaw_from_quat(q: np.ndarray) -> float:
    qw, qx, qy, qz = q
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(siny_cosp, cosy_cosp)


class TestIMUPrediction(unittest.TestCase):
    def setUp(self):
        self.config = EKFConfig()
        self.config.init_pos_std = 0.01
        self.config.init_vel_std = 0.01
        self.config.init_attitude_rad_std = 0.01
        self.engine = EKFFusionEngine(self.config)

    def test_prediction_moves_position(self):
        accel = np.array([1.0, 0.0, 0.0])
        gyro = np.array([0.0, 0.0, 0.0])
        initial_pos = self.engine.x[0:3].copy()
        self.engine.predict_imu(accel, gyro, 0.01)
        new_pos = self.engine.x[0:3]
        self.assertTrue(np.linalg.norm(new_pos - initial_pos) > 0.0)

    def test_prediction_integrates_velocity(self):
        accel = np.array([0.0, 0.0, self.config.gravity])
        gyro = np.array([0.0, 0.0, 0.0])
        initial_vel = self.engine.x[3:6].copy()
        self.engine.predict_imu(accel, gyro, 0.01)
        new_vel = self.engine.x[3:6]
        self.assertAlmostEqual(float(new_vel[0]), float(initial_vel[0]), delta=1e-6)

    def test_prediction_updates_quaternion(self):
        accel = np.array([0.0, 0.0, self.config.gravity])
        gyro = np.array([0.0, 0.0, 1.0])
        initial_yaw = _yaw_from_quat(self.engine.x[6:10])
        self.engine.predict_imu(accel, gyro, 0.1)
        new_yaw = _yaw_from_quat(self.engine.x[6:10])
        diff = abs(new_yaw - initial_yaw)
        self.assertLess(abs(diff - 0.1), 0.05)

    def test_prediction_latency(self):
        accel = np.array([0.1, 0.2, self.config.gravity + 0.3])
        gyro = np.array([0.01, 0.02, 0.03])
        times = []
        for _ in range(100):
            t0 = time.perf_counter()
            self.engine.predict_imu(accel, gyro, 0.005)
            times.append(time.perf_counter() - t0)
        avg_ms = sum(times) / len(times) * 1000
        self.assertLess(avg_ms, 20.0,
                        f"Prediction latency {avg_ms:.2f}ms exceeds 20ms limit")


class TestWiFiObservation(unittest.TestCase):
    def setUp(self):
        self.engine = EKFFusionEngine(EKFConfig())
        self.engine.x[0:3] = np.array([1.0, 0.0, 1.5])
        self.ap = np.array([0.0, 0.0, 1.0])

    def test_rssi_update_reduces_position_uncertainty(self):
        P_before = np.trace(self.engine.P[0:3, 0:3])
        wifi_rssi_update(self.engine, self.ap, -40.0, -30.0, 1.0, 2.0, 3.0)
        P_after = np.trace(self.engine.P[0:3, 0:3])
        self.assertLess(P_after, P_before)

    def test_rtt_update_reduces_position_uncertainty(self):
        P_before = np.trace(self.engine.P[0:3, 0:3])
        wifi_rtt_update(self.engine, self.ap, 1.118, 0.5)
        P_after = np.trace(self.engine.P[0:3, 0:3])
        self.assertLess(P_after, P_before)

    def test_wifi_update_latency(self):
        times = []
        for _ in range(100):
            t0 = time.perf_counter()
            wifi_rssi_update(
                self.engine,
                np.array([0.0, 0.0, 1.0]),
                -40.0,
                -30.0, 1.0, 2.0, 3.0,
            )
            times.append(time.perf_counter() - t0)
        avg_ms = sum(times) / len(times) * 1000
        self.assertLess(avg_ms, 5.0,
                        f"WiFi update latency {avg_ms:.2f}ms exceeds 5ms limit")


class TestSLAMPrior(unittest.TestCase):
    def setUp(self):
        self.engine = EKFFusionEngine(EKFConfig())
        self.engine.x[0:3] = np.array([5.0, 5.0, 1.5])

    def test_slam_prior_corrects_position(self):
        slam_pos = np.array([5.1, 5.1, 1.6])
        slam_quat = np.array([1.0, 0.0, 0.0, 0.0])
        slam_prior_update(self.engine, slam_pos, slam_quat, 0.05, 0.05)
        corrected = self.engine.x[0:3]
        self.assertLess(np.linalg.norm(corrected - np.array([5.0, 5.0, 1.5])), 0.2)


class TestEndToEndSimulation(unittest.TestCase):
    def setUp(self):
        self.config = EKFConfig()
        self.config.accel_noise_std = 0.05
        self.config.gyro_noise_std = 0.001
        self.config.init_pos_std = 0.1
        self.config.init_vel_std = 0.01
        self.config.init_attitude_rad_std = 0.01

    def test_position_drift_with_slam(self):
        duration = 30.0
        imu_freq = 200.0

        traj = generate_ground_truth_trajectory(duration, imu_freq)
        sim = IMUSimulator(
            accel_noise_std=self.config.accel_noise_std,
            gyro_noise_std=self.config.gyro_noise_std,
        )

        aps = [
            WiFiAPInfo("ap1", -3.0, -3.0, 1.5),
            WiFiAPInfo("ap2", 3.0, -3.0, 1.5),
            WiFiAPInfo("ap3", 0.0, 3.0, 1.5),
            WiFiAPInfo("ap4", -3.0, 3.0, 1.5),
            WiFiAPInfo("ap5", 3.0, 3.0, 1.5),
        ]

        engine = EKFFusionEngine(self.config)
        engine.x[0:3] = traj[0].pos.copy()
        engine.x[6:10] = traj[0].quat.copy()
        P_init = np.eye(15) * 0.01
        np.fill_diagonal(P_init[0:3, 0:3], 0.1**2)
        np.fill_diagonal(P_init[3:6, 3:6], 0.01**2)
        np.fill_diagonal(P_init[6:9, 6:9], 0.01**2)
        engine.P = P_init

        dt = 1.0 / imu_freq
        max_pos_error = 0.0
        final_pos_error = 0.0

        for i, pt in enumerate(traj):
            imu = sim.simulate_imu(pt)
            engine.predict_imu(
                np.array([imu.accel_x, imu.accel_y, imu.accel_z]),
                np.array([imu.gyro_x, imu.gyro_y, imu.gyro_z]),
                dt,
            )

            if i % 7 == 0:
                slam = sim.simulate_slam(pt, pos_std=0.02, orient_std_rad=0.015)
                slam_prior_update(
                    engine,
                    np.array([slam.position_x, slam.position_y, slam.position_z]),
                    np.array([slam.quat_w, slam.quat_x, slam.quat_y, slam.quat_z]),
                    slam.position_std,
                    slam.orientation_std_rad,
                )

            if i % 80 == 0:
                rssi = sim.simulate_wifi_rssi(pt, aps[0])
                wifi_rssi_update(
                    engine,
                    np.array([aps[0].position_x, aps[0].position_y, aps[0].position_z]),
                    rssi.rssi,
                    aps[0].reference_rssi,
                    aps[0].reference_distance,
                    aps[0].path_loss_exponent,
                    aps[0].rssi_std,
                )

            if i % 200 == 0:
                rtt = sim.simulate_wifi_rtt(pt, aps[0])
                wifi_rtt_update(
                    engine,
                    np.array([aps[0].position_x, aps[0].position_y, aps[0].position_z]),
                    rtt.distance_m,
                    aps[0].rtt_std,
                )

            pos_error = float(np.linalg.norm(engine.x[0:3] - pt.pos))
            max_pos_error = max(max_pos_error, pos_error)
            if i == len(traj) - 1:
                final_pos_error = pos_error

        self.assertLess(final_pos_error, 1.0,
                        f"Final position error {final_pos_error:.3f}m exceeds 1m limit")
        self.assertLess(max_pos_error, 2.0,
                        f"Max position error {max_pos_error:.3f}m too large")

    def test_azimuth_error_with_slam(self):
        duration = 30.0
        imu_freq = 200.0

        traj = generate_ground_truth_trajectory(duration, imu_freq)
        sim = IMUSimulator()

        aps = [
            WiFiAPInfo("ap1", -3.0, -3.0, 1.5),
            WiFiAPInfo("ap2", 3.0, -3.0, 1.5),
        ]

        engine = EKFFusionEngine(self.config)
        engine.x[0:3] = traj[0].pos.copy()
        engine.x[6:10] = traj[0].quat.copy()

        dt = 1.0 / imu_freq
        max_azimuth_error_deg = 0.0

        for i, pt in enumerate(traj):
            imu = sim.simulate_imu(pt)
            engine.predict_imu(
                np.array([imu.accel_x, imu.accel_y, imu.accel_z]),
                np.array([imu.gyro_x, imu.gyro_y, imu.gyro_z]),
                dt,
            )

            if i % 7 == 0:
                slam = sim.simulate_slam(pt, pos_std=0.02, orient_std_rad=0.015)
                slam_prior_update(
                    engine,
                    np.array([slam.position_x, slam.position_y, slam.position_z]),
                    np.array([slam.quat_w, slam.quat_x, slam.quat_y, slam.quat_z]),
                    slam.position_std,
                    slam.orientation_std_rad,
                )

            if i % 80 == 0:
                for ap in aps:
                    rssi = sim.simulate_wifi_rssi(pt, ap)
                    wifi_rssi_update(
                        engine,
                        np.array([ap.position_x, ap.position_y, ap.position_z]),
                        rssi.rssi,
                        ap.reference_rssi,
                        ap.reference_distance,
                        ap.path_loss_exponent,
                        ap.rssi_std,
                    )

            est_yaw = math.degrees(_yaw_from_quat(engine.x[6:10]))
            gt_yaw = math.degrees(_yaw_from_quat(pt.quat))

            yaw_err = abs(est_yaw - gt_yaw)
            if yaw_err > 180:
                yaw_err = 360 - yaw_err
            max_azimuth_error_deg = max(max_azimuth_error_deg, yaw_err)

        self.assertLess(max_azimuth_error_deg, 20.0,
                        f"Max azimuth error {max_azimuth_error_deg:.2f}deg exceeds 20deg limit")

    def test_imu_only_drifts_without_slam(self):
        duration = 10.0
        imu_freq = 200.0

        traj = generate_ground_truth_trajectory(duration, imu_freq)
        sim = IMUSimulator(accel_noise_std=0.01, gyro_noise_std=0.0001)

        engine = EKFFusionEngine(self.config)
        engine.x[0:3] = traj[0].pos.copy()
        engine.x[6:10] = traj[0].quat.copy()

        dt = 1.0 / imu_freq
        for pt in traj:
            imu = sim.simulate_imu(pt)
            engine.predict_imu(
                np.array([imu.accel_x, imu.accel_y, imu.accel_z]),
                np.array([imu.gyro_x, imu.gyro_y, imu.gyro_z]),
                dt,
            )

        pos_error = float(np.linalg.norm(engine.x[0:3] - traj[-1].pos))
        self.assertGreater(pos_error, 0.5,
                           f"IMU-only should show drift, but error is only {pos_error:.3f}m")


class TestMQTTPublisher(unittest.TestCase):
    def test_publish_calls_callback(self):
        published: list[tuple[str, str]] = []

        def cb(topic, payload):
            published.append((topic, payload))

        pub = MQTTStatePublisher(topic="/localization/state")
        pub.set_publish_callback(cb)

        state = NavigationState(
            position_x=1.0, position_y=2.0, position_z=3.0,
            velocity_x=0.1, velocity_y=0.2, velocity_z=0.0,
            quat_w=1.0, quat_x=0.0, quat_y=0.0, quat_z=0.0,
            timestamp_s=123456.0,
        )
        pub.publish(state)

        self.assertEqual(len(published), 1)
        self.assertEqual(published[0][0], "/localization/state")
        payload = eval(published[0][1])
        self.assertAlmostEqual(payload["x"], 1.0)
        self.assertAlmostEqual(payload["y"], 2.0)
        self.assertAlmostEqual(payload["z"], 3.0)

    def test_get_latest_state(self):
        pub = MQTTStatePublisher()
        self.assertIsNone(pub.get_latest_state())
        state = NavigationState(position_x=5.0, position_y=5.0, position_z=5.0)
        pub.publish(state)
        latest = pub.get_latest_state()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.position_x, 5.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
