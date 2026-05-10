from __future__ import annotations

import logging
import signal
import sys

from ha_client.config import load_config
from ha_client.gui.app import HADebugApp


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config = load_config("config.yaml")
    app = HADebugApp(config)
    app.run()


if __name__ == "__main__":
    main()
