"""Extract high-specificity identity tokens shared across discovery protocols."""

from __future__ import annotations

import re
from typing import Any


_SPLIT_RE = re.compile(r"[/\s_.:|=,;?&#@()\[\]{}<>]+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_IDENTITY_KEYS = {
    "clientid",
    "client_id",
    "connection",
    "connections",
    "device",
    "deviceid",
    "device_id",
    "id",
    "ids",
    "identifier",
    "identifiers",
    "ieee",
    "ieeeaddress",
    "ieee_address",
    "mac",
    "serial",
    "serialnumber",
    "serial_number",
    "sn",
    "uniqueid",
    "unique_id",
    "usn",
    "uuid",
}
_SCALAR_ONLY_IDENTITY_KEYS = {"sn"}


def extract_identity_tokens(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        for scalar in _scalar_strings(value):
            for candidate in _SPLIT_RE.split(scalar):
                normalized = _normalize(candidate)
                if _is_strong(normalized):
                    tokens.add(normalized)
    return tokens


def _scalar_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            normalized_key = str(key).casefold().replace("-", "_")
            compact_key = normalized_key.replace("_", "")
            if normalized_key in _IDENTITY_KEYS or compact_key in _IDENTITY_KEYS:
                if (
                    normalized_key in _SCALAR_ONLY_IDENTITY_KEYS
                    or compact_key in _SCALAR_ONLY_IDENTITY_KEYS
                ):
                    if isinstance(item, (str, int)):
                        result.extend(_all_scalar_strings(item))
                else:
                    result.extend(_all_scalar_strings(item))
            elif isinstance(item, (dict, list, tuple, set)):
                result.extend(_scalar_strings(item))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            result.extend(_scalar_strings(item))
        return result
    if isinstance(value, (str, int)):
        return [str(value)]
    return []


def _all_scalar_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            result.extend(_all_scalar_strings(key))
            result.extend(_all_scalar_strings(item))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            result.extend(_all_scalar_strings(item))
        return result
    if isinstance(value, (str, int)):
        return [str(value)]
    return []


def _normalize(value: str) -> str:
    return _NON_ALNUM_RE.sub("", value.casefold())


def _is_strong(value: str) -> bool:
    if len(value) < 8:
        return False
    has_alpha = any(char.isalpha() for char in value)
    has_digit = any(char.isdigit() for char in value)
    if len(value) >= 12 and all(char in "0123456789abcdef" for char in value):
        return True
    return len(value) >= 10 and has_alpha and has_digit
