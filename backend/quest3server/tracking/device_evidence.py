from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import cv2
import httpx
import numpy as np

from .part_proposal import DevicePartProposal

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VlmEvidenceSettings:
    enabled: bool
    base_url: str
    api_key: str
    model: str
    timeout_s: float
    max_part_crops: int

    @classmethod
    def from_env(cls) -> "VlmEvidenceSettings":
        api_key = os.getenv("QUEST3_VLM_API_KEY", "").strip()
        base_url = os.getenv("QUEST3_VLM_BASE_URL", "").strip()
        model = os.getenv("QUEST3_VLM_MODEL", "qwen-vl-plus").strip()
        enabled = os.getenv("QUEST3_VLM_ENABLED", "0").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return cls(
            enabled=enabled and bool(api_key) and bool(base_url),
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            timeout_s=float(os.getenv("QUEST3_VLM_TIMEOUT_S", "12")),
            max_part_crops=max(0, int(os.getenv("QUEST3_VLM_MAX_PART_CROPS", "6"))),
        )


class OpenAICompatibleVlmEvidenceProvider:
    """Describe a segmented lab device through an OpenAI-compatible VLM API."""

    def __init__(self, settings: VlmEvidenceSettings | None = None) -> None:
        self._settings = settings or VlmEvidenceSettings.from_env()

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    def describe_device(
        self,
        *,
        rgb: np.ndarray,
        device_mask: np.ndarray,
        parts: list[DevicePartProposal],
        geometry: dict[str, Any],
        local_hint: str = "",
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"provider": "vlm_disabled", "enabled": False}

        try:
            whole_crop = _masked_crop(rgb, device_mask, pad_ratio=0.18)
            context_crop = _context_crop(rgb, device_mask, pad_ratio=0.55)
            images = [
                ("whole_device", whole_crop),
                ("context", context_crop),
            ]
            for part in parts[: self._settings.max_part_crops]:
                x0, y0, x1, y1 = part.box_xyxy
                crop = rgb[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
                if crop.size:
                    images.append((f"part_{part.part_id}_{part.kind}", crop))

            prompt = _build_prompt(
                geometry=geometry,
                parts=[item.to_payload() for item in parts],
                local_hint=local_hint,
            )
            content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            for label, image in images:
                encoded = _encode_jpeg_data_url(image)
                if encoded:
                    content.append({"type": "text", "text": f"Image: {label}"})
                    content.append({"type": "image_url", "image_url": {"url": encoded}})

            response = httpx.post(
                f"{self._settings.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._settings.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._settings.model,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                },
                timeout=self._settings.timeout_s,
            )
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            parsed = _parse_json_object(text)
            parsed.setdefault("provider", "openai_compatible_vlm")
            parsed.setdefault("model", self._settings.model)
            return parsed
        except Exception as exc:
            logger.warning("VLM evidence request failed: %s", exc)
            return {
                "provider": "openai_compatible_vlm",
                "model": self._settings.model,
                "error": str(exc),
            }


def build_local_visual_evidence(
    *,
    label: str,
    label_score: float,
    parts: list[DevicePartProposal],
    geometry: dict[str, Any],
    segmentation_source: str,
    segmentation_confidence: float,
) -> dict[str, Any]:
    return {
        "provider": "local",
        "label_hint": label,
        "label_score": round(float(label_score), 4),
        "segmentation_source": segmentation_source,
        "segmentation_confidence": round(float(segmentation_confidence), 4),
        "geometry": geometry,
        "parts": [item.to_payload() for item in parts],
    }


def _build_prompt(
    *,
    geometry: dict[str, Any],
    parts: list[dict[str, Any]],
    local_hint: str,
) -> str:
    return (
        "You are helping associate a visually selected lab device with backend "
        "network-discovered devices. The segmentation mask is already chosen by "
        "the system; do not challenge its boundary unless there is obvious leakage. "
        "Describe only stable, matchable evidence.\n\n"
        "Return strict JSON with this schema:\n"
        "{\n"
        '  "device_category": ["..."],\n'
        '  "possible_device_types": ["..."],\n'
        '  "visual_features": ["..."],\n'
        '  "visible_text": ["..."],\n'
        '  "visible_ports_or_io": ["..."],\n'
        '  "visible_indicators": ["..."],\n'
        '  "materials_colors": ["..."],\n'
        '  "association_hints": ["..."],\n'
        '  "confidence": 0.0,\n'
        '  "notes": "..."\n'
        "}\n\n"
        f"Local label hint: {local_hint or 'none'}\n"
        f"Geometry: {json.dumps(geometry, ensure_ascii=True)}\n"
        f"Detected part proposals: {json.dumps(parts, ensure_ascii=True)}\n"
    )


def _masked_crop(rgb: np.ndarray, mask: np.ndarray, pad_ratio: float) -> np.ndarray:
    h, w = rgb.shape[:2]
    ys, xs = np.where(mask)
    if xs.size == 0:
        return rgb
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)
    pad_x = max(8, int(bw * pad_ratio))
    pad_y = max(8, int(bh * pad_ratio))
    x0 = max(0, x0 - pad_x)
    y0 = max(0, y0 - pad_y)
    x1 = min(w, x1 + pad_x + 1)
    y1 = min(h, y1 + pad_y + 1)
    crop = rgb[y0:y1, x0:x1].copy()
    crop_mask = mask[y0:y1, x0:x1]
    bg = np.full_like(crop, 245)
    return np.where(crop_mask[:, :, None], crop, bg)


def _context_crop(rgb: np.ndarray, mask: np.ndarray, pad_ratio: float) -> np.ndarray:
    h, w = rgb.shape[:2]
    ys, xs = np.where(mask)
    if xs.size == 0:
        return rgb
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)
    pad_x = max(16, int(bw * pad_ratio))
    pad_y = max(16, int(bh * pad_ratio))
    return rgb[max(0, y0 - pad_y):min(h, y1 + pad_y + 1), max(0, x0 - pad_x):min(w, x1 + pad_x + 1)]


def _encode_jpeg_data_url(image: np.ndarray) -> str:
    if image.size == 0:
        return ""
    bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    ok, buffer = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        return ""
    encoded = base64.b64encode(buffer.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _parse_json_object(text: str) -> dict[str, Any]:
    if isinstance(text, dict):
        return text
    content = str(text).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, flags=re.DOTALL)
    if fenced:
        content = fenced.group(1)
    if not content.startswith("{"):
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            content = content[start:end + 1]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("VLM response JSON must be an object")
    return parsed
