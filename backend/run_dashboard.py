from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import uvicorn
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget


API_BASE = "http://127.0.0.1:8000"


def start_server() -> None:
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="info")


def fetch_status() -> dict:
    req = urllib.request.Request(f"{API_BASE}/api/status", method="GET")
    with urllib.request.urlopen(req, timeout=1.0) as res:
        return json.loads(res.read().decode("utf-8"))


class DashboardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Quest 3 Heartbeat Dashboard")
        self.resize(600, 360)

        self.status_label = QLabel("Backend: starting...")
        self.connection_label = QLabel("Headset: unknown")
        self.device_label = QLabel("Device: -")
        self.tick_label = QLabel("Tick: 0")
        self.version_label = QLabel("App Version: -")
        self.network_label = QLabel("Connection Mode: -")
        self.time_label = QLabel("Last Seen (UTC): -")

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.connection_label)
        layout.addWidget(self.device_label)
        layout.addWidget(self.tick_label)
        layout.addWidget(self.version_label)
        layout.addWidget(self.network_label)
        layout.addWidget(self.time_label)

        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def refresh(self) -> None:
        try:
            data = fetch_status()
            payload = data.get("last_payload", {})

            self.status_label.setText("Backend: running")
            self.connection_label.setText(
                f"Headset Connected: {data.get('connected', False)} "
                f"(active={data.get('active_connections', 0)})"
            )
            self.device_label.setText(f"Device: {payload.get('device_model', '-')}")
            self.tick_label.setText(f"Tick: {data.get('last_tick', 0)}")
            self.version_label.setText(
                f"App Version: {payload.get('app_version', '-')}"
            )
            self.network_label.setText(
                f"Connection Mode: {payload.get('connection_mode', '-')}"
            )
            self.time_label.setText(
                f"Last Seen (UTC): {data.get('last_seen_utc', '-')}"
            )
        except urllib.error.URLError:
            self.status_label.setText("Backend: disconnected")
        except Exception as ex:
            self.status_label.setText(f"Backend: error ({ex})")


def main() -> int:
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Give server a short warm-up so dashboard doesn't start with immediate errors.
    time.sleep(0.6)

    app = QApplication([])
    window = DashboardWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
