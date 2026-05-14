from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from ha_client.config import load_config
from ha_client.core.event_bus import EventBus
from ha_client.api.connection import ConnectionManager
from ha_client.core.device_manager import DeviceManager
from ha_client.core.controller import DeviceController
from ha_client.gui.dashboard import DashboardApp


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config_path = (
        sys.argv[sys.argv.index("--config") + 1]
        if "--config" in sys.argv
        else os.environ.get(
            "HA_CONFIG", str(Path(__file__).resolve().parents[2] / "config.yaml")
        )
    )
    config = load_config(config_path)

    event_bus = EventBus()
    conn_mgr = ConnectionManager(config)
    device_mgr = DeviceManager(conn_mgr, event_bus)
    controller = DeviceController(device_mgr, event_bus)

    app = DashboardApp(config, conn_mgr, device_mgr, controller, event_bus)
    app.run()


if __name__ == "__main__":
    main()
