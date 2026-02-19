from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import uvicorn
from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


API_BASE = "http://127.0.0.1:8000"


def start_server() -> None:
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="info")


def fetch_status() -> dict:
    req = urllib.request.Request(f"{API_BASE}/api/status", method="GET")
    with urllib.request.urlopen(req, timeout=1.0) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_logs(since_id: int) -> dict:
    req = urllib.request.Request(
        f"{API_BASE}/api/logs?since_id={since_id}&limit=300", method="GET"
    )
    with urllib.request.urlopen(req, timeout=1.0) as res:
        return json.loads(res.read().decode("utf-8"))


class DashboardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Quest 3 Heartbeat Dashboard")
        self.resize(860, 640)

        self._last_log_id = 0
        self._latest_rgb_bytes: bytes | None = None
        self._rgb_socket = QWebSocket()
        self._rgb_socket.binaryMessageReceived.connect(self.on_rgb_binary)
        self._rgb_socket.connected.connect(self.on_rgb_socket_connected)
        self._rgb_socket.disconnected.connect(self.on_rgb_socket_disconnected)
        self._rgb_socket.errorOccurred.connect(self.on_rgb_socket_error)

        tabs = QTabWidget()

        status_tab = QWidget()
        status_layout = QVBoxLayout()

        self.status_label = QLabel("Backend: starting...")
        self.connection_label = QLabel("Headset: unknown")
        self.device_label = QLabel("Device: -")
        self.tick_label = QLabel("Tick: 0")
        self.version_label = QLabel("App Version: -")
        self.network_label = QLabel("Connection Mode: -")
        self.time_label = QLabel("Last Seen (UTC): -")
        self.rgb_info_label = QLabel("RGB Meta: frame=- size=-")
        self.rgb_label = QLabel("RGB Frame: waiting...")
        self.rgb_label.setMinimumSize(640, 360)

        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.connection_label)
        status_layout.addWidget(self.device_label)
        status_layout.addWidget(self.tick_label)
        status_layout.addWidget(self.version_label)
        status_layout.addWidget(self.network_label)
        status_layout.addWidget(self.time_label)
        status_layout.addWidget(self.rgb_info_label)
        status_layout.addWidget(self.rgb_label)
        status_tab.setLayout(status_layout)

        logs_tab = QWidget()
        logs_layout = QVBoxLayout()
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Log stream will appear here...")
        logs_layout.addWidget(self.log_view)
        logs_tab.setLayout(logs_layout)

        tabs.addTab(status_tab, "Status")
        tabs.addTab(logs_tab, "Logs")

        root = QWidget()
        root_layout = QVBoxLayout()
        root_layout.addWidget(tabs)
        root.setLayout(root_layout)
        self.setCentralWidget(root)

        self.status_timer = QTimer(self)
        self.status_timer.setInterval(300)
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start()

        self.rgb_timer = QTimer(self)
        self.rgb_timer.setInterval(16)
        self.rgb_timer.timeout.connect(self.refresh_rgb_preview)
        self.rgb_timer.start()

        self.log_timer = QTimer(self)
        self.log_timer.setInterval(250)
        self.log_timer.timeout.connect(self.refresh_logs)
        self.log_timer.start()

        self.rgb_reconnect_timer = QTimer(self)
        self.rgb_reconnect_timer.setInterval(1000)
        self.rgb_reconnect_timer.timeout.connect(self.ensure_rgb_socket_connected)
        self.rgb_reconnect_timer.start()

        self.ensure_rgb_socket_connected()

    def refresh_status(self) -> None:
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
            self.rgb_info_label.setText(
                f"RGB Meta: frame={data.get('last_rgb_frame_id', 0)} size={data.get('last_rgb_size', '-')}"
            )
        except urllib.error.URLError:
            self.status_label.setText("Backend: disconnected")
        except Exception as ex:
            self.status_label.setText(f"Backend: error ({ex})")

    def refresh_rgb_preview(self) -> None:
        try:
            data = self._latest_rgb_bytes
            if data is None:
                return

            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                self.rgb_label.setText("RGB Frame: decode failed")
                return

            scaled = pixmap.scaled(
                self.rgb_label.width(),
                self.rgb_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.rgb_label.setText("")
            self.rgb_label.setPixmap(scaled)
        except Exception as ex:
            self.rgb_label.setText(
                f"RGB Frame: unavailable ({type(ex).__name__}: {ex})"
            )

    def ensure_rgb_socket_connected(self) -> None:
        if self._rgb_socket.state() == QAbstractSocket.SocketState.ConnectedState:
            return

        if self._rgb_socket.state() == QAbstractSocket.SocketState.ConnectingState:
            return

        self._rgb_socket.open(QUrl("ws://127.0.0.1:8000/ws/rgb-preview"))

    def on_rgb_binary(self, payload) -> None:
        self._latest_rgb_bytes = bytes(payload)

    def on_rgb_socket_connected(self) -> None:
        self.append_log_item(
            {
                "timestamp_utc": "-",
                "level": "INFO",
                "source": "python",
                "script": "run_dashboard.py",
                "line": None,
                "message": "Connected to /ws/rgb-preview",
                "stack_trace": "",
            }
        )

    def on_rgb_socket_disconnected(self) -> None:
        self.append_log_item(
            {
                "timestamp_utc": "-",
                "level": "WARNING",
                "source": "python",
                "script": "run_dashboard.py",
                "line": None,
                "message": "Disconnected from /ws/rgb-preview",
                "stack_trace": "",
            }
        )

    def on_rgb_socket_error(self, _error) -> None:
        self.rgb_label.setText(
            f"RGB Frame: socket error ({self._rgb_socket.errorString()})"
        )

    def refresh_logs(self) -> None:
        try:
            result = fetch_logs(self._last_log_id)
            logs = result.get("logs", [])
            if not logs:
                return

            for item in logs:
                self._last_log_id = max(self._last_log_id, int(item.get("id", 0)))
                self.append_log_item(item)
        except Exception:
            pass

    def append_log_item(self, item: dict) -> None:
        level = str(item.get("level", "INFO")).upper()
        source = item.get("source", "unknown")
        script = item.get("script") or "-"
        line = item.get("line")
        ts = item.get("timestamp_utc", "-")
        msg = item.get("message", "")
        stack = item.get("stack_trace") or ""

        color = {
            "ERROR": "#d32f2f",
            "WARNING": "#f57c00",
            "INFO": "#9e9e9e",
        }.get(level, "#9e9e9e")

        line_info = f":{line}" if line is not None else ""
        text = (
            f"<div style='color:{color};'>"
            f"[{ts}] [{level}] [{source}] {script}{line_info} - {msg}"
            f"</div>"
        )
        self.log_view.append(text)

        if stack:
            escaped = (
                stack.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br>")
            )
            self.log_view.append(
                f"<div style='color:#757575; margin-left:12px;'>stack: {escaped}</div>"
            )


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
