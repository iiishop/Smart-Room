from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ModuleConfig:
    enabled: bool = True
    import_path: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrchestratorConfig:
    source_path: Path
    raw: dict[str, Any]
    wifi_scanner: ModuleConfig
    location_estimator: ModuleConfig
    ha_integration: ModuleConfig
    quest3_datasource: ModuleConfig


class ConfigLoader:
    def __init__(self, config_path: str) -> None:
        self._path = Path(config_path)

    def load(self) -> OrchestratorConfig:
        if not self._path.exists():
            raise FileNotFoundError(f"Config file not found: {self._path}")

        with self._path.open("r", encoding="utf-8") as handle:
            root = yaml.safe_load(handle) or {}

        wifi_location = root.get("wifi_location", {}) or {}

        ha_base = {
            "url": root.get("url"),
            "token": root.get("token"),
            "verify_ssl": root.get("verify_ssl"),
            "request_timeout": root.get("request_timeout"),
            "reconnect_delay": root.get("reconnect_delay"),
            "max_reconnect_attempts": root.get("max_reconnect_attempts"),
        }
        ha_base = {k: v for k, v in ha_base.items() if v is not None}

        scanner_cfg = self._module_from_section(
            wifi_location,
            key="wifi_scanner",
            default_import="wifi_scanner.scanner:WiFiScanner",
        )
        estimator_cfg = self._module_from_section(
            wifi_location,
            key="location_estimator",
            default_import="location_estimator.estimator:LocationEstimator",
        )
        quest_cfg = self._module_from_section(
            wifi_location,
            key="quest3_datasource",
            default_import="quest3_datasource.client:Quest3DataSource",
            default_enabled=False,
        )
        ha_cfg = self._module_from_section(
            wifi_location,
            key="ha_integration",
            default_import="ha_integration.publisher:HAIntegration",
            extra_defaults=ha_base,
        )

        return OrchestratorConfig(
            source_path=self._path,
            raw=root,
            wifi_scanner=scanner_cfg,
            location_estimator=estimator_cfg,
            ha_integration=ha_cfg,
            quest3_datasource=quest_cfg,
        )

    def _module_from_section(
        self,
        section: dict[str, Any],
        *,
        key: str,
        default_import: str,
        default_enabled: bool = True,
        extra_defaults: dict[str, Any] | None = None,
    ) -> ModuleConfig:
        extra_defaults = extra_defaults or {}
        module_section = section.get(key, {})
        if module_section is None:
            module_section = {}

        if not isinstance(module_section, dict):
            raise ValueError(f"wifi_location.{key} must be a mapping")

        enabled = module_section.get("enabled", default_enabled)
        import_path = module_section.get("import", default_import)
        options = dict(extra_defaults)

        if "options" in module_section:
            if not isinstance(module_section["options"], dict):
                raise ValueError(f"wifi_location.{key}.options must be a mapping")
            options.update(module_section["options"])

        for opt_key, opt_value in module_section.items():
            if opt_key in {"enabled", "import", "options"}:
                continue
            options[opt_key] = opt_value

        return ModuleConfig(
            enabled=bool(enabled),
            import_path=str(import_path),
            options=options,
        )
