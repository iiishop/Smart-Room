from __future__ import annotations

import json
import math
import os
import struct
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import uvicorn
from PySide6.QtCore import QEvent, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


API_BASE = "http://127.0.0.1:8000"


def start_server() -> None:
    uvicorn.run("quest3server.main:app", host="0.0.0.0", port=8000, log_level="info")


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


def fetch_raycast_result() -> dict:
    req = urllib.request.Request(f"{API_BASE}/api/raycast-result", method="GET")
    with urllib.request.urlopen(req, timeout=1.0) as res:
        return json.loads(res.read().decode("utf-8"))


def post_raycast_query(u: float, v: float) -> dict:
    payload = json.dumps({"u": float(u), "v": float(v)}).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/api/raycast-query",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
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
        self._latest_depth_values: list[float] | None = None
        self._latest_depth_width = 0
        self._latest_depth_height = 0
        self._depth_preview_pixmap: QPixmap | None = None
        self._depth_overlay_pixmap: QPixmap | None = None
        self._depth_valid_bbox: tuple[int, int, int, int] | None = None
        self._depth_draw_rect: tuple[int, int, int, int] | None = None
        self._rgb_draw_rect: tuple[int, int, int, int] | None = None
        self._latest_rgb_width = 0
        self._latest_rgb_height = 0
        self._lut_loaded = False
        self._lut_rgb_w = 0
        self._lut_rgb_h = 0
        self._lut_depth_w = 0
        self._lut_depth_h = 0
        self._map_depth_to_rgb_x: np.ndarray | None = None
        self._map_depth_to_rgb_y: np.ndarray | None = None
        self._valid_depth_to_rgb: np.ndarray | None = None
        self._map_rgb_to_depth_x: np.ndarray | None = None
        self._map_rgb_to_depth_y: np.ndarray | None = None
        self._valid_rgb_to_depth: np.ndarray | None = None
        self._last_query_sent_at = 0.0
        self._latest_query_id = 0

        self._rgb_socket = QWebSocket()
        self._rgb_socket.binaryMessageReceived.connect(self.on_rgb_binary)
        self._rgb_socket.connected.connect(self.on_rgb_socket_connected)
        self._rgb_socket.disconnected.connect(self.on_rgb_socket_disconnected)
        self._rgb_socket.errorOccurred.connect(self.on_rgb_socket_error)

        self._depth_socket = QWebSocket()
        self._depth_socket.binaryMessageReceived.connect(self.on_depth_binary)
        self._depth_socket.connected.connect(self.on_depth_socket_connected)
        self._depth_socket.disconnected.connect(self.on_depth_socket_disconnected)
        self._depth_socket.errorOccurred.connect(self.on_depth_socket_error)

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
        self.depth_info_label = QLabel("Depth Meta: frame=- size=-")
        self.depth_map_info_label = QLabel("Depth->RGB Overlay: disabled")
        self.rgb_label = QLabel("RGB Frame: waiting...")
        self.depth_label = QLabel("Depth Frame: waiting...")
        self.rgb_hover_label = QLabel("RGB Hover: -")
        self.depth_hover_label = QLabel("Depth: -")
        self.world_xyz_label = QLabel("World XYZ: -")
        self.camera_xyz_label = QLabel("Camera XYZ: -")
        self.hit_surface_label = QLabel("Hit Surface Label: -")
        self.rgb_label.setMinimumSize(640, 360)
        self.depth_label.setMinimumSize(640, 360)
        self.rgb_label.setMouseTracking(True)
        self.depth_label.setMouseTracking(True)
        self.rgb_label.installEventFilter(self)
        self.depth_label.installEventFilter(self)

        preview_row = QWidget()
        preview_layout = QHBoxLayout()
        preview_layout.addWidget(self.rgb_label)
        preview_layout.addWidget(self.depth_label)
        preview_row.setLayout(preview_layout)

        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.connection_label)
        status_layout.addWidget(self.device_label)
        status_layout.addWidget(self.tick_label)
        status_layout.addWidget(self.version_label)
        status_layout.addWidget(self.network_label)
        status_layout.addWidget(self.time_label)
        status_layout.addWidget(self.rgb_info_label)
        status_layout.addWidget(self.depth_info_label)
        status_layout.addWidget(self.depth_map_info_label)
        status_layout.addWidget(self.rgb_hover_label)
        status_layout.addWidget(self.depth_hover_label)
        status_layout.addWidget(self.world_xyz_label)
        status_layout.addWidget(self.camera_xyz_label)
        status_layout.addWidget(self.hit_surface_label)
        status_layout.addWidget(preview_row)
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

        self.depth_timer = QTimer(self)
        self.depth_timer.setInterval(16)
        self.depth_timer.timeout.connect(self.refresh_depth_preview)
        self.depth_timer.start()

        self.log_timer = QTimer(self)
        self.log_timer.setInterval(250)
        self.log_timer.timeout.connect(self.refresh_logs)
        self.log_timer.start()

        self.raycast_timer = QTimer(self)
        self.raycast_timer.setInterval(120)
        self.raycast_timer.timeout.connect(self.refresh_raycast_result)
        self.raycast_timer.start()

        self.rgb_reconnect_timer = QTimer(self)
        self.rgb_reconnect_timer.setInterval(1000)
        self.rgb_reconnect_timer.timeout.connect(self.ensure_rgb_socket_connected)
        self.rgb_reconnect_timer.start()

        self.depth_reconnect_timer = QTimer(self)
        self.depth_reconnect_timer.setInterval(1000)
        self.depth_reconnect_timer.timeout.connect(self.ensure_depth_socket_connected)
        self.depth_reconnect_timer.start()

        self.ensure_rgb_socket_connected()
        self.ensure_depth_socket_connected()
        self.load_alignment_lut()

    def load_alignment_lut(self) -> None:
        base_dir = Path(__file__).resolve().parents[1]
        candidates = [
            base_dir / "calib_capture" / "manual_plane_lut.npz",
            base_dir / "calib_capture" / "rgb_depth_lut.npz",
        ]

        for path in candidates:
            if not path.exists():
                continue
            try:
                data = np.load(path)
                self._map_depth_to_rgb_x = data["map_depth_to_rgb_x"].astype(np.float32)
                self._map_depth_to_rgb_y = data["map_depth_to_rgb_y"].astype(np.float32)
                self._valid_depth_to_rgb = data["valid_depth_to_rgb"].astype(np.uint8)
                self._map_rgb_to_depth_x = data["map_rgb_to_depth_x"].astype(np.float32)
                self._map_rgb_to_depth_y = data["map_rgb_to_depth_y"].astype(np.float32)
                self._valid_rgb_to_depth = data["valid_rgb_to_depth"].astype(np.uint8)
                self._lut_rgb_w = int(data["rgb_w"])
                self._lut_rgb_h = int(data["rgb_h"])
                self._lut_depth_w = int(data["depth_w"])
                self._lut_depth_h = int(data["depth_h"])
                self._lut_loaded = True
                self.append_log_item(
                    {
                        "timestamp_utc": "-",
                        "level": "INFO",
                        "source": "python",
                        "script": "run_dashboard.py",
                        "line": None,
                        "message": f"Loaded alignment LUT: {path}",
                        "stack_trace": "",
                    }
                )
                return
            except Exception as ex:
                self.append_log_item(
                    {
                        "timestamp_utc": "-",
                        "level": "WARNING",
                        "source": "python",
                        "script": "run_dashboard.py",
                        "line": None,
                        "message": f"Failed to load LUT {path}: {ex}",
                        "stack_trace": "",
                    }
                )

        self._lut_loaded = False

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
                f"Last Seen (UTC): {data.get('last_seen_utc', '-') }"
            )
            self.rgb_info_label.setText(
                f"RGB Meta: frame={data.get('last_rgb_frame_id', 0)} size={data.get('last_rgb_size', '-') }"
            )
            self.depth_info_label.setText(
                f"Depth Meta: frame={data.get('last_depth_frame_id', 0)} size={data.get('last_depth_size', '-') }"
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

            self._latest_rgb_width = pixmap.width()
            self._latest_rgb_height = pixmap.height()

            scaled = pixmap.scaled(
                self.rgb_label.width(),
                self.rgb_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.rgb_label.setText("")
            self.rgb_label.setPixmap(scaled)

            x = max(0, (self.rgb_label.width() - scaled.width()) // 2)
            y = max(0, (self.rgb_label.height() - scaled.height()) // 2)
            self._rgb_draw_rect = (x, y, scaled.width(), scaled.height())
        except Exception as ex:
            self.rgb_label.setText(
                f"RGB Frame: unavailable ({type(ex).__name__}: {ex})"
            )

    def refresh_depth_preview(self) -> None:
        try:
            pixmap = self._depth_preview_pixmap
            if pixmap is None:
                return

            scaled = pixmap.scaled(
                self.depth_label.width(),
                self.depth_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.depth_label.setText("")
            self.depth_label.setPixmap(scaled)

            x = max(0, (self.depth_label.width() - scaled.width()) // 2)
            y = max(0, (self.depth_label.height() - scaled.height()) // 2)
            self._depth_draw_rect = (x, y, scaled.width(), scaled.height())
        except Exception as ex:
            self.depth_label.setText(
                f"Depth Frame: unavailable ({type(ex).__name__}: {ex})"
            )

    def ensure_rgb_socket_connected(self) -> None:
        if self._rgb_socket.state() == QAbstractSocket.SocketState.ConnectedState:
            return

        if self._rgb_socket.state() == QAbstractSocket.SocketState.ConnectingState:
            return

        self._rgb_socket.open(QUrl("ws://127.0.0.1:8000/ws/rgb-preview"))

    def ensure_depth_socket_connected(self) -> None:
        if self._depth_socket.state() == QAbstractSocket.SocketState.ConnectedState:
            return

        if self._depth_socket.state() == QAbstractSocket.SocketState.ConnectingState:
            return

        self._depth_socket.open(QUrl("ws://127.0.0.1:8000/ws/depth-preview"))

    def on_rgb_binary(self, payload) -> None:
        self._latest_rgb_bytes = bytes(payload)

    def on_depth_binary(self, payload) -> None:
        packet = bytes(payload)
        parsed = self.parse_depth_packet(packet)
        if parsed is None:
            self.depth_label.setText("Depth Frame: invalid packet")
            return

        width, height, values = parsed
        self._latest_depth_width = width
        self._latest_depth_height = height
        self._latest_depth_values = values
        self._depth_valid_bbox = self.compute_valid_depth_bbox(width, height, values)

        image = self.depth_values_to_image(width, height, values)
        if image is None:
            self.depth_label.setText("Depth Frame: decode failed")
            return

        self._depth_preview_pixmap = QPixmap.fromImage(image)
        self.depth_map_info_label.setText("Depth->RGB Overlay: disabled")

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

    def on_depth_socket_connected(self) -> None:
        self.append_log_item(
            {
                "timestamp_utc": "-",
                "level": "INFO",
                "source": "python",
                "script": "run_dashboard.py",
                "line": None,
                "message": "Connected to /ws/depth-preview",
                "stack_trace": "",
            }
        )

    def on_depth_socket_disconnected(self) -> None:
        self.append_log_item(
            {
                "timestamp_utc": "-",
                "level": "WARNING",
                "source": "python",
                "script": "run_dashboard.py",
                "line": None,
                "message": "Disconnected from /ws/depth-preview",
                "stack_trace": "",
            }
        )

    def on_depth_socket_error(self, _error) -> None:
        self.depth_label.setText(
            f"Depth Frame: socket error ({self._depth_socket.errorString()})"
        )

    @staticmethod
    def parse_depth_packet(packet: bytes) -> tuple[int, int, list[float]] | None:
        if len(packet) < 36:
            return None

        try:
            (
                magic,
                frame_id,
                timestamp_ms,
                width,
                height,
                row_stride,
                pixel_stride,
                payload_len,
            ) = struct.unpack_from("<4sI q I I I I I", packet, 0)
        except struct.error:
            return None

        if magic != b"DEP1":
            return None

        if payload_len <= 0:
            return None

        if len(packet) < 36 + payload_len:
            return None

        payload = packet[36 : 36 + payload_len]
        expected = width * height * 4
        if expected <= 0:
            return None

        if payload_len != expected:
            return None

        try:
            values = list(struct.unpack(f"<{width * height}f", payload))
        except struct.error:
            return None

        return width, height, values

    @staticmethod
    def depth_values_to_image(
        width: int, height: int, values: list[float]
    ) -> QImage | None:
        if width <= 0 or height <= 0:
            return None

        if len(values) != width * height:
            return None

        valid_max = 0.0
        for depth in values:
            if depth > 0.0 and math.isfinite(depth):
                valid_max = max(valid_max, depth)

        if valid_max <= 0.0:
            valid_max = 5.0
        else:
            valid_max = min(valid_max, 8.0)

        pixels = bytearray(width * height)
        for i, depth in enumerate(values):
            if depth <= 0.0 or not math.isfinite(depth):
                pixels[i] = 0
                continue

            normalized = min(depth, valid_max) / valid_max
            pixels[i] = int((1.0 - normalized) * 255.0)

        image = QImage(
            bytes(pixels), width, height, width, QImage.Format.Format_Grayscale8
        )
        return image.copy()

    @staticmethod
    def depth_values_to_overlay_image(
        width: int, height: int, values: list[float]
    ) -> QImage | None:
        if width <= 0 or height <= 0:
            return None

        if len(values) != width * height:
            return None

        valid_max = 0.0
        for depth in values:
            if depth > 0.0 and math.isfinite(depth):
                valid_max = max(valid_max, depth)

        if valid_max <= 0.0:
            valid_max = 5.0
        else:
            valid_max = min(valid_max, 8.0)

        rgba = bytearray(width * height * 4)
        for i, depth in enumerate(values):
            base = i * 4
            if depth <= 0.0 or not math.isfinite(depth):
                rgba[base + 0] = 0
                rgba[base + 1] = 0
                rgba[base + 2] = 0
                rgba[base + 3] = 0
                continue

            t = min(depth, valid_max) / valid_max
            near = 1.0 - t
            r = int(255 * near)
            g = int(255 * (1.0 - abs(near - 0.5) * 2.0))
            b = int(255 * (1.0 - near))
            rgba[base + 0] = b
            rgba[base + 1] = g
            rgba[base + 2] = r
            rgba[base + 3] = 110

        image = QImage(
            bytes(rgba), width, height, width * 4, QImage.Format.Format_ARGB32
        )
        return image.copy()

    @staticmethod
    def compute_valid_depth_bbox(
        width: int, height: int, values: list[float]
    ) -> tuple[int, int, int, int] | None:
        min_x = width
        min_y = height
        max_x = -1
        max_y = -1

        for y in range(height):
            row_base = y * width
            for x in range(width):
                depth = values[row_base + x]
                if depth <= 0.0 or not math.isfinite(depth):
                    continue
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y

        if max_x < min_x or max_y < min_y:
            return None
        return min_x, min_y, max_x, max_y

    def compose_rgb_with_depth_overlay(self, rgb_pixmap: QPixmap) -> QPixmap | None:
        if self._depth_overlay_pixmap is None:
            return rgb_pixmap

        if self._lut_loaded:
            composed = self.compose_rgb_with_lut_overlay(rgb_pixmap)
            if composed is not None:
                return composed

        canvas = QPixmap(rgb_pixmap.size())
        canvas.fill(Qt.GlobalColor.transparent)

        painter = QPainter(canvas)
        try:
            painter.drawPixmap(0, 0, rgb_pixmap)

            overlay_scaled = self._depth_overlay_pixmap.scaled(
                rgb_pixmap.width(),
                rgb_pixmap.height(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(0, 0, overlay_scaled)

            if self._depth_valid_bbox is not None:
                x0, y0, x1, y1 = self._depth_valid_bbox
                dw = max(1, self._latest_depth_width)
                dh = max(1, self._latest_depth_height)
                rx0 = int(x0 * rgb_pixmap.width() / dw)
                ry0 = int(y0 * rgb_pixmap.height() / dh)
                rx1 = int((x1 + 1) * rgb_pixmap.width() / dw) - 1
                ry1 = int((y1 + 1) * rgb_pixmap.height() / dh) - 1
                rect_w = max(1, rx1 - rx0 + 1)
                rect_h = max(1, ry1 - ry0 + 1)

                pen = QPen(QColor(255, 255, 0, 220), 2)
                painter.setPen(pen)
                painter.drawRect(rx0, ry0, rect_w, rect_h)
        finally:
            painter.end()

        return canvas

    def compose_rgb_with_lut_overlay(self, rgb_pixmap: QPixmap) -> QPixmap | None:
        if (
            self._map_depth_to_rgb_x is None
            or self._map_depth_to_rgb_y is None
            or self._valid_depth_to_rgb is None
            or self._latest_depth_values is None
            or self._latest_depth_width <= 0
            or self._latest_depth_height <= 0
        ):
            return None

        if (
            self._latest_depth_width != self._lut_depth_w
            or self._latest_depth_height != self._lut_depth_h
            or rgb_pixmap.width() != self._lut_rgb_w
            or rgb_pixmap.height() != self._lut_rgb_h
        ):
            return None

        canvas = QPixmap(rgb_pixmap.size())
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        try:
            painter.drawPixmap(0, 0, rgb_pixmap)

            depth_values = self._latest_depth_values
            for y in range(self._lut_depth_h):
                row_base = y * self._lut_depth_w
                for x in range(self._lut_depth_w):
                    if self._valid_depth_to_rgb[y, x] == 0:
                        continue
                    depth_m = depth_values[row_base + x]
                    if depth_m <= 0.0 or not math.isfinite(depth_m):
                        continue

                    rx = int(round(float(self._map_depth_to_rgb_x[y, x])))
                    ry = int(round(float(self._map_depth_to_rgb_y[y, x])))
                    if (
                        rx < 0
                        or ry < 0
                        or rx >= self._lut_rgb_w
                        or ry >= self._lut_rgb_h
                    ):
                        continue

                    t = min(depth_m, 8.0) / 8.0
                    near = 1.0 - t
                    color = QColor(
                        int(255 * near),
                        int(255 * (1.0 - abs(near - 0.5) * 2.0)),
                        int(255 * (1.0 - near)),
                        110,
                    )
                    painter.setPen(color)
                    painter.drawPoint(rx, ry)
        finally:
            painter.end()

        return canvas

    def compute_depth_to_rgb_roi_for_current_frame(
        self,
    ) -> tuple[int, int, int, int] | None:
        if self._latest_depth_values is None:
            return None

        if self._lut_loaded and self._valid_depth_to_rgb is not None:
            if (
                self._latest_depth_width != self._lut_depth_w
                or self._latest_depth_height != self._lut_depth_h
                or self._map_depth_to_rgb_x is None
                or self._map_depth_to_rgb_y is None
            ):
                return None

            min_x = self._lut_rgb_w
            min_y = self._lut_rgb_h
            max_x = -1
            max_y = -1
            for y in range(self._lut_depth_h):
                row_base = y * self._lut_depth_w
                for x in range(self._lut_depth_w):
                    if self._valid_depth_to_rgb[y, x] == 0:
                        continue
                    depth_m = self._latest_depth_values[row_base + x]
                    if depth_m <= 0.0 or not math.isfinite(depth_m):
                        continue
                    rx = int(round(float(self._map_depth_to_rgb_x[y, x])))
                    ry = int(round(float(self._map_depth_to_rgb_y[y, x])))
                    if rx < min_x:
                        min_x = rx
                    if ry < min_y:
                        min_y = ry
                    if rx > max_x:
                        max_x = rx
                    if ry > max_y:
                        max_y = ry

            if max_x < min_x or max_y < min_y:
                return None
            return min_x, min_y, max_x - min_x + 1, max_y - min_y + 1

        if self._depth_valid_bbox is None:
            return None
        x0, y0, x1, y1 = self._depth_valid_bbox
        rgb_x0 = int(x0 * self._latest_rgb_width / max(1, self._latest_depth_width))
        rgb_y0 = int(y0 * self._latest_rgb_height / max(1, self._latest_depth_height))
        rgb_x1 = (
            int((x1 + 1) * self._latest_rgb_width / max(1, self._latest_depth_width))
            - 1
        )
        rgb_y1 = (
            int((y1 + 1) * self._latest_rgb_height / max(1, self._latest_depth_height))
            - 1
        )
        return rgb_x0, rgb_y0, max(1, rgb_x1 - rgb_x0 + 1), max(1, rgb_y1 - rgb_y0 + 1)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.depth_label:
            event_type = event.type()
            if event_type == QEvent.Type.MouseMove:
                pos = event.position().toPoint()
                self.update_depth_hover(pos.x(), pos.y())
            elif event_type == QEvent.Type.Leave:
                self.depth_hover_label.setText("Depth Hover: -")
        elif watched is self.rgb_label:
            event_type = event.type()
            if event_type == QEvent.Type.MouseMove:
                pos = event.position().toPoint()
                self.update_rgb_hover(pos.x(), pos.y())
            elif event_type == QEvent.Type.Leave:
                self.rgb_hover_label.setText("RGB Hover: -")

        return super().eventFilter(watched, event)

    def update_rgb_hover(self, mouse_x: int, mouse_y: int) -> None:
        if self._rgb_draw_rect is None:
            self.rgb_hover_label.setText("RGB Hover: -")
            return

        draw_x, draw_y, draw_w, draw_h = self._rgb_draw_rect
        if draw_w <= 0 or draw_h <= 0:
            self.rgb_hover_label.setText("RGB Hover: -")
            return

        if (
            mouse_x < draw_x
            or mouse_y < draw_y
            or mouse_x >= draw_x + draw_w
            or mouse_y >= draw_y + draw_h
        ):
            self.rgb_hover_label.setText("RGB Hover: -")
            return

        u = (mouse_x - draw_x) / max(1, draw_w)
        v = (mouse_y - draw_y) / max(1, draw_h)

        rgb_x = min(max(0, self._latest_rgb_width - 1), int(u * self._latest_rgb_width))
        rgb_y = min(
            max(0, self._latest_rgb_height - 1), int(v * self._latest_rgb_height)
        )
        self.rgb_hover_label.setText(
            f"RGB Hover: rgb=({rgb_x}, {rgb_y}) uv=({u:.4f}, {v:.4f})"
        )

        now = time.time()
        if now - self._last_query_sent_at < 0.06:
            return

        self._last_query_sent_at = now
        try:
            result = post_raycast_query(u, v)
            if result.get("ok"):
                self._latest_query_id = int(result.get("query_id", 0))
        except Exception:
            pass

    def update_depth_hover(self, mouse_x: int, mouse_y: int) -> None:
        if self._depth_draw_rect is None:
            self.depth_hover_label.setText("Depth Hover: -")
            return

        if self._latest_depth_values is None:
            self.depth_hover_label.setText("Depth Hover: -")
            return

        draw_x, draw_y, draw_w, draw_h = self._depth_draw_rect
        if draw_w <= 0 or draw_h <= 0:
            self.depth_hover_label.setText("Depth Hover: -")
            return

        if (
            mouse_x < draw_x
            or mouse_y < draw_y
            or mouse_x >= draw_x + draw_w
            or mouse_y >= draw_y + draw_h
        ):
            self.depth_hover_label.setText("Depth Hover: -")
            return

        u = (mouse_x - draw_x) / max(1, draw_w)
        v = (mouse_y - draw_y) / max(1, draw_h)

        src_x = min(self._latest_depth_width - 1, int(u * self._latest_depth_width))
        src_y = min(self._latest_depth_height - 1, int(v * self._latest_depth_height))
        idx = src_y * self._latest_depth_width + src_x
        if idx < 0 or idx >= len(self._latest_depth_values):
            self.depth_hover_label.setText("Depth Hover: -")
            return

        depth_m = self._latest_depth_values[idx]
        if depth_m <= 0.0 or not math.isfinite(depth_m):
            self.depth_hover_label.setText(f"Depth Hover: ({src_x}, {src_y}) invalid")
            return

        self.depth_hover_label.setText(
            f"Depth Hover: ({src_x}, {src_y}) {depth_m:.3f} m"
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

    def refresh_raycast_result(self) -> None:
        try:
            item = fetch_raycast_result()
            if not item:
                return

            qid = int(item.get("query_id") or 0)
            if self._latest_query_id > 0 and qid > 0 and qid < self._latest_query_id:
                return

            hit = bool(item.get("hit", False))
            label = item.get("hit_surface_label") or "-"
            self.hit_surface_label.setText(f"Hit Surface Label: {label}")

            if not hit:
                self.depth_hover_label.setText("Depth: no hit")
                self.world_xyz_label.setText("World XYZ: -")
                self.camera_xyz_label.setText("Camera XYZ: -")
                return

            depth_m = item.get("depth_m")
            if depth_m is None:
                self.depth_hover_label.setText("Depth: -")
                self.world_xyz_label.setText("World XYZ: -")
                self.camera_xyz_label.setText("Camera XYZ: -")
                return
            world_xyz = item.get("world_xyz") or [0.0, 0.0, 0.0]
            camera_xyz = item.get("camera_xyz") or [0.0, 0.0, 0.0]

            self.depth_hover_label.setText(f"Depth: {float(depth_m):.3f} m")
            self.world_xyz_label.setText(
                f"World XYZ: ({float(world_xyz[0]):.3f}, {float(world_xyz[1]):.3f}, {float(world_xyz[2]):.3f})"
            )
            self.camera_xyz_label.setText(
                f"Camera XYZ: ({float(camera_xyz[0]):.3f}, {float(camera_xyz[1]):.3f}, {float(camera_xyz[2]):.3f})"
            )
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

    def closeEvent(self, event) -> None:
        self._rgb_socket.abort()
        self._depth_socket.abort()
        super().closeEvent(event)


def main() -> int:
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    time.sleep(0.6)

    app = QApplication([])
    window = DashboardWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
