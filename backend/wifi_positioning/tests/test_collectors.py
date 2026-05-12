from __future__ import annotations

import asyncio
from pathlib import Path

import wifi_positioning.collectors.linux_collector as linux_module
import wifi_positioning.collectors.windows_collector as windows_module
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


def test_linux_collect_continues_after_failed_scan(monkeypatch) -> None:
    class _Result:
        def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
            self.returncode = returncode
            self._stdout = stdout.encode("utf-8")
            self._stderr = stderr.encode("utf-8")

        async def communicate(self):
            return self._stdout, self._stderr

    calls = [
        _Result(1, "", "temporary scan error"),
        _Result(0, (SAMPLES / "linux_iw_scan.txt").read_text(encoding="utf-8"), ""),
    ]

    async def _fake_exec(*_args, **_kwargs):
        return calls.pop(0)

    async def _fast_sleep(_seconds: float):
        return None

    monkeypatch.setattr(linux_module.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(linux_module.asyncio, "sleep", _fast_sleep)

    async def _run() -> list:
        collector = LinuxRssiCollector(scan_interval=0)
        results = []
        async for reading in collector.collect():
            results.append(reading)
            await collector.stop()
        return results

    readings = asyncio.run(_run())
    assert len(readings) >= 1
    assert readings[0].ap_bssid == "aa:bb:cc:dd:ee:ff"


def test_windows_collect_continues_after_failed_scan(monkeypatch) -> None:
    class _Result:
        def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
            self.returncode = returncode
            self._stdout = stdout.encode("utf-8")
            self._stderr = stderr.encode("utf-8")

        async def communicate(self):
            return self._stdout, self._stderr

    calls = [
        _Result(1, "", "temporary netsh error"),
        _Result(0, (SAMPLES / "windows_netsh_scan.txt").read_text(encoding="utf-8"), ""),
    ]

    async def _fake_exec(*_args, **_kwargs):
        return calls.pop(0)

    async def _fast_sleep(_seconds: float):
        return None

    monkeypatch.setattr(windows_module.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(windows_module.asyncio, "sleep", _fast_sleep)

    async def _run() -> list:
        collector = WindowsRssiCollector(scan_interval=0)
        results = []
        async for reading in collector.collect():
            results.append(reading)
            await collector.stop()
        return results

    readings = asyncio.run(_run())
    assert len(readings) >= 1
    assert readings[0].ap_bssid == "aa:bb:cc:dd:ee:ff"
