from __future__ import annotations

from math import atan2, degrees, hypot, sqrt

from .models import RawPosition, RssiReading
from .path_loss import PathLossModel


class TrilaterationEngine:
    def __init__(self, ap_positions: dict[str, tuple[float, float]]) -> None:
        self.ap_positions = ap_positions

    def estimate_position(self, readings: list[RssiReading], path_loss: PathLossModel) -> RawPosition | None:
        usable: list[tuple[tuple[float, float], float, float]] = []
        for reading in readings:
            if reading.bssid not in self.ap_positions:
                continue
            distance, confidence = path_loss.rssi_to_distance(reading.rssi, reading.frequency)
            usable.append((self.ap_positions[reading.bssid], distance, confidence))

        if not usable:
            return None

        if len(usable) == 1:
            (ax, ay), distance, conf = usable[0]
            return RawPosition(
                x=float(ax),
                y=float(ay),
                confidence=conf * 0.35,
                direction=0.0,
                source="single_ap",
                estimated_distance=distance,
            )

        if len(usable) == 2:
            return self._estimate_two_ap(usable)

        return self._estimate_weighted_least_squares(usable)

    def estimate_direction(self, position: RawPosition, readings: list[RssiReading]) -> float:
        total_x = 0.0
        total_y = 0.0
        total_w = 0.0

        for reading in readings:
            ap = self.ap_positions.get(reading.bssid)
            if ap is None:
                continue
            dx = ap[0] - position.x
            dy = ap[1] - position.y
            norm = hypot(dx, dy)
            if norm == 0:
                continue
            weight = max(1.0, 100.0 + reading.rssi)
            total_x += (dx / norm) * weight
            total_y += (dy / norm) * weight
            total_w += weight

        if total_w == 0.0:
            return 0.0

        angle = degrees(atan2(total_y, total_x))
        return (angle + 360.0) % 360.0

    def _estimate_two_ap(self, usable: list[tuple[tuple[float, float], float, float]]) -> RawPosition:
        (x0, y0), d0, c0 = usable[0]
        (x1, y1), d1, c1 = usable[1]
        dx = x1 - x0
        dy = y1 - y0
        base = float(hypot(dx, dy))

        if base == 0.0:
            return RawPosition(x=x0, y=y0, confidence=min(c0, c1) * 0.4, source="two_ap")

        a = (d0**2 - d1**2 + base**2) / (2.0 * base)
        h_sq = max(d0**2 - a**2, 0.0)
        h = float(sqrt(h_sq))

        xm = x0 + a * dx / base
        ym = y0 + a * dy / base

        rx = -dy * (h / base)
        ry = dx * (h / base)
        p1 = (xm + rx, ym + ry)
        p2 = (xm - rx, ym - ry)

        stronger_idx = 0 if c0 >= c1 else 1
        ref = (x0, y0) if stronger_idx == 0 else (x1, y1)
        d_p1 = hypot(p1[0] - ref[0], p1[1] - ref[1])
        d_p2 = hypot(p2[0] - ref[0], p2[1] - ref[1])
        chosen = p1 if d_p1 <= d_p2 else p2

        return RawPosition(
            x=float(chosen[0]),
            y=float(chosen[1]),
            confidence=min(c0, c1) * 0.6,
            source="two_ap",
        )

    def _estimate_weighted_least_squares(self, usable: list[tuple[tuple[float, float], float, float]]) -> RawPosition:
        (x1, y1), d1, c1 = usable[0]
        a_rows = []
        b_rows = []
        w_rows = []
        for (xi, yi), di, ci in usable[1:]:
            a_rows.append([2.0 * (xi - x1), 2.0 * (yi - y1)])
            b_rows.append((xi**2 + yi**2 - di**2) - (x1**2 + y1**2 - d1**2))
            w_rows.append(max(0.05, (ci + c1) / 2.0))

        s11 = s12 = s22 = 0.0
        t1 = t2 = 0.0
        for (a1, a2), b, w in zip(a_rows, b_rows, w_rows):
            s11 += w * a1 * a1
            s12 += w * a1 * a2
            s22 += w * a2 * a2
            t1 += w * a1 * b
            t2 += w * a2 * b

        det = s11 * s22 - s12 * s12
        if abs(det) < 1e-9:
            avg_x = sum(pos[0][0] for pos in usable) / len(usable)
            avg_y = sum(pos[0][1] for pos in usable) / len(usable)
            return RawPosition(x=avg_x, y=avg_y, confidence=0.1, source="wls")

        sol_x = (t1 * s22 - t2 * s12) / det
        sol_y = (s11 * t2 - s12 * t1) / det

        residual_sum = 0.0
        for (a1, a2), b in zip(a_rows, b_rows):
            r = a1 * sol_x + a2 * sol_y - b
            residual_sum += r * r
        rmse = sqrt(residual_sum / max(1, len(b_rows)))

        mean_w = sum(w_rows) / len(w_rows)
        confidence = max(0.1, min(1.0, mean_w * (1.0 / (1.0 + rmse / 5.0))))
        return RawPosition(x=float(sol_x), y=float(sol_y), confidence=confidence, source="wls")
