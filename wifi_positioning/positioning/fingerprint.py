from __future__ import annotations

import json
from dataclasses import dataclass
from math import sqrt
from pathlib import Path


@dataclass(slots=True)
class FingerprintSample:
    location_name: str
    position: tuple[float, float]
    rssi_vector: dict[str, float]


class FingerprintDB:
    def __init__(self) -> None:
        self.samples: list[FingerprintSample] = []

    def add_sample(self, location_name: str, position: tuple[float, float], rssi_vector: dict[str, float]) -> None:
        self.samples.append(FingerprintSample(location_name=location_name, position=position, rssi_vector=rssi_vector))

    def match(self, rssi_vector: dict[str, float]) -> tuple[str | None, float]:
        if not self.samples:
            return None, 0.0

        keys = set(rssi_vector.keys())
        for sample in self.samples:
            keys.update(sample.rssi_vector.keys())

        key_list = sorted(keys)
        query = [float(rssi_vector.get(k, -100.0)) for k in key_list]

        best_name = None
        best_dist = float("inf")
        for sample in self.samples:
            vec = [float(sample.rssi_vector.get(k, -100.0)) for k in key_list]
            dist = sqrt(sum((a - b) ** 2 for a, b in zip(query, vec)))
            if dist < best_dist:
                best_dist = dist
                best_name = sample.location_name

        confidence = max(0.05, min(1.0, 1.0 / (1.0 + best_dist / 30.0)))
        return best_name, confidence

    def save(self, path: str | Path) -> None:
        payload = [
            {
                "location_name": s.location_name,
                "position": [s.position[0], s.position[1]],
                "rssi_vector": s.rssi_vector,
            }
            for s in self.samples
        ]
        target = Path(path)
        if target.suffix.lower() in {".yml", ".yaml"}:
            import yaml

            target.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
            return
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "FingerprintDB":
        target = Path(path)
        text = target.read_text(encoding="utf-8")
        if target.suffix.lower() in {".yml", ".yaml"}:
            import yaml

            payload = yaml.safe_load(text)
        else:
            payload = json.loads(text)

        db = cls()
        for item in payload or []:
            db.add_sample(
                location_name=item["location_name"],
                position=(float(item["position"][0]), float(item["position"][1])),
                rssi_vector={k: float(v) for k, v in item["rssi_vector"].items()},
            )
        return db
