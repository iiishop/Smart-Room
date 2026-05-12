from __future__ import annotations


class ZoneManager:
    def __init__(self, zones_config: dict[str, list[tuple[float, float]]]):
        self._zones = {name: list(points) for name, points in zones_config.items()}

    def get_zone_bounds(self, name: str) -> list[tuple[float, float]]:
        return list(self._zones[name])

    def classify(self, x: float, y: float) -> str | None:
        for name, polygon in self._zones.items():
            if self._contains_point(polygon, x, y):
                return name
        return None

    @staticmethod
    def _contains_point(polygon: list[tuple[float, float]], x: float, y: float) -> bool:
        inside = False
        count = len(polygon)
        if count < 3:
            return False

        for i in range(count):
            x1, y1 = polygon[i]
            x2, y2 = polygon[(i + 1) % count]

            if ZoneManager._point_on_segment(x, y, x1, y1, x2, y2):
                return True

            intersects = (y1 > y) != (y2 > y)
            if intersects:
                x_cross = (x2 - x1) * (y - y1) / (y2 - y1) + x1
                if x < x_cross:
                    inside = not inside

        return inside

    @staticmethod
    def _point_on_segment(
        px: float,
        py: float,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> bool:
        cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
        if abs(cross) > 1e-9:
            return False
        dot = (px - x1) * (px - x2) + (py - y1) * (py - y2)
        return dot <= 0
