"""TOML configuration loader for Discover Client sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from discover_client.source import SourceConfig

# We'll use tomli (stdlib tomllib on 3.11+) for TOML parsing.
# On Python < 3.11, tomllib is unavailable; use tomli as fallback.
try:
    import tomllib  # Python >= 3.11
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class SourceTypeSchema:
    """Validation schema for a source type's settings."""

    required: list[str] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)

    def validate(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Merge defaults and check required keys. Returns validated settings."""
        merged = {**self.defaults, **settings}
        for key in self.required:
            if key not in merged or merged[key] is None:
                raise ValueError(
                    f"Missing required setting '{key}' for source type"
                )
        return merged


# -- Registered schemas per source_type --------------------------------

SCHEMAS: dict[str, SourceTypeSchema] = {
    "mqtt": SourceTypeSchema(
        required=["host", "port"],
        defaults={
            "host": "localhost",
            "port": 1883,
            "username": None,
            "password": None,
            "topic_whitelist": [],
            "topic_blacklist": [],
        },
    ),
    "mdns": SourceTypeSchema(
        required=[],
        defaults={
            "scan_interval_s": 30,
            "service_types": [
                "_mqtt._tcp.local.",
                "_home-assistant._tcp.local.",
                "_http._tcp.local.",
            ],
        },
    ),
    "ssdp": SourceTypeSchema(
        required=[],
        defaults={
            "scan_interval_s": 60,
        },
    ),
    "nmap": SourceTypeSchema(
        required=[],
        defaults={
            "scan_interval_s": 300,
            "target_subnet": "",
            "scan_flags": "-sn -PR",
        },
    ),
    "home_assistant": SourceTypeSchema(
        required=["base_url", "token"],
        defaults={
            "base_url": "",
            "token": "",
        },
    ),
}


def load_config(path: str | Path | None = None) -> list[SourceConfig]:
    """Load and validate source configurations from a TOML file.

    Args:
        path: Path to config.toml. Defaults to discover_client/config.toml
              relative to this module's directory.

    Returns:
        List of validated SourceConfig objects.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        ValueError: If any source block fails validation.
    """
    if path is None:
        path = Path(__file__).resolve().parent / "config.toml"
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "rb") as f:
        data = tomllib.load(f)

    sources_raw = data.get("sources", [])
    if not isinstance(sources_raw, list):
        raise ValueError("TOML must contain a [[sources]] array")

    configs: list[SourceConfig] = []
    for i, block in enumerate(sources_raw):
        source_id = block.get("source_id", f"source-{i}")
        source_type = block.get("source_type", "")
        enabled = block.get("enabled", True)
        raw_settings = block.get("settings", {})

        if not source_type:
            raise ValueError(f"source_id='{source_id}' is missing source_type")

        schema = SCHEMAS.get(source_type)
        if schema is None:
            raise ValueError(
                f"Unknown source_type='{source_type}' for source_id='{source_id}'"
            )

        settings = schema.validate(raw_settings)
        configs.append(
            SourceConfig(
                source_id=source_id,
                source_type=source_type,
                enabled=enabled,
                settings=settings,
            )
        )

    return configs
