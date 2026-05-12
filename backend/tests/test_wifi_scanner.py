import time

from backend.wifi_scanner import ScannerConfig, create_scanner
from backend.wifi_scanner.models import RSSISample
from backend.wifi_scanner.platform_linux import LinuxWifiScanner
from backend.wifi_scanner.platform_windows import WindowsWifiScanner


def test_linux_scan_parses_records(monkeypatch):
    config = ScannerConfig(interface="wlan0")
    scanner = LinuxWifiScanner(config)

    scan_output = """
BSS aa:bb:cc:dd:ee:ff(on wlan0)
\tsignal: -45.00 dBm
\tfreq: 2412
\tSSID: DemoWifi
BSS 11:22:33:44:55:66(on wlan0)
\tsignal: -62.00 dBm
\tfreq: 5180
\tSSID: Office5G
"""

    def fake_run(*_args, **_kwargs):
        class Result:
            returncode = 0
            stdout = scan_output
            stderr = ""

        return Result()

    monkeypatch.setattr("backend.wifi_scanner.platform_linux.subprocess.run", fake_run)
    samples = scanner.scan()

    assert len(samples) == 2
    assert samples[0].bssid == "aa:bb:cc:dd:ee:ff"
    assert samples[0].ssid == "DemoWifi"
    assert samples[0].rssi == -45
    assert samples[0].frequency == 2412


def test_windows_scan_parses_records(monkeypatch):
    config = ScannerConfig()
    scanner = WindowsWifiScanner(config)

    netsh_output = """
SSID 1 : DemoWifi
    BSSID 1                 : aa:bb:cc:dd:ee:ff
         Signal             : 88%
         Radio type         : 802.11n
         Channel            : 1

SSID 2 : Office5G
    BSSID 1                 : 11:22:33:44:55:66
         Signal             : 60%
         Radio type         : 802.11ac
         Channel            : 36
"""

    def fake_run(*_args, **_kwargs):
        class Result:
            returncode = 0
            stdout = netsh_output
            stderr = ""

        return Result()

    monkeypatch.setattr("backend.wifi_scanner.platform_windows.subprocess.run", fake_run)
    samples = scanner.scan()

    assert len(samples) == 2
    assert samples[0].bssid == "aa:bb:cc:dd:ee:ff"
    assert samples[0].ssid == "DemoWifi"
    assert -60 <= samples[0].rssi <= -50
    assert samples[0].frequency == 2412


def test_bssid_filter_applied(monkeypatch):
    config = ScannerConfig(filter_bssids={"aa:bb:cc:dd:ee:ff"})
    scanner = WindowsWifiScanner(config)

    netsh_output = """
SSID 1 : DemoWifi
    BSSID 1                 : aa:bb:cc:dd:ee:ff
         Signal             : 90%
         Channel            : 11

SSID 2 : Office5G
    BSSID 1                 : 11:22:33:44:55:66
         Signal             : 55%
         Channel            : 36
"""

    def fake_run(*_args, **_kwargs):
        class Result:
            returncode = 0
            stdout = netsh_output
            stderr = ""

        return Result()

    monkeypatch.setattr("backend.wifi_scanner.platform_windows.subprocess.run", fake_run)
    samples = scanner.scan()

    assert len(samples) == 1
    assert samples[0].bssid == "aa:bb:cc:dd:ee:ff"


def test_create_scanner_by_os(monkeypatch):
    monkeypatch.setattr("backend.wifi_scanner.factory.platform.system", lambda: "Linux")
    scanner = create_scanner(ScannerConfig(interface="wlan0"))
    assert isinstance(scanner, LinuxWifiScanner)

    monkeypatch.setattr("backend.wifi_scanner.factory.platform.system", lambda: "Windows")
    scanner = create_scanner(ScannerConfig())
    assert isinstance(scanner, WindowsWifiScanner)


def test_continuous_scan_invokes_callback(monkeypatch):
    calls = []

    class FakeLinuxScanner(LinuxWifiScanner):
        def scan(self):
            return [
                RSSISample(
                    bssid="aa:bb:cc:dd:ee:ff",
                    ssid="DemoWifi",
                    rssi=-40,
                    frequency=2412,
                    timestamp=time.time(),
                )
            ]

    scanner = FakeLinuxScanner(ScannerConfig(interface="wlan0"))

    def callback(samples):
        calls.append(samples)

    scanner.start_continuous(interval_sec=0.05, callback=callback)
    time.sleep(0.18)
    scanner.stop()

    assert len(calls) >= 2
