from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from .ekf_tracker import EKFTracker
from .models import RssiReading, SmoothedPosition
from .path_loss import PathLossModel
from .trilateration import TrilaterationEngine


class RssiProcessor:
    """Coordinates trilateration → EKF pipeline grouped by device MAC."""

    def __init__(
        self,
        path_loss: PathLossModel,
        trilateration: TrilaterationEngine,
        dt: float = 0.1,
        predict_interval: float = 0.1,
    ) -> None:
        self._path_loss = path_loss
        self._trilateration = trilateration
        self._dt = dt
        self._predict_interval = predict_interval
        self._trackers: dict[str, EKFTracker] = {}

    async def process(self, readings: list[RssiReading]) -> list[SmoothedPosition]:
        results: list[SmoothedPosition] = []
        now = time.monotonic()

        grouped: dict[str, list[RssiReading]] = defaultdict(list)
        for r in readings:
            key = r.device_mac if r.device_mac else "__default__"
            grouped[key].append(r)

        active_devices: set[str] = set()

        for device_mac, dev_readings in grouped.items():
            active_devices.add(device_mac)
            raw = self._trilateration.estimate_position(dev_readings, self._path_loss)
            if raw is None:
                continue

            tracker = self._trackers.get(device_mac)
            if tracker is None:
                tracker = EKFTracker(dt=self._dt)
                self._trackers[device_mac] = tracker

            smoothed = tracker.update(raw, timestamp=now)
            results.append(smoothed)

        for device_mac, tracker in list(self._trackers.items()):
            if device_mac not in active_devices:
                smoothed = tracker.predict(timestamp=now)
                results.append(smoothed)

        if not readings and not results:
            for tracker in self._trackers.values():
                pred = tracker.predict(timestamp=now)
                results.append(pred)

        return results

    async def run(
        self,
        collector,
        output_queue: asyncio.Queue,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        if stop_event is None:
            stop_event = asyncio.Event()

        while not stop_event.is_set():
            try:
                readings = await asyncio.wait_for(collector(), timeout=self._predict_interval)
            except asyncio.TimeoutError:
                readings = []

            results = await self.process(readings)
            for r in results:
                await output_queue.put(r)

    async def run_with_readings(
        self,
        readings_queue: asyncio.Queue,
        output_queue: asyncio.Queue,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        if stop_event is None:
            stop_event = asyncio.Event()

        while not stop_event.is_set():
            batch: list[RssiReading] = []

            try:
                while True:
                    reading = readings_queue.get_nowait()
                    batch.append(reading)
            except asyncio.QueueEmpty:
                pass

            results = await self.process(batch)
            for r in results:
                await output_queue.put(r)

            await asyncio.sleep(self._predict_interval)
