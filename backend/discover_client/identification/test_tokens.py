from discover_client.identification.tokens import extract_identity_tokens


def test_extracts_specific_device_tokens_but_not_generic_segments() -> None:
    tokens = extract_identity_tokens(
        "UCL/OPSEBO/206/2ByDoorToCommonArea2C2/LMS",
        {"serial": "C4DD573EB84C", "state": "ON"},
    )

    assert "2bydoortocommonarea2c2" in tokens
    assert "c4dd573eb84c" in tokens
    assert "opsebo" not in tokens
    assert "on" not in tokens


def test_does_not_treat_ordinary_payload_values_as_identity() -> None:
    tokens = extract_identity_tokens(
        "site/device/temperature",
        {
            "firmware": "release20260625",
            "state": "sharedstatus2026",
            "nested": {"measurement": "sensorvalue12345"},
        },
    )

    assert "release20260625" not in tokens
    assert "sharedstatus2026" not in tokens
    assert "sensorvalue12345" not in tokens


def test_sn_is_serial_only_when_scalar_not_tasmota_sensor_snapshot() -> None:
    assert "abc123456789" in extract_identity_tokens({"sn": "ABC123456789"})
    assert extract_identity_tokens(
        {
            "sn": {
                "Time": "2026-06-25T23:00:30",
                "ENERGY": {"TotalStartTime": "2022-11-16T16:28:08"},
            }
        }
    ) == set()
