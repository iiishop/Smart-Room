import threading
import time
from collections.abc import Callable

from .config import ScannerConfig
from .models import RSSISample


class BaseWifiScanner:
    def __init__(self, config: ScannerConfig):
        self.config = config
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def scan(self) -> list[RSSISample]:
        raise NotImplementedError

    def _apply_bssid_filter(self, samples: list[RSSISample]) -> list[RSSISample]:
        if not self.config.filter_bssids:
            return samples
        allowed = {bssid.lower() for bssid in self.config.filter_bssids}
        return [sample for sample in samples if sample.bssid.lower() in allowed]

    def start_continuous(
        self,
        interval_sec: float,
        callback: Callable[[list[RSSISample]], None],
    ) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()

        def worker() -> None:
            while not self._stop_event.is_set():
                started = time.monotonic()
                samples = self.scan()
                callback(samples)
                elapsed = time.monotonic() - started
                wait_time = max(0.0, interval_sec - elapsed)
                self._stop_event.wait(wait_time)

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
