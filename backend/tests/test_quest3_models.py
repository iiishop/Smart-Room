from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from quest3_datasource.models import Quest3WiFiAccessPoint, Quest3WiFiData


def test_wifi_data_to_dict_contains_required_fields() -> None:
    data = Quest3WiFiData.now(
        device_id="quest3-1",
        scan_results=[
            Quest3WiFiAccessPoint(
                bssid="aa:bb:cc:dd:ee:ff",
                ssid="lab-ap",
                rssi=-55,
                frequency=5180,
            )
        ],
        rtt_available=True,
    )

    payload = data.to_dict()
    assert payload["device_id"] == "quest3-1"
    assert isinstance(payload["timestamp"], str)
    assert payload["rtt_available"] is True
    assert payload["scan_results"][0]["bssid"] == "aa:bb:cc:dd:ee:ff"
    assert payload["scan_results"][0]["ssid"] == "lab-ap"
    assert payload["scan_results"][0]["rssi"] == -55
    assert payload["scan_results"][0]["frequency"] == 5180
