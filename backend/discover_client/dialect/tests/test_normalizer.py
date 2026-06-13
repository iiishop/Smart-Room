"""Tests for dialect key normalizer."""

from discover_client.dialect.normalizer import to_canonical, to_dialect


def test_to_canonical_zigbee2mqtt_state_to_power() -> None:
    """zigbee2mqtt 'state' maps to canonical 'power'."""
    assert to_canonical("zigbee2mqtt", "state") == "power"


def test_to_canonical_tasmota_POWER_to_power() -> None:
    """Tasmota 'POWER' (uppercase) maps to canonical 'power'."""
    assert to_canonical("tasmota", "POWER") == "power"


def test_to_canonical_flatdict_power_passthrough() -> None:
    """Flatdict has empty mapping — falls through to lowercase key."""
    assert to_canonical("flatdict", "power") == "power"


def test_to_dialect_zigbee2mqtt_power_to_state() -> None:
    """Reverse lookup: canonical 'power' → zigbee2mqtt 'state'."""
    assert to_dialect("zigbee2mqtt", "power") == "state"


def test_to_dialect_flatdict_power_fallback() -> None:
    """Reverse lookup with empty mapping falls back to canonical key."""
    assert to_dialect("flatdict", "power") == "power"
