import math
import random

from wifi_positioning.positioning.ekf_tracker import EKFTracker
from wifi_positioning.positioning.models import RawPosition


def test_ekf_predict_only_moves_position_forward() -> None:
    tracker = EKFTracker(dt=1.0)
    tracker._initialized = True
    tracker._x[0, 0] = 0.0
    tracker._x[1, 0] = 0.0
    tracker._x[2, 0] = 1.0
    tracker._x[3, 0] = 0.5

    state = tracker.predict(timestamp=1.0)
    assert abs(state.x - 1.0) < 0.2
    assert abs(state.y - 0.5) < 0.2
    assert abs(state.vx - 1.0) < 0.2
    assert abs(state.vy - 0.5) < 0.2


def test_ekf_update_reduces_covariance() -> None:
    tracker = EKFTracker(dt=1.0, measurement_noise=0.1, initial_covariance=10.0)
    tracker._initialized = True
    tracker._x[0, 0] = 0.0
    tracker._x[1, 0] = 0.0

    init_cov = float(tracker._P[0, 0])

    tracker.update(RawPosition(x=1.0, y=0.5, confidence=0.9), timestamp=1.0)
    tracker.update(RawPosition(x=2.0, y=1.0, confidence=0.9), timestamp=2.0)

    assert tracker._P[0, 0] < init_cov


def test_ekf_missing_measurements_recovery() -> None:
    tracker = EKFTracker(dt=0.1, measurement_noise=0.5, process_noise=0.01)
    tracker.update(RawPosition(x=5.0, y=10.0, confidence=0.9), timestamp=0.0)

    for _ in range(5):
        state = tracker.predict()

    assert state.confidence < 0.6
    assert tracker._missed_updates >= 5

    tracker.update(RawPosition(x=5.1, y=10.1, confidence=0.9), timestamp=0.6)
    state2 = tracker.get_state()
    assert state2.confidence > 0.4
    assert tracker._missed_updates == 0


def test_ekf_smoothing_reduces_noise() -> None:
    random.seed(42)
    tracker = EKFTracker(dt=0.1, measurement_noise=0.5, process_noise=0.001)

    raw_errors: list[float] = []
    ekf_errors: list[float] = []
    t = 0.0
    tx = 0.0
    ty = 0.0
    for _ in range(80):
        tx += 0.1
        ty += 0.05
        t += 0.1
        noisy_x = tx + random.gauss(0.0, 0.3)
        noisy_y = ty + random.gauss(0.0, 0.3)
        raw = RawPosition(x=noisy_x, y=noisy_y, confidence=0.9)
        raw_err = math.hypot(noisy_x - tx, noisy_y - ty)
        raw_errors.append(raw_err)

        smoothed = tracker.update(raw, timestamp=t)
        ekf_err = math.hypot(smoothed.x - tx, smoothed.y - ty)
        ekf_errors.append(ekf_err)

    ekf_mean = sum(ekf_errors[-40:]) / 40.0
    raw_mean = sum(raw_errors[-40:]) / 40.0
    assert ekf_mean < raw_mean * 0.6


def test_ekf_uninitialized_first_update_sets_state() -> None:
    tracker = EKFTracker(dt=0.1)
    state = tracker.update(RawPosition(x=7.0, y=3.0, confidence=0.8), timestamp=1.0)

    assert state.x == 7.0
    assert state.y == 3.0
    assert state.vx == 0.0
    assert state.vy == 0.0


def test_ekf_confidence_drops_with_missed_updates() -> None:
    tracker = EKFTracker(dt=0.1)
    tracker.update(RawPosition(x=0.0, y=0.0, confidence=0.9))
    c1 = tracker.get_state().confidence

    for _ in range(10):
        tracker.predict()

    c2 = tracker.get_state().confidence
    assert c2 < c1


def test_ekf_reset_clears_state() -> None:
    tracker = EKFTracker(dt=0.1)
    tracker.update(RawPosition(x=3.0, y=4.0, confidence=0.9))
    tracker.reset()

    state = tracker.get_state()
    assert state.x == 0.0
    assert state.y == 0.0
    assert not tracker._initialized
