from wifi_positioning.positioning.path_loss import PathLossModel, PathLossParams


def test_rssi_to_distance_matches_log_distance_model() -> None:
    model = PathLossModel(PathLossParams(reference_distance=1.0, reference_rssi=-40.0, path_loss_exponent=2.0))
    distance, confidence = model.rssi_to_distance(-60.0, 2412)
    assert abs(distance - 10.0) < 0.25
    assert 0.2 <= confidence <= 1.0


def test_calibration_fits_reference_and_path_loss_exponent() -> None:
    # RSSI = -42 - 10 * 2.2 * log10(d)
    samples = [
        (1.0, -42.0),
        (2.0, -48.62),
        (4.0, -55.24),
        (8.0, -61.86),
    ]
    model = PathLossModel(PathLossParams(reference_distance=1.0, reference_rssi=-38.0, path_loss_exponent=2.0))
    params = model.calibrate(samples)
    assert abs(params.reference_rssi - (-42.0)) < 1.0
    assert abs(params.path_loss_exponent - 2.2) < 0.2
