from wifi_positioning.positioning.models import RssiReading
from wifi_positioning.positioning.path_loss import PathLossModel, PathLossParams
from wifi_positioning.positioning.trilateration import TrilaterationEngine


def _distance_to_rssi(distance: float, ref_rssi: float = -40.0, n: float = 2.0) -> float:
    import math

    return ref_rssi - 10.0 * n * math.log10(distance)


def test_three_ap_position_accuracy_under_two_meters() -> None:
    ap_positions = {
        "ap1": (0.0, 0.0),
        "ap2": (8.0, 0.0),
        "ap3": (0.0, 8.0),
    }
    truth = (3.0, 2.0)
    readings = []
    for bssid, (x, y) in ap_positions.items():
        dist = ((truth[0] - x) ** 2 + (truth[1] - y) ** 2) ** 0.5
        readings.append(RssiReading(bssid=bssid, rssi=_distance_to_rssi(dist), frequency=2412))

    model = PathLossModel(PathLossParams(reference_distance=1.0, reference_rssi=-40.0, path_loss_exponent=2.0))
    engine = TrilaterationEngine(ap_positions)
    pos = engine.estimate_position(readings, model)

    assert pos is not None
    err = ((pos.x - truth[0]) ** 2 + (pos.y - truth[1]) ** 2) ** 0.5
    assert err < 2.0


def test_two_ap_degraded_mode_returns_position() -> None:
    ap_positions = {"ap1": (0.0, 0.0), "ap2": (6.0, 0.0)}
    readings = [
        RssiReading(bssid="ap1", rssi=-50.0, frequency=2412),
        RssiReading(bssid="ap2", rssi=-52.0, frequency=2412),
    ]
    model = PathLossModel()
    engine = TrilaterationEngine(ap_positions)
    pos = engine.estimate_position(readings, model)
    assert pos is not None
    assert pos.source == "two_ap"


def test_single_ap_degraded_mode_returns_distance_hint() -> None:
    ap_positions = {"ap1": (1.0, 1.0)}
    readings = [RssiReading(bssid="ap1", rssi=-65.0, frequency=2412)]
    model = PathLossModel()
    engine = TrilaterationEngine(ap_positions)
    pos = engine.estimate_position(readings, model)
    assert pos is not None
    assert pos.source == "single_ap"
    assert pos.estimated_distance is not None
