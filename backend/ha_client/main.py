from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from ha_client.config import load_config
from ha_client.gui.app import HADebugApp


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
    app = HADebugApp(config)
    app.run()


if __name__ == "__main__":
    main()
