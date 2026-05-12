from __future__ import annotations

import asyncio
from pathlib import Path

from wifi_positioning.collectors.linux_collector import LinuxRssiCollector
from wifi_positioning.collectors.mock_collector import MockRssiCollector
from wifi_positioning.collectors.windows_collector import WindowsRssiCollector


SAMPLES = Path(__file__).parent / "samples"


def test_linux_parser() -> None:
    content = (SAMPLES / "linux_iw_scan.txt").read_text(encoding="utf-8")
    readings = LinuxRssiCollector.parse_iw_output(content)

    assert len(readings) == 2
    assert readings[0].ap_bssid == "aa:bb:cc:dd:ee:ff"
    assert readings[0].frequency == 2412
    assert readings[0].rssi == -42.0
    assert readings[1].ap_bssid == "11:22:33:44:55:66"
    assert readings[1].frequency == 5180
    assert readings[1].rssi == -61.5


def test_windows_parser() -> None:
    content = (SAMPLES / "windows_netsh_scan.txt").read_text(encoding="utf-8")
    readings = WindowsRssiCollector.parse_netsh_output(content)

    assert len(readings) == 2
    assert readings[0].ap_bssid == "aa:bb:cc:dd:ee:ff"
    assert readings[0].rssi == -61.0
    assert readings[1].ap_bssid == "11:22:33:44:55:66"
    assert readings[1].rssi == -78.0


def test_mock_collector_replay_order() -> None:
    async def _run() -> list:
        collector = MockRssiCollector(SAMPLES / "mock_readings.json", playback_speed=1000.0)
        results = []
        async for reading in collector.collect():
            results.append(reading)
        return results

    readings = asyncio.run(_run())
    assert len(readings) == 2
    assert readings[0].timestamp <= readings[1].timestamp
    assert readings[0].ap_bssid == "aa:bb:cc:dd:ee:ff"
    assert readings[1].ap_bssid == "11:22:33:44:55:66"
