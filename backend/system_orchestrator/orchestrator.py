from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from dataclasses import dataclass
from typing import Any

from .config_loader import ConfigLoader, ModuleConfig, OrchestratorConfig
from .health_monitor import HealthMonitor

LOGGER = logging.getLogger(__name__)


@dataclass
class ServiceState:
    name: str
    started: bool = False
    enabled: bool = True
    error: str = ""


class NoopService:
    def __init__(self, name: str) -> None:
        self.name = name
        self.unavailable = True
        self.started = False

    async def start(self) -> None:
        self.started = False

    async def stop(self) -> None:
        self.started = False


class SystemOrchestrator:
    def __init__(self, config_path: str) -> None:
        self.config_loader = ConfigLoader(config_path)
        self.config: OrchestratorConfig = self.config_loader.load()
        self.monitor = HealthMonitor()

        self.services: dict[str, Any] = {}
        self.states: dict[str, ServiceState] = {}

    async def start(self) -> None:
        self.services["wifi_scanner"] = self._create_service(
            "wifi_scanner", self.config.wifi_scanner
        )
        self.services["quest3_datasource"] = self._create_service(
            "quest3_datasource", self.config.quest3_datasource
        )
        self.services["location_estimator"] = self._create_service(
            "location_estimator", self.config.location_estimator
        )
        self.services["ha_integration"] = self._create_service(
            "ha_integration", self.config.ha_integration
        )

        await self._start_parallel("wifi_scanner", "quest3_datasource")
        await self._start_service("location_estimator")
        await self._start_service("ha_integration")

    async def stop(self) -> None:
        for name in [
            "ha_integration",
            "location_estimator",
            "quest3_datasource",
            "wifi_scanner",
        ]:
            await self._stop_service(name)

    def get_health(self) -> dict[str, Any]:
        report = self.monitor.collect(self.services)
        report["modules"] = {
            name: {
                "enabled": state.enabled,
                "started": state.started,
                "error": state.error,
            }
            for name, state in self.states.items()
        }
        return report

    def _create_service(self, name: str, module_config: ModuleConfig) -> Any:
        state = ServiceState(name=name, enabled=module_config.enabled)
        self.states[name] = state

        if not module_config.enabled:
            LOGGER.info("%s disabled by configuration", name)
            return None

        try:
            service = self._load_and_instantiate(module_config)
            LOGGER.info("Loaded %s from %s", name, module_config.import_path)
            return service
        except Exception as exc:
            state.error = str(exc)
            LOGGER.exception("Failed to load %s", name)
            return NoopService(name)

    def _load_and_instantiate(self, module_config: ModuleConfig) -> Any:
        if ":" not in module_config.import_path:
            raise ValueError(
                "Module import path must be in format 'package.module:ClassName'"
            )

        module_name, class_name = module_config.import_path.split(":", maxsplit=1)
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)

        if not callable(cls):
            raise TypeError(f"{module_config.import_path} is not callable")
        return cls(**module_config.options)

    async def _start_parallel(self, *names: str) -> None:
        tasks = [self._start_service(name) for name in names]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _start_service(self, name: str) -> None:
        service = self.services.get(name)
        state = self.states[name]
        if service is None:
            return
        if getattr(service, "unavailable", False):
            state.started = False
            return

        try:
            await self._invoke(service, "start")
            state.started = True
            if not state.error:
                state.error = ""
            LOGGER.info("Started %s", name)
        except Exception as exc:
            state.started = False
            state.error = str(exc)
            LOGGER.exception("Failed to start %s", name)

    async def _stop_service(self, name: str) -> None:
        service = self.services.get(name)
        state = self.states.get(name)
        if service is None or state is None:
            return

        try:
            await self._invoke(service, "stop")
            state.started = False
            LOGGER.info("Stopped %s", name)
        except Exception as exc:
            state.error = str(exc)
            LOGGER.exception("Failed to stop %s", name)

    async def _invoke(self, target: Any, method: str) -> Any:
        func = getattr(target, method, None)
        if func is None:
            return None
        result = func()
        if inspect.isawaitable(result):
            return await result
        return result
