#!/usr/bin/env python3
"""
Mock Govee H5179 WiFi Thermometer/Hygrometer MQTT Publisher.

Simulates a Govee H5179 device publishing temperature and humidity
readings to MQTT topics as formatted by the govee2mqtt bridge
(https://github.com/wez/govee2mqtt).

Readings follow a constrained random walk to produce realistic,
smoothly-varying London-weather values.

Usage:
    python mock_govee_source.py [--broker HOST] [--port PORT]

Defaults:
    --broker localhost
    --port   1883
"""

import argparse
import json
import random
import signal
import sys
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# ── Constants ────────────────────────────────────────────────────────────────
CLIENT_ID = "govee-h5179-a1b2c3d4"
MAC_ADDR = "a1b2c3d4e5f6"  # fake MAC used as MQTT topic identifier
DEVICE_MODEL = "H5179"

TOPIC_TEMPERATURE = f"govee/{DEVICE_MODEL}/{MAC_ADDR}/temperature"
TOPIC_HUMIDITY = f"govee/{DEVICE_MODEL}/{MAC_ADDR}/humidity"

INTERVAL_S = 1.0

# London-weather realistic ranges
TEMP_CENTER = 15.0   # °C
TEMP_MIN = 8.0
TEMP_MAX = 25.0
TEMP_STEP = 0.3      # max change per step

HUM_CENTER = 60.0    # %
HUM_MIN = 35.0
HUM_MAX = 85.0
HUM_STEP = 0.5       # max change per step


class RandomWalk:
    """Constrained random walk producing smoothly-varying values."""

    def __init__(self, start: float, center: float, low: float, high: float, step: float):
        self.value = start
        self.center = center
        self.low = low
        self.high = high
        self.step = step

    def next(self) -> float:
        # Small random delta
        delta = random.uniform(-self.step, self.step)

        # Gentle pull toward center when near bounds
        pull = (self.center - self.value) * 0.01
        delta += pull

        self.value += delta

        # Clamp hard to bounds
        if self.value < self.low:
            self.value = self.low + abs(delta)
        elif self.value > self.high:
            self.value = self.high - abs(delta)

        return round(self.value, 1)


def build_payload(base_value: float, unit: str) -> str:
    """Build a Govee-style JSON payload string."""
    return json.dumps({"value": base_value, "unit": unit})


def now_iso() -> str:
    """UTC ISO-8601 timestamp for log lines."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock Govee H5179 MQTT source")
    parser.add_argument("--broker", default="localhost", help="MQTT broker host (default: localhost)")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port (default: 1883)")
    args = parser.parse_args()

    print(f"[{now_iso()}] Starting mock Govee H5179 (client_id={CLIENT_ID})")
    print(f"[{now_iso()}] Broker: {args.broker}:{args.port}")
    print(f"[{now_iso()}] Temperature topic: {TOPIC_TEMPERATURE}")
    print(f"[{now_iso()}] Humidity topic:    {TOPIC_HUMIDITY}")
    print(f"[{now_iso()}] Interval: {INTERVAL_S}s")

    # Set up MQTT client
    client = mqtt.Client(client_id=CLIENT_ID, protocol=mqtt.MQTTv311)

    # Flag for graceful shutdown
    running = True

    def handle_shutdown(signum, frame):
        nonlocal running
        print(f"\n[{now_iso()}] Received signal {signum}, shutting down...")
        running = False

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Connect to broker
    try:
        client.connect(args.broker, args.port, keepalive=10)
        print(f"[{now_iso()}] Connected to MQTT broker at {args.broker}:{args.port}")
    except Exception as e:
        print(f"[{now_iso()}] ERROR: Failed to connect to MQTT broker: {e}", file=sys.stderr)
        sys.exit(1)

    client.loop_start()

    # Initialize random walks
    temp_walk = RandomWalk(
        start=TEMP_CENTER,
        center=TEMP_CENTER,
        low=TEMP_MIN,
        high=TEMP_MAX,
        step=TEMP_STEP,
    )
    hum_walk = RandomWalk(
        start=HUM_CENTER,
        center=HUM_CENTER,
        low=HUM_MIN,
        high=HUM_MAX,
        step=HUM_STEP,
    )

    print(f"[{now_iso()}] Publishing started. Press Ctrl+C to stop.\n")

    try:
        while running:
            temperature = temp_walk.next()
            humidity = hum_walk.next()

            temp_payload = build_payload(temperature, "C")
            hum_payload = build_payload(humidity, "%")

            ts = now_iso()
            # Publish temperature
            info_temp = client.publish(TOPIC_TEMPERATURE, temp_payload, qos=0)
            print(f"[{ts}] PUB  {TOPIC_TEMPERATURE}  ->  {temp_payload}")

            # Publish humidity
            info_hum = client.publish(TOPIC_HUMIDITY, hum_payload, qos=0)
            print(f"[{ts}] PUB  {TOPIC_HUMIDITY}     ->  {hum_payload}")

            time.sleep(INTERVAL_S)

    except KeyboardInterrupt:
        pass  # handled by signal handler setting running=False

    finally:
        print(f"\n[{now_iso()}] Disconnecting from MQTT broker...")
        client.loop_stop()
        client.disconnect()
        print(f"[{now_iso()}] Mock Govee H5179 stopped.")


if __name__ == "__main__":
    main()
