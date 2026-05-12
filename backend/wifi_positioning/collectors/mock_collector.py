from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from wifi_positioning.collectors.base import RssiCollector
from wifi_positioning.models import RssiReading, RssiSource


class MockRssiCollector(RssiCollector):
    def __init__(self, data_file: str | Path, playback_speed: float = 1.0) -> None:
        self.data_file = Path(data_file)
        self.playback_speed = max(playback_speed, 0.0001)
        self._running = False
        self._readings = self._load_data()

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def collect(self):
        await self.start()
        prev_ts: datetime | None = None

        for reading in self._readings:
            if not self._running:
                break

            if prev_ts is not None:
                delta = (reading.timestamp - prev_ts).total_seconds()
                if delta > 0:
                    await asyncio.sleep(delta / self.playback_speed)

            yield reading
            prev_ts = reading.timestamp

    async def list_aps(self) -> list[str]:
        return sorted({r.ap_bssid for r in self._readings})

    def _load_data(self) -> list[RssiReading]:
        suffix = self.data_file.suffix.lower()
        if suffix == ".json":
            return self._load_json()
        if suffix == ".csv":
            return self._load_csv()
        raise ValueError(f"Unsupported mock data format: {suffix}")

    def _load_json(self) -> list[RssiReading]:
        rows = json.loads(self.data_file.read_text(encoding="utf-8"))
        readings = [self._row_to_reading(r) for r in rows]
        return sorted(readings, key=lambda r: r.timestamp)

    def _load_csv(self) -> list[RssiReading]:
        with self.data_file.open("r", encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            readings = [self._row_to_reading(row) for row in reader]
        return sorted(readings, key=lambda r: r.timestamp)

    @staticmethod
    def _row_to_reading(row: dict) -> RssiReading:
        ts = datetime.fromisoformat(str(row["timestamp"]))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        source = RssiSource(str(row.get("source", RssiSource.AP_MONITOR.value)))
        frequency = row.get("frequency")
        return RssiReading(
            source=source,
            ap_bssid=str(row["ap_bssid"]).lower(),
            device_mac=row.get("device_mac"),
            rssi=float(row["rssi"]),
            frequency=int(frequency) if frequency not in (None, "") else None,
            timestamp=ts,
        )
