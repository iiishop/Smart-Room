from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np


@dataclass(slots=True)
class DevicePartProposal:
    part_id: int
    kind: str
    box_xyxy: tuple[int, int, int, int]
    score: float
    area: int
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "part_id": self.part_id,
            "kind": self.kind,
            "box_xyxy": list(self.box_xyxy),
            "score": round(float(self.score), 4),
            "area": int(self.area),
            "attributes": self.attributes,
        }


class DevicePartProposalGenerator:
    """Extract cheap visual part proposals inside a device mask.

    These proposals are intentionally class-agnostic and conservative. They
    provide targeted crops for a VLM/OCR stage without making final semantic
    claims locally.
    """

    def __init__(self, *, max_parts: int = 12) -> None:
        self._max_parts = int(max_parts)

    def generate(
        self,
        rgb: np.ndarray,
        device_mask: np.ndarray,
        *,
        depth_m: np.ndarray | None = None,
    ) -> list[DevicePartProposal]:
        if rgb.ndim != 3 or not device_mask.any():
            return []
        h, w = rgb.shape[:2]
        mask = _resize_mask(device_mask, h, w)
        proposals: list[DevicePartProposal] = []
        proposals.extend(self._screen_or_panel_candidates(rgb, mask))
        proposals.extend(self._led_candidates(rgb, mask))
        proposals.extend(self._port_or_dark_candidates(rgb, mask))
        proposals.extend(self._cable_edge_candidates(rgb, mask))
        proposals = self._dedupe(proposals)
        proposals.sort(key=lambda item: item.score, reverse=True)
        return [
            DevicePartProposal(
                part_id=index + 1,
                kind=item.kind,
                box_xyxy=item.box_xyxy,
                score=item.score,
                area=item.area,
                attributes=item.attributes,
            )
            for index, item in enumerate(proposals[: self._max_parts])
        ]

    def _screen_or_panel_candidates(
        self, rgb: np.ndarray, mask: np.ndarray
    ) -> list[DevicePartProposal]:
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        value = hsv[:, :, 2]
        saturation = hsv[:, :, 1]
        edges = cv2.Canny(gray, 60, 150)
        candidates = ((value > 120) & (saturation < 110) & mask) | ((edges > 0) & mask)
        candidates = cv2.morphologyEx(
            candidates.astype(np.uint8),
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
        ).astype(bool)
        return self._rectangular_components(
            candidates,
            kind="panel_or_screen",
            min_area=180,
            min_rectangularity=0.38,
            score_bias=0.12,
        )

    def _led_candidates(
        self, rgb: np.ndarray, mask: np.ndarray
    ) -> list[DevicePartProposal]:
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        hue = hsv[:, :, 0]
        sat = hsv[:, :, 1]
        val = hsv[:, :, 2]
        bright_color = (sat > 90) & (val > 150)
        red = (hue < 12) | (hue > 168)
        green = (hue > 35) & (hue < 95)
        blue = (hue > 95) & (hue < 135)
        led_mask = bright_color & (red | green | blue) & mask
        led_mask = cv2.morphologyEx(
            led_mask.astype(np.uint8),
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
        ).astype(bool)
        proposals = self._rectangular_components(
            led_mask,
            kind="indicator_light",
            min_area=6,
            min_rectangularity=0.22,
            score_bias=0.28,
            max_area_fraction=0.025,
        )
        for item in proposals:
            item.attributes["color_hint"] = _dominant_color_name(rgb, item.box_xyxy)
        return proposals

    def _port_or_dark_candidates(
        self, rgb: np.ndarray, mask: np.ndarray
    ) -> list[DevicePartProposal]:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        device_values = gray[mask]
        if device_values.size == 0:
            return []
        threshold = min(75, int(np.percentile(device_values, 22)))
        dark = (gray <= threshold) & mask
        dark = cv2.morphologyEx(
            dark.astype(np.uint8),
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        ).astype(bool)
        proposals = self._rectangular_components(
            dark,
            kind="port_or_vent",
            min_area=30,
            min_rectangularity=0.25,
            score_bias=0.18,
            max_area_fraction=0.08,
        )
        return proposals

    def _cable_edge_candidates(
        self, rgb: np.ndarray, mask: np.ndarray
    ) -> list[DevicePartProposal]:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 80, 180)
        dilated_mask = cv2.dilate(
            mask.astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
            iterations=1,
        ).astype(bool)
        ring = dilated_mask & ~cv2.erode(
            mask.astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
            iterations=1,
        ).astype(bool)
        edge_ring = (edges > 0) & ring
        edge_ring = cv2.dilate(
            edge_ring.astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
            iterations=1,
        ).astype(bool)
        return self._rectangular_components(
            edge_ring,
            kind="cable_or_edge",
            min_area=24,
            min_rectangularity=0.08,
            score_bias=0.04,
            max_area_fraction=0.05,
        )

    def _rectangular_components(
        self,
        component_mask: np.ndarray,
        *,
        kind: str,
        min_area: int,
        min_rectangularity: float,
        score_bias: float,
        max_area_fraction: float = 0.18,
    ) -> list[DevicePartProposal]:
        h, w = component_mask.shape
        labels_count, labels, stats, _ = cv2.connectedComponentsWithStats(
            component_mask.astype(np.uint8), connectivity=8
        )
        image_area = max(h * w, 1)
        proposals: list[DevicePartProposal] = []
        for label in range(1, labels_count):
            x, y, bw, bh, area = [int(v) for v in stats[label]]
            if area < min_area or area > int(image_area * max_area_fraction):
                continue
            if bw < 3 or bh < 3:
                continue
            rect_area = max(bw * bh, 1)
            rectangularity = area / rect_area
            aspect = bw / max(bh, 1)
            if rectangularity < min_rectangularity:
                continue
            if aspect > 12.0 or aspect < 0.08:
                continue
            score = float(np.clip(score_bias + 0.42 * rectangularity + 0.18 * min(area / 800.0, 1.0), 0.0, 1.0))
            proposals.append(
                DevicePartProposal(
                    part_id=0,
                    kind=kind,
                    box_xyxy=(x, y, x + bw, y + bh),
                    score=score,
                    area=area,
                    attributes={
                        "rectangularity": round(float(rectangularity), 4),
                        "aspect_ratio": round(float(aspect), 4),
                    },
                )
            )
        return proposals

    def _dedupe(self, proposals: list[DevicePartProposal]) -> list[DevicePartProposal]:
        kept: list[DevicePartProposal] = []
        for item in sorted(proposals, key=lambda candidate: candidate.score, reverse=True):
            if all(_iou(item.box_xyxy, other.box_xyxy) < 0.55 for other in kept):
                kept.append(item)
        return kept


def _resize_mask(mask: np.ndarray, h: int, w: int) -> np.ndarray:
    if mask.shape == (h, w):
        return mask.astype(bool, copy=False)
    return cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)


def _iou(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area_a = max((a[2] - a[0]) * (a[3] - a[1]), 1)
    area_b = max((b[2] - b[0]) * (b[3] - b[1]), 1)
    return inter / float(area_a + area_b - inter)


def _dominant_color_name(
    rgb: np.ndarray,
    box: tuple[int, int, int, int],
) -> str:
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = box
    x0 = max(0, min(w - 1, x0))
    y0 = max(0, min(h - 1, y0))
    x1 = max(x0 + 1, min(w, x1))
    y1 = max(y0 + 1, min(h, y1))
    crop = rgb[y0:y1, x0:x1]
    if crop.size == 0:
        return "unknown"
    mean = crop.reshape(-1, 3).mean(axis=0)
    r, g, b = float(mean[0]), float(mean[1]), float(mean[2])
    if r > g * 1.25 and r > b * 1.25:
        return "red"
    if g > r * 1.18 and g > b * 1.18:
        return "green"
    if b > r * 1.18 and b > g * 1.18:
        return "blue"
    if max(r, g, b) > 180:
        return "white"
    return "colored"
