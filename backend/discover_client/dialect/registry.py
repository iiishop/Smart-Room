"""Recognizer registry — isolated from __init__.py to avoid circular imports."""

from __future__ import annotations

from discover_client.dialect.recognizer import DialectRecognizer

RECOGNIZERS: dict[str, type[DialectRecognizer]] = {}


def register_recognizer(name: str):
    def decorator(cls: type[DialectRecognizer]):
        RECOGNIZERS[name] = cls
        return cls
    return decorator
