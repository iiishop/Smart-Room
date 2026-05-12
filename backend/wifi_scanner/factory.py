import platform

from .base import BaseWifiScanner
from .config import ScannerConfig
from .platform_linux import LinuxWifiScanner
from .platform_windows import WindowsWifiScanner


def create_scanner(config: ScannerConfig) -> BaseWifiScanner:
    os_name = platform.system().lower()
    if os_name == "linux":
        return LinuxWifiScanner(config)
    if os_name == "windows":
        return WindowsWifiScanner(config)
    if os_name == "android":
        return LinuxWifiScanner(config)
    raise NotImplementedError(f"Unsupported platform: {os_name}")
