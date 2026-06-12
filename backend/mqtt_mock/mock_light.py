"""Mock MQTT light bulb — subscribes to commands, publishes state, shows PySide6 GUI."""

import json
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QRadialGradient, QBrush, QPainter, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


DEVICE_ID = "light-1"
TOPIC_COMMAND = f"mock/{DEVICE_ID}/set"
TOPIC_STATE = f"mock/{DEVICE_ID}/state"
TOPIC_STATUS = f"mock/{DEVICE_ID}/status"


class LightBulb(QFrame):
    """Visual light bulb that reacts to state changes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 320)
        self._power = False
        self._brightness = 100
        self._color_temp = 4000

    def set_power(self, on: bool):
        self._power = on
        self.update()

    def set_brightness(self, val: int):
        self._brightness = max(0, min(100, val))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Bulb body
        center_x = self.width() // 2
        bulb_y = 40
        bulb_w, bulb_h = 120, 150

        if self._power:
            alpha = int(80 + self._brightness * 1.5)
            glow = QColor(255, 240, 150, min(alpha, 255))
            # Outer glow
            gradient = QRadialGradient(center_x, bulb_y + bulb_h // 2, 140)
            gradient.setColorAt(0, glow)
            gradient.setColorAt(1, QColor(255, 240, 150, 0))
            painter.setBrush(QBrush(gradient))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(center_x - 70, bulb_y - 20, 140, 190, 70, 70)

            # Inner bright core
            core = QColor(255, 255, 200, min(220, alpha + 30))
            gradient2 = QRadialGradient(center_x, bulb_y + bulb_h // 2, 70)
            gradient2.setColorAt(0, core)
            gradient2.setColorAt(1, QColor(255, 220, 100, 0))
            painter.setBrush(QBrush(gradient2))
            painter.drawRoundedRect(center_x - 35, bulb_y + 20, 70, 110, 35, 35)
        else:
            painter.setBrush(QBrush(QColor(90, 90, 95)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(center_x - 35, bulb_y + 20, 70, 110, 35, 35)

        # Base
        painter.setBrush(QBrush(QColor(70, 70, 75)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(center_x - 25, bulb_y + 130, 50, 118, 6, 6)

    def current_state(self) -> dict:
        return {
            "power": "ON" if self._power else "OFF",
            "brightness": self._brightness,
        }


class MockLightWindow(QWidget):
    """Main window showing a light bulb + state labels."""

    def __init__(self, broker_host: str, broker_port: int):
        super().__init__()
        self.setWindowTitle(f"MQTT Light — {DEVICE_ID}")
        self.setFixedSize(320, 420)
        self.setStyleSheet("background-color: #1e1e2e; color: #cdd6f4;")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Light bulb widget
        self._bulb = LightBulb()
        layout.addWidget(self._bulb, alignment=Qt.AlignmentFlag.AlignCenter)

        # State label
        self._label = QLabel("OFF")
        self._label.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)

        # Brightness label
        self._bright_label = QLabel("Brightness: 100%")
        self._bright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._bright_label)

        # MQTT client
        self._mqtt = mqtt.Client(client_id=f"mock-light-{DEVICE_ID}")
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_message = self._on_message
        self._mqtt.connect_async(broker_host, broker_port)
        self._mqtt.loop_start()

        # Periodic status heartbeat
        self._heartbeat = QTimer(self)
        self._heartbeat.timeout.connect(self._publish_status)
        self._heartbeat.start(15_000)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(TOPIC_COMMAND, qos=1)
            # Announce capability — publish retained so Discover detects cmd topic
            client.publish(TOPIC_COMMAND, json.dumps({"_announce": True}), qos=1, retain=True)
            self._publish_status()
            print(f"[mock-light] Connected to {client._host}:{client._port}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, ValueError):
            return

        if payload.get("_announce"):
            return  # Skip self-announcement

        changed = False
        if "power" in payload:
            val = str(payload["power"]).upper()
            self._bulb.set_power(val in ("ON", "TRUE", "1", "TOGGLE"))
            changed = True
        if "brightness" in payload:
            self._bulb.set_brightness(int(payload["brightness"]))
            changed = True

        if changed:
            state = self._bulb.current_state()
            client.publish(TOPIC_STATE, json.dumps(state), qos=1, retain=True)
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            print(f"[{ts}] State changed → {state}")

    def _publish_status(self):
        state = self._bulb.current_state()
        self._mqtt.publish(TOPIC_STATE, json.dumps(state), qos=1, retain=True)
        self._mqtt.publish(TOPIC_STATUS, json.dumps({
            "device": DEVICE_ID,
            "type": "light",
            "capabilities": ["power", "brightness"],
            "connected": True,
        }))

        self._label.setText("ON" if self._bulb._power else "OFF")
        self._label.setStyleSheet(
            "color: #f9e2af; font-weight: bold;" if self._bulb._power else "color: #6c7086;"
        )
        self._bright_label.setText(f"Brightness: {self._bulb._brightness}%")
        self._bright_label.setStyleSheet(
            "color: #a6adc8;" if self._bulb._power else "color: #585b70;"
        )


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    args = parser.parse_args()

    app = QApplication([])
    window = MockLightWindow(args.broker, args.port)
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
