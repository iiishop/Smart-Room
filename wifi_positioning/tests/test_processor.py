import asyncio
import math
import random

from wifi_positioning.positioning.models import RawPosition, RssiReading
from wifi_positioning.positioning.path_loss import PathLossModel, PathLossParams
from wifi_positioning.positioning.processor import RssiProcessor
from wifi_positioning.positioning.trilateration import TrilaterationEngine


def _distance_to_rssi(distance: float, ref_rssi: float = -40.0, n: float = 2.0) -> float:
    return ref_rssi - 10.0 * n * math.log10(distance)


def _make_ap_positions() -> dict[str, tuple[float, float]]:
    return {
        "ap1": (0.0, 0.0),
        "ap2": (8.0, 0.0),
        "ap3": (0.0, 8.0),
    }


def _make_readings_for_position(
    ap_positions: dict[str, tuple[float, float]],
    x: float,
    y: float,
    device_mac: str = "",
    noise_std: float = 0.0,
) -> list[RssiReading]:
    readings = []
    for bssid, (ax, ay) in ap_positions.items():
        dist = math.hypot(x - ax, y - ay)
        rssi = _distance_to_rssi(dist) + random.gauss(0.0, noise_std)
        readings.append(RssiReading(bssid=bssid, rssi=rssi, frequency=2412, device_mac=device_mac))
    return readings


def test_processor_single_device_outputs_smoothed() -> None:
    ap_positions = _make_ap_positions()
    path_loss = PathLossModel(PathLossParams(reference_rssi=-40.0, path_loss_exponent=2.0))
    trilat = TrilaterationEngine(ap_positions)
    processor = RssiProcessor(path_loss, trilat, dt=0.1)

    readings = _make_readings_for_position(ap_positions, 3.0, 2.0)
    results = asyncio.run(processor.process(readings))

    assert len(results) == 1
    assert results[0].source == "ekf"
    assert abs(results[0].x - 3.0) < 5.0
    assert abs(results[0].y - 2.0) < 5.0


def test_processor_multiple_frames_smooths_trajectory() -> None:
    random.seed(123)
    ap_positions = _make_ap_positions()
    path_loss = PathLossModel(PathLossParams(reference_rssi=-40.0, path_loss_exponent=2.0))
    trilat = TrilaterationEngine(ap_positions)
    processor = RssiProcessor(path_loss, trilat, dt=0.1)

    raw_errors: list[float] = []
    ekf_errors: list[float] = []

    tx, ty = 1.0, 1.0
    for _ in range(60):
        tx += 0.05
        ty += 0.025
        readings = _make_readings_for_position(ap_positions, tx, ty, noise_std=1.5)
        trilat_result = trilat.estimate_position(readings, path_loss)
        assert trilat_result is not None
        raw_errors.append(math.hypot(trilat_result.x - tx, trilat_result.y - ty))

        results = asyncio.run(processor.process(readings))
        ekf_errors.append(math.hypot(results[0].x - tx, results[0].y - ty))

    avg_raw = sum(raw_errors[-30:]) / 30.0
    avg_ekf = sum(ekf_errors[-30:]) / 30.0
    assert avg_raw > 0.1
    assert avg_ekf < avg_raw * 0.6


def test_processor_empty_readings_still_predicts() -> None:
    ap_positions = _make_ap_positions()
    path_loss = PathLossModel()
    trilat = TrilaterationEngine(ap_positions)
    processor = RssiProcessor(path_loss, trilat, dt=0.1)

    readings = _make_readings_for_position(ap_positions, 4.0, 3.0)
    asyncio.run(processor.process(readings))

    results = asyncio.run(processor.process([]))
    assert len(results) == 1
    assert results[0].source == "ekf"


async def _mock_collector_sleep() -> list[RssiReading]:
    await asyncio.sleep(0.01)
    return []


def test_processor_run_loop_stable() -> None:
    ap_positions = _make_ap_positions()
    path_loss = PathLossModel()
    trilat = TrilaterationEngine(ap_positions)
    processor = RssiProcessor(path_loss, trilat, dt=0.1, predict_interval=0.02)
    output_queue: asyncio.Queue = asyncio.Queue()

    async def _run():
        stop = asyncio.Event()

        async def _cancel():
            await asyncio.sleep(0.15)
            stop.set()

        async def _feed():
            await asyncio.sleep(0.02)
            readings = _make_readings_for_position(ap_positions, 2.0, 2.0)
            await processor.process(readings)

        await asyncio.gather(
            processor.run(_mock_collector_sleep, output_queue, stop),
            _feed(),
            _cancel(),
        )

    asyncio.run(_run())
    assert output_queue.qsize() >= 1


def test_processor_no_initial_tracker_creates_one() -> None:
    ap_positions = _make_ap_positions()
    path_loss = PathLossModel()
    trilat = TrilaterationEngine(ap_positions)
    processor = RssiProcessor(path_loss, trilat, dt=0.1)

    assert len(processor._trackers) == 0
    readings = _make_readings_for_position(ap_positions, 1.0, 1.0)
    asyncio.run(processor.process(readings))
    assert len(processor._trackers) == 1
