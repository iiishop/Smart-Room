from __future__ import annotations

import json
import threading
import time
from typing import Optional, Callable

from .types import NavigationState


class MQTTStatePublisher:
    def __init__(
        self,
        topic: str = "/localization/state",
        publish_interval_s: float = 0.05,
    ):
        self.topic = topic
        self.publish_interval_s = publish_interval_s
        self._mock_publish_callback: Optional[Callable[[str, str], None]] = None
        self._latest_state: Optional[NavigationState] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def set_publish_callback(self, cb: Callable[[str, str], None]) -> None:
        self._mock_publish_callback = cb

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def publish(self, state: NavigationState) -> None:
        with self._lock:
            self._latest_state = state

        payload = json.dumps(state.to_json())
        if self._mock_publish_callback:
            self._mock_publish_callback(self.topic, payload)

    def _loop(self) -> None:
        while self._running:
            time.sleep(self.publish_interval_s)

    def get_latest_state(self) -> Optional[NavigationState]:
        with self._lock:
            return self._latest_state
