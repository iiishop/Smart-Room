import sys
from pathlib import Path

from ha_client.config.settings import load_config, create_default_config
from ha_client.gui.app import HADebugApp


def main():
    config_path = "config.yaml"

    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    config_file = Path(config_path)
    if not config_file.exists():
        print(f"Config file not found, creating default: {config_path}")
        create_default_config(config_path)
        print(f"Please edit {config_path} with your Home Assistant URL and token.")
        sys.exit(0)

    config = load_config(config_path)

    if not config.token or config.token == "YOUR_LONG_LIVED_ACCESS_TOKEN_HERE":
        print(
            "ERROR: Please set your Home Assistant long-lived access token in "
            f"{config_path}"
        )
        sys.exit(1)

    app = HADebugApp(config)
    app.run()


if __name__ == "__main__":
    main()
