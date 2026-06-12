"""Local IEEE OUI database — auto-downloads and caches for vendor enrichment."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

OUI_URL = "https://standards-oui.ieee.org/oui/oui.csv"
CACHE_MAX_AGE_S = 30 * 86400  # 30 days

_oui_cache: dict[str, str] | None = None


def _cache_path() -> Path:
    """Cache location: project root's oui_cache.json."""
    return Path(__file__).resolve().parent.parent.parent / "oui_cache.json"


def _is_cache_fresh(path: Path) -> bool:
    try:
        return (time.time() - os.path.getmtime(path)) < CACHE_MAX_AGE_S
    except OSError:
        return False


def _download_oui() -> dict[str, str]:
    """Download IEEE OUI CSV and parse into {MAC_prefix: company} dict."""
    logger.info("Downloading IEEE OUI database from %s ...", OUI_URL)
    req = urllib.request.Request(OUI_URL, headers={"User-Agent": "DiscoverClient/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")

    oui: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Registry"):
            continue
        parts = line.split(",", 3)
        if len(parts) >= 4:
            mac = parts[1].strip().upper()
            company = parts[2].strip().strip('"')
            if mac and company:
                # Normalize to colon-separated hex: 7C7D21 -> 7C:7D:21
                chunks = [mac[i:i+2] for i in range(0, len(mac), 2)]
                mac_normalized = ":".join(chunks)
                oui[mac_normalized] = company

    logger.info("Parsed %d OUI entries", len(oui))
    return oui


def load_oui() -> dict[str, str]:
    """Return {MAC_prefix: company_name} dict, downloading + caching as needed."""
    global _oui_cache
    if _oui_cache is not None:
        return _oui_cache

    path = _cache_path()

    if _is_cache_fresh(path):
        try:
            with open(path, encoding="utf-8") as f:
                _oui_cache = json.load(f)
            logger.info("Loaded %d OUI entries from cache", len(_oui_cache))
            return _oui_cache
        except (json.JSONDecodeError, OSError):
            logger.warning("OUI cache corrupt, re-downloading")

    try:
        _oui_cache = _download_oui()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_oui_cache, f, separators=(",", ":"))
    except Exception:
        logger.exception("Failed to download OUI database")
        _oui_cache = {}

    return _oui_cache
