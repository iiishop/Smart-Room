"""Local IEEE OUI database loader.

Downloads and caches the public IEEE OUI listing (oui.txt) for offline
MAC → vendor lookups.  The file is ~6.4 MB and lives under
``discover_client/data/oui.txt``.

Usage::

    from discover_client.identification.oui import load_oui
    oui = load_oui()
    vendor = oui.get("58044F")  # → "TP-LINK TECHNOLOGIES CO.,LTD."

The OUI file is refreshed automatically when older than 30 days.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_OUI_URL = "https://standards-oui.ieee.org/oui/oui.txt"
_REFRESH_SECONDS = 30 * 86400  # 30 days

_OUI_CACHE: dict[str, str] | None = None
_LAST_LOAD_TIME: float = 0.0


def _oui_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "oui.txt"


def _download_if_stale() -> None:
    path = _oui_path()
    if path.exists():
        age = time.time() - path.stat().st_mtime
        if age < _REFRESH_SECONDS:
            return
        logger.info("OUI file is %d days old, refreshing", int(age / 86400))

    import urllib.request

    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading IEEE OUI database from %s", _OUI_URL)
    urllib.request.urlretrieve(_OUI_URL, path)
    logger.info("OUI database saved to %s (%d bytes)", path, path.stat().st_size)


def load_oui() -> dict[str, str]:
    """Return a MAC prefix → vendor dict, downloading the OUI file if needed.

    MAC prefixes are normalised to uppercase hex without separators
    (e.g. ``"58044F"``).  Only MA-L (24-bit / 3-octet) prefixes are indexed.
    """
    global _OUI_CACHE, _LAST_LOAD_TIME

    now = time.time()
    if _OUI_CACHE is not None and (now - _LAST_LOAD_TIME) < 3600:
        return _OUI_CACHE

    _download_if_stale()
    path = _oui_path()

    oui: dict[str, str] = {}
    hex_re = re.compile(r"^([0-9A-F]{2})-([0-9A-F]{2})-([0-9A-F]{2})\s+\(hex\)\s+(.+)")

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = hex_re.match(line)
            if m:
                prefix = m.group(1) + m.group(2) + m.group(3)
                vendor = m.group(4).strip()
                oui[prefix] = vendor

    _OUI_CACHE = oui
    _LAST_LOAD_TIME = now
    logger.info("Loaded %d OUI entries", len(oui))
    return oui
