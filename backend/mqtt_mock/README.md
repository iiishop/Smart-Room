# MQTT Broker Setup for Testing

This directory contains mock MQTT sources for testing the Smart Room AR Dashboard pipeline.

## Starting a Local MQTT Broker

### Option 1: Mosquitto (native)

```bash
# Install mosquitto (if not already installed)
# Windows: download from https://mosquitto.org/download/
# Linux:   sudo apt install mosquitto  (or your distro's package manager)
# macOS:   brew install mosquitto

# Run the broker on the default port 1883
mosquitto -p 1883

# Or with verbose logging
mosquitto -p 1883 -v
```

### Option 2: Docker (recommended for quick testing)

```bash
# Run eclipse-mosquitto on port 1883
docker run -it --rm -p 1883:1883 eclipse-mosquitto

# Or with a custom config
docker run -it --rm -p 1883:1883 -v ./mosquitto.conf:/mosquitto/config/mosquitto.conf eclipse-mosquitto
```

## Running the Mock Govee Source

```bash
# From the backend directory, with default settings (localhost:1883)
python mqtt_mock/mock_govee_source.py

# Or with uv
uv run python mqtt_mock/mock_govee_source.py

# Custom broker
python mqtt_mock/mock_govee_source.py --broker 192.168.1.100 --port 1883
```

## Testing the Pipeline

1. Start the MQTT broker (mosquitto or Docker)
2. Run the mock Govee source: `python mqtt_mock/mock_govee_source.py`
3. Subscribe to verify messages:

```bash
# Using mosquitto_sub
mosquitto_sub -t "govee/H5179/a1b2c3d4e5f6/#" -v

# Using MQTT Explorer or similar GUI tool
```
