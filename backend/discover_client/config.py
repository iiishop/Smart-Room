"""TOML configuration loader for Discover Client sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import os
import time
import uuid
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
    "packet_sniff": SourceTypeSchema(
        required=[],
        defaults={
            "pcap_path": "",
            "live": False,
            "interface": "",
            "broker_ports": [1883],
            "capture_filter": "",
            "emit_publish_topics": True,
            "max_packets": 0,
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


def save_config(configs: list[SourceConfig], path: str | Path | None = None) -> Path:
    if path is None:
        target = Path(__file__).resolve().parent / "config.toml"
    else:
        target = Path(path)

    lines = ["# discover_client/config.toml"]
    for config in configs:
        lines.extend(
            [
                "",
                "[[sources]]",
                f"source_id = {_toml_string(config.source_id)}",
                f"source_type = {_toml_string(config.source_type)}",
                f"enabled = {'true' if config.enabled else 'false'}",
                "",
                "[sources.settings]",
            ]
        )
        for key, value in sorted(config.settings.items()):
            lines.append(f"{key} = {_toml_value(value)}")

    save_config_text("\n".join(lines) + "\n", target)
    return target


def save_config_text(content: str, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(7):
        tmp = target.with_name(
            f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
            return target
        except PermissionError as exc:
            last_error = exc
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.025 * (2**attempt))
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    assert last_error is not None
    raise last_error


def _toml_string(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return _toml_string(value)
