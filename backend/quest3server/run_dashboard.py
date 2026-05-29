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
from PySide6.QtCore import QEvent, Qt, QTimer
try:
    from PySide6.QtCore import QUrl
except ImportError:
    pass
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

API_BASE = "http://127.0.0.1:8500"


# ═══════════════════════ API helpers ══════════════════════════════════


def start_server() -> None:
    uvicorn.run("quest3server.main:app", host="0.0.0.0", port=8500, log_level="info")


def fetch_json(path: str) -> dict:
    req = urllib.request.Request(f"{API_BASE}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=1.0) as res:
        return json.loads(res.read().decode("utf-8"))


def post_json(path: str, body: dict) -> dict:
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15.0) as res:
        return json.loads(res.read().decode("utf-8"))


# ═══════════════════════ Dashboard Window ═════════════════════════════


class DashboardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Smart Room Dashboard")
        self.resize(1100, 750)

        self._last_log_id = 0

        # RGB preview state
        self._latest_rgb_bytes: bytes | None = None
        self._latest_rgb_width = 0
        self._latest_rgb_height = 0
        self._rgb_draw_rect: tuple[int, int, int, int] | None = None
        self._rgb_hover_pixel: tuple[int, int] | None = None  # (x, y) in source coords

        # Depth preview state
        self._latest_depth_values: list[float] | None = None
        self._latest_depth_width = 0
        self._latest_depth_height = 0
        self._depth_preview_pixmap: QPixmap | None = None
        self._depth_draw_rect: tuple[int, int, int, int] | None = None

        # Tracking state
        self._latest_track_result: dict | None = None
        self._track_bbox_pixel: tuple[int, int, int, int] | None = None  # (x0,y0,x1,y1)
        self._track_label: str = ""
        self._model_status: dict = {}
        self._crop_pixmap: QPixmap | None = None

        # LUT
        self._lut_loaded = False
        self._lut_rgb_w = 0
        self._lut_rgb_h = 0
        self._lut_depth_w = 0
        self._lut_depth_h = 0

        # ── WebSocket connections ──

        self._rgb_socket = QWebSocket()
        self._rgb_socket.binaryMessageReceived.connect(self._on_rgb_binary)

        self._depth_socket = QWebSocket()
        self._depth_socket.binaryMessageReceived.connect(self._on_depth_binary)

        # ── Build UI ──

        tabs = QTabWidget()
        tabs.addTab(self._build_status_tab(), "Status")
        tabs.addTab(self._build_preview_tab(), "Preview")
        tabs.addTab(self._build_tracking_tab(), "Tracking")
        tabs.addTab(self._build_logs_tab(), "Logs")

        root = QWidget()
        root_layout = QVBoxLayout()
        root_layout.addWidget(tabs)
        root.setLayout(root_layout)
        self.setCentralWidget(root)

        # ── Timers ──

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(300)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(33)
        self._preview_timer.timeout.connect(self._refresh_previews)
        self._preview_timer.start()

        self._log_timer = QTimer(self)
        self._log_timer.setInterval(250)
        self._log_timer.timeout.connect(self._refresh_logs)
        self._log_timer.start()

        self._tracking_timer = QTimer(self)
        self._tracking_timer.setInterval(500)
        self._tracking_timer.timeout.connect(self._refresh_tracking_status)
        self._tracking_timer.start()

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(1000)
        self._reconnect_timer.timeout.connect(self._ensure_sockets)
        self._reconnect_timer.start()

        self._ensure_sockets()
        self._load_alignment_lut()

    # ═══════════════════════ UI: Status Tab ═══════════════════════════

    def _build_status_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()

        # Connection group
        conn = QGroupBox("Connection")
        conn_layout = QVBoxLayout()
        self._lbl_status = QLabel("Backend: starting...")
        self._lbl_headset = QLabel("Headset: -")
        self._lbl_device = QLabel("Device: -")
        self._lbl_tick = QLabel("Tick: 0")
        self._lbl_version = QLabel("App Version: -")
        self._lbl_network = QLabel("Connection Mode: -")
        self._lbl_last_seen = QLabel("Last Seen: -")
        for lbl in [self._lbl_status, self._lbl_headset, self._lbl_device,
                     self._lbl_tick, self._lbl_version, self._lbl_network,
                     self._lbl_last_seen]:
            conn_layout.addWidget(lbl)
        conn.setLayout(conn_layout)
        layout.addWidget(conn)

        # Streams group
        streams = QGroupBox("Streams")
        streams_layout = QVBoxLayout()
        self._lbl_rgb = QLabel("RGB: -")
        self._lbl_depth = QLabel("Depth: -")
        self._lbl_intrinsics = QLabel("Intrinsics: -")
        self._lbl_vram = QLabel("GPU VRAM: -")
        streams_layout.addWidget(self._lbl_rgb)
        streams_layout.addWidget(self._lbl_depth)
        streams_layout.addWidget(self._lbl_intrinsics)
        streams_layout.addWidget(self._lbl_vram)
        streams.setLayout(streams_layout)
        layout.addWidget(streams)

        # Tracking group
        track = QGroupBox("Tracking Engine")
        track_layout = QVBoxLayout()
        self._lbl_track_models = QLabel("Models: -")
        self._lbl_track_state = QLabel("State: idle")
        self._lbl_track_label = QLabel("Label: -")
        track_layout.addWidget(self._lbl_track_models)
        track_layout.addWidget(self._lbl_track_state)
        track_layout.addWidget(self._lbl_track_label)
        track.setLayout(track_layout)
        layout.addWidget(track)

        layout.addStretch()
        w.setLayout(layout)
        return w

    # ═══════════════════════ UI: Preview Tab ═════════════════════════

    def _build_preview_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()

        # Hover info bar
        self._lbl_preview_hover = QLabel("Hover over image for pixel coords and depth")
        layout.addWidget(self._lbl_preview_hover)

        # Single combined RGB + depth overlay image
        self._preview_label = QLabel("RGB + Depth: waiting...")
        self._preview_label.setMinimumSize(640, 360)
        self._preview_label.setMouseTracking(True)
        self._preview_label.installEventFilter(self)
        layout.addWidget(self._preview_label)

        w.setLayout(layout)
        return w

    # ═══════════════════════ UI: Tracking Tab ════════════════════════

    def _build_tracking_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()

        # Controls
        ctrl_row = QWidget()
        ctrl_layout = QHBoxLayout()
        self._btn_track_center = QPushButton("Detect at Center")
        self._btn_track_center.clicked.connect(self._on_detect_center)
        self._btn_track_stop = QPushButton("Stop Tracking")
        self._btn_track_stop.clicked.connect(self._on_stop_tracking)
        ctrl_layout.addWidget(self._btn_track_center)
        ctrl_layout.addWidget(self._btn_track_stop)
        ctrl_layout.addStretch()
        ctrl_row.setLayout(ctrl_layout)
        layout.addWidget(ctrl_row)

        # Manual pixel input
        input_row = QWidget()
        input_layout = QHBoxLayout()
        input_layout.addWidget(QLabel("Pixel X:"))
        self._input_px = QPushButton("320")
        self._input_px.setMaximumWidth(80)
        input_layout.addWidget(self._input_px)
        input_layout.addWidget(QLabel("Y:"))
        self._input_py = QPushButton("240")
        self._input_py.setMaximumWidth(80)
        input_layout.addWidget(self._input_py)
        self._btn_track_coords = QPushButton("Detect")
        self._btn_track_coords.clicked.connect(self._on_detect_coords)
        input_layout.addWidget(self._btn_track_coords)
        input_layout.addStretch()
        input_row.setLayout(input_layout)
        layout.addWidget(input_row)

        # Result display
        result_group = QGroupBox("Last Detection")
        result_layout = QVBoxLayout()
        self._lbl_result_label = QLabel("Label: -")
        self._lbl_result_score = QLabel("Score: -")
        self._lbl_result_bbox = QLabel("Bbox: -")
        self._lbl_result_time = QLabel("Time: -")
        result_layout.addWidget(self._lbl_result_label)
        result_layout.addWidget(self._lbl_result_score)
        result_layout.addWidget(self._lbl_result_bbox)
        result_layout.addWidget(self._lbl_result_time)
        result_group.setLayout(result_layout)
        layout.addWidget(result_group)

        # Original frame with cursor marker
        orig_group = QGroupBox("Original Frame (with cursor)")
        orig_layout = QVBoxLayout()
        self._lbl_original_frame = QLabel("No trigger yet")
        self._lbl_original_frame.setMinimumSize(320, 180)
        self._lbl_original_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_original_frame.setStyleSheet(
            "background-color: #1a1a1a; border-radius: 4px;"
        )
        orig_layout.addWidget(self._lbl_original_frame)
        orig_group.setLayout(orig_layout)
        layout.addWidget(orig_group)

        # Depth top-down (bird's-eye) view
        td_group = QGroupBox("Depth Top-Down (bird's-eye, red cross = cursor)")
        td_layout = QVBoxLayout()
        self._lbl_topdown = QLabel("No depth data yet")
        self._lbl_topdown.setMinimumSize(320, 240)
        self._lbl_topdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_topdown.setStyleSheet(
            "background-color: #1a1a1a; border-radius: 4px;"
        )
        td_layout.addWidget(self._lbl_topdown)
        td_group.setLayout(td_layout)
        layout.addWidget(td_group)

        # Crop preview
        crop_group = QGroupBox("Detection Crop")
        crop_layout = QVBoxLayout()
        self._lbl_crop = QLabel("No detection yet")
        self._lbl_crop.setMinimumSize(200, 150)
        self._lbl_crop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_crop.setStyleSheet("background-color: #1a1a1a; border-radius: 4px;")
        crop_layout.addWidget(self._lbl_crop)
        crop_group.setLayout(crop_layout)
        layout.addWidget(crop_group)

        layout.addStretch()
        w.setLayout(layout)
        return w

    # ═══════════════════════ UI: Logs Tab ════════════════════════════

    def _build_logs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setPlaceholderText("Log stream will appear here...")
        layout.addWidget(self._log_view)
        w.setLayout(layout)
        return w

    # ═══════════════════════ Timer callbacks ═════════════════════════

    def _refresh_status(self) -> None:
        try:
            data = fetch_json("/api/status")
            p = data.get("last_payload", {})
            self._lbl_status.setText("Backend: ✓ running")
            self._lbl_headset.setText(
                f"Headset: {'connected' if data.get('connected') else 'disconnected'} "
                f"(active={data.get('active_connections', 0)})"
            )
            self._lbl_device.setText(f"Device: {p.get('device_model', '-')}")
            self._lbl_tick.setText(f"Tick: {data.get('last_tick', 0)}")
            self._lbl_version.setText(f"App: {p.get('app_version', '-')}")
            self._lbl_network.setText(f"Connection: {p.get('connection_mode', '-')}")
            self._lbl_last_seen.setText(f"Last Seen: {data.get('last_seen_utc', '-')}")
            self._lbl_rgb.setText(f"RGB: frame={data.get('last_rgb_frame_id',0)} {data.get('last_rgb_size','-')}")
            self._lbl_depth.setText(f"Depth: frame={data.get('last_depth_frame_id',0)} {data.get('last_depth_size','-')}")

            # Intrinsics
            ci = data.get("camera_intrinsics", {})
            if ci.get("fx"):
                self._lbl_intrinsics.setText(f"Intrinsics: fx={ci['fx']:.1f} fy={ci['fy']:.1f} cx={ci['cx']:.1f} cy={ci['cy']:.1f}")
        except urllib.error.URLError:
            self._lbl_status.setText("Backend: ✗ disconnected")
        except Exception as ex:
            self._lbl_status.setText(f"Backend: error ({ex})")

    def _refresh_tracking_status(self) -> None:
        try:
            data = fetch_json("/api/track/status")
            active = data.get("active", False)
            state = data.get("state", "idle")
            label = data.get("label", "")

            self._lbl_track_state.setText(
                f"State: {'● ' + state if active else '○ ' + state}"
            )
            if label:
                self._lbl_track_label.setText(f"Label: {label}")

            # Model status
            ms = fetch_json("/api/models/status")
            self._model_status = ms
            parts = []
            parts.append(f"SAM2: {'✓' if ms.get('sam2') else '✗'}")
            parts.append(f"Florence-2: {'✓' if ms.get('florence2') else '✗'}")
            parts.append(f"SigLIP2: {'✓' if ms.get('clip') else '✗'}")
            self._lbl_track_models.setText("Models: " + "  |  ".join(parts))

            # Crop refresh
            if active and state == "tracking":
                self._refresh_crop()
                self._refresh_original_frame()
                self._refresh_topdown()
        except Exception:
            self._lbl_track_models.setText("Models: -")

    def _refresh_crop(self) -> None:
        """Fetch the latest detection crop and display it."""
        try:
            req = urllib.request.Request(f"{API_BASE}/api/track/last-crop", method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as res:
                data = res.read()
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                scaled = pixmap.scaled(
                    self._lbl_crop.width(), self._lbl_crop.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._lbl_crop.setPixmap(scaled)
                self._lbl_crop.setText("")
        except urllib.error.HTTPError:
            pass  # 404 = no crop yet
        except Exception:
            pass

    def _refresh_original_frame(self) -> None:
        """Fetch the original frame with cursor crosshair."""
        try:
            req = urllib.request.Request(
                f"{API_BASE}/api/track/last-original", method="GET",
            )
            with urllib.request.urlopen(req, timeout=1.0) as res:
                data = res.read()
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                scaled = pixmap.scaled(
                    self._lbl_original_frame.width(),
                    self._lbl_original_frame.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._lbl_original_frame.setPixmap(scaled)
                self._lbl_original_frame.setText("")
        except urllib.error.HTTPError:
            pass
        except Exception:
            pass

    def _refresh_topdown(self) -> None:
        """Fetch the depth top-down (bird's-eye) view."""
        try:
            req = urllib.request.Request(
                f"{API_BASE}/api/depth/topdown", method="GET",
            )
            with urllib.request.urlopen(req, timeout=1.0) as res:
                data = res.read()
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                scaled = pixmap.scaled(
                    self._lbl_topdown.width(),
                    self._lbl_topdown.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._lbl_topdown.setPixmap(scaled)
                self._lbl_topdown.setText("")
        except urllib.error.HTTPError:
            pass
        except Exception:
            pass

    def _refresh_previews(self) -> None:
        self._refresh_combined_preview()

    def _refresh_combined_preview(self) -> None:
        """Draw RGB image with depth heatmap overlaid."""
        if self._latest_rgb_bytes is None:
            return

        rgb_pix = QPixmap()
        if not rgb_pix.loadFromData(self._latest_rgb_bytes):
            return

        self._latest_rgb_width = rgb_pix.width()
        self._latest_rgb_height = rgb_pix.height()

        # Start with the RGB image as base canvas
        canvas = QPixmap(rgb_pix.size())
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        try:
            painter.drawPixmap(0, 0, rgb_pix)

            # Draw tracking bbox if we have one
            if self._track_bbox_pixel is not None:
                x0, y0, x1, y1 = self._track_bbox_pixel
                pen = QPen(QColor(0, 255, 128, 255), 3)
                painter.setPen(pen)
                painter.drawRect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
                if self._track_label:
                    painter.setPen(QPen(QColor(0, 0, 0, 200)))
                    font = painter.font()
                    font.setPointSize(12)
                    font.setBold(True)
                    painter.setFont(font)
                    painter.drawText(x0, max(0, y0 - 6), self._track_label)

            # Overlay depth heatmap at 40% opacity
            if self._depth_preview_pixmap is not None:
                depth_scaled = self._depth_preview_pixmap.scaled(
                    rgb_pix.size(),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                painter.setOpacity(0.4)
                painter.drawPixmap(0, 0, depth_scaled)
                painter.setOpacity(1.0)
        finally:
            painter.end()

        # Scale to fit the label
        scaled = canvas.scaled(
            self._preview_label.width(), self._preview_label.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview_label.setPixmap(scaled)

        # Cache draw rect for hover coordinate mapping
        rx = max(0, (self._preview_label.width() - scaled.width()) // 2)
        ry = max(0, (self._preview_label.height() - scaled.height()) // 2)
        self._preview_draw_rect = (rx, ry, scaled.width(), scaled.height())

    # ── old _refresh_rgb and _refresh_depth removed; replaced above ──

    def _refresh_logs(self) -> None:
        try:
            req = urllib.request.Request(
                f"{API_BASE}/api/logs?since_id={self._last_log_id}&limit=300",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=0.5) as res:
                data = json.loads(res.read().decode("utf-8"))
            for item in data.get("logs", []):
                self._last_log_id = max(self._last_log_id, int(item.get("id", 0)))
                self._append_log(item)
        except Exception:
            pass

    # ═══════════════════════ WebSocket ════════════════════════════════

    def _ensure_sockets(self) -> None:
        if self._rgb_socket.state() not in (
            QAbstractSocket.SocketState.ConnectedState,
            QAbstractSocket.SocketState.ConnectingState,
        ):
            self._rgb_socket.open(QUrl("ws://127.0.0.1:8500/ws/rgb-preview"))
        if self._depth_socket.state() not in (
            QAbstractSocket.SocketState.ConnectedState,
            QAbstractSocket.SocketState.ConnectingState,
        ):
            self._depth_socket.open(QUrl("ws://127.0.0.1:8500/ws/depth-preview"))

    def _on_rgb_binary(self, payload) -> None:
        self._latest_rgb_bytes = bytes(payload)

    def _on_depth_binary(self, payload) -> None:
        packet = bytes(payload)
        parsed = self._parse_depth_packet(packet)
        if parsed is None:
            return
        width, height, values = parsed
        self._latest_depth_width = width
        self._latest_depth_height = height
        self._latest_depth_values = values

        image = self._depth_to_image(width, height, values)
        if image is not None:
            self._depth_preview_pixmap = QPixmap.fromImage(image)

    # ═══════════════════════ Tracking actions ════════════════════════

    def _on_detect_center(self) -> None:
        """Detect at center of RGB frame."""
        if self._latest_rgb_width <= 0:
            self._lbl_result_label.setText("Label: no RGB frame")
            return
        self._do_detect(self._latest_rgb_width // 2, self._latest_rgb_height // 2)

    def _on_detect_coords(self) -> None:
        """Detect at manually entered coordinates."""
        try:
            px = int(self._input_px.text())
            py = int(self._input_py.text())
        except ValueError:
            self._lbl_result_label.setText("Label: invalid coords")
            return
        self._do_detect(px, py)

    def _do_detect(self, px: int, py: int) -> None:
        self._lbl_result_label.setText("Label: detecting...")
        self._lbl_result_time.setText("Time: ...")
        t0 = time.time()
        try:
            data = post_json("/api/track/start", {"pixel_x": px, "pixel_y": py})
            elapsed = time.time() - t0
            result = data.get("result", {})
            self._latest_track_result = result

            label = result.get("label", "?")
            score = result.get("score", 0)
            bbox = result.get("box_xyxy", [0, 0, 0, 0])

            self._lbl_result_label.setText(f"Label: {label}")
            self._lbl_result_score.setText(f"Score: {score:.4f}")
            self._lbl_result_bbox.setText(f"Bbox: {bbox}")
            self._lbl_result_time.setText(f"Time: {elapsed:.2f}s")

            # Store for RGB overlay
            if len(bbox) == 4 and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                self._track_bbox_pixel = (bbox[0], bbox[1], bbox[2], bbox[3])
                self._track_label = label
            else:
                self._track_bbox_pixel = None
                self._track_label = ""

            self._refresh_tracking_status()
        except urllib.error.HTTPError as ex:
            self._lbl_result_label.setText(f"Label: HTTP {ex.code}")
            self._lbl_result_time.setText(f"Time: {time.time() - t0:.2f}s")
        except Exception as ex:
            self._lbl_result_label.setText(f"Label: error ({ex})")
            self._lbl_result_time.setText(f"Time: {time.time() - t0:.2f}s")

    def _on_stop_tracking(self) -> None:
        try:
            post_json("/api/track/stop", {})
            self._track_bbox_pixel = None
            self._track_label = ""
            self._latest_track_result = None
            self._crop_pixmap = None
            self._lbl_crop.setText("No detection yet")
            self._lbl_crop.setPixmap(QPixmap())
            self._lbl_original_frame.setText("No trigger yet")
            self._lbl_original_frame.setPixmap(QPixmap())
            self._lbl_topdown.setText("No depth data yet")
            self._lbl_topdown.setPixmap(QPixmap())
            self._lbl_result_label.setText("Label: -")
            self._lbl_result_score.setText("Score: -")
            self._lbl_result_bbox.setText("Bbox: -")
        except Exception:
            pass

    # ═══════════════════════ RGB click-to-detect ═════════════════════

    def eventFilter(self, watched, event) -> bool:
        if watched is self._preview_label:
            etype = event.type()
            if etype == QEvent.Type.MouseMove:
                pos = event.position().toPoint()
                self._update_preview_hover(pos.x(), pos.y())
            elif etype == QEvent.Type.MouseButtonPress:
                pos = event.position().toPoint()
                self._on_rgb_click(pos.x(), pos.y())
            elif etype == QEvent.Type.Leave:
                self._lbl_preview_hover.setText(
                    "Hover over image for pixel coords and depth"
                )
        return super().eventFilter(watched, event)

    def _update_preview_hover(self, mx: int, my: int) -> None:
        if not hasattr(self, '_preview_draw_rect') or self._preview_draw_rect is None:
            return
        dx, dy, dw, dh = self._preview_draw_rect
        if dw <= 0 or dh <= 0:
            return
        if mx < dx or my < dy or mx >= dx + dw or my >= dy + dh:
            return

        u = (mx - dx) / dw
        v = (my - dy) / dh
        px = int(u * self._latest_rgb_width)
        py = int(v * self._latest_rgb_height)

        # Query depth from aligned depth API (may return None)
        depth_str = "-"
        try:
            req = urllib.request.Request(
                f"{API_BASE}/api/depth/at?px={px}&py={py}", method="GET",
            )
            with urllib.request.urlopen(req, timeout=0.3) as res:
                d = json.loads(res.read().decode("utf-8"))
            dm = d.get("depth_m")
            if dm is not None:
                depth_str = f"{dm:.3f}m"
            else:
                depth_str = "(no depth)"
        except Exception:
            depth_str = "(query failed)"

        self._lbl_preview_hover.setText(
            f"Pixel: ({px}, {py})  |  Depth: {depth_str}"
        )

    def _on_rgb_click(self, mx: int, my: int) -> None:
        """Click on RGB preview → trigger tracking at that point."""
        if not hasattr(self, '_preview_draw_rect') or self._preview_draw_rect is None:
            return
        dx, dy, dw, dh = self._preview_draw_rect
        if dw <= 0 or dh <= 0:
            return
        if mx < dx or my < dy or mx >= dx + dw or my >= dy + dh:
            return

        u = (mx - dx) / dw
        v = (my - dy) / dh
        px = int(u * self._latest_rgb_width)
        py = int(v * self._latest_rgb_height)
        self._do_detect(px, py)

    # ═══════════════════════ Depth helpers ════════════════════════════

    @staticmethod
    def _parse_depth_packet(packet: bytes) -> tuple[int, int, list[float]] | None:
        if len(packet) < 36:
            return None
        try:
            magic, _, _, width, height, _, _, payload_len = struct.unpack_from(
                "<4sI q I I I I I", packet, 0
            )
        except struct.error:
            return None
        if magic != b"DEP1":
            return None
        if payload_len <= 0 or len(packet) < 36 + payload_len:
            return None
        expected = width * height * 4
        if payload_len != expected:
            return None
        try:
            values = list(struct.unpack(f"<{width * height}f", packet[36:36 + payload_len]))
        except struct.error:
            return None
        return width, height, values

    @staticmethod
    def _depth_to_image(width: int, height: int, values: list[float]) -> QImage | None:
        if width <= 0 or height <= 0 or len(values) != width * height:
            return None
        valid_max = min(max(v for v in values if v > 0 and math.isfinite(v)), 8.0) if any(
            v > 0 and math.isfinite(v) for v in values
        ) else 5.0
        if valid_max <= 0:
            valid_max = 5.0

        pixels = bytearray(width * height)
        for i, d in enumerate(values):
            if d > 0 and math.isfinite(d):
                pixels[i] = int((1.0 - min(d, valid_max) / valid_max) * 255)
            else:
                pixels[i] = 0

        img = QImage(bytes(pixels), width, height, width, QImage.Format.Format_Grayscale8)
        return img.copy()

    # ═══════════════════════ Alignment LUT ════════════════════════════

    def _load_alignment_lut(self) -> None:
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
                self._lut_rgb_w = int(data["rgb_w"])
                self._lut_rgb_h = int(data["rgb_h"])
                self._lut_depth_w = int(data["depth_w"])
                self._lut_depth_h = int(data["depth_h"])
                self._lut_loaded = True
                self._log_info(f"Loaded alignment LUT: {path.name}")
                return
            except Exception:
                pass
        self._lut_loaded = False

    # ═══════════════════════ Log display ══════════════════════════════

    def _log_info(self, msg: str) -> None:
        self._append_log({
            "timestamp_utc": "-", "level": "INFO", "source": "python",
            "script": "run_dashboard.py", "line": None,
            "message": msg, "stack_trace": "",
        })

    def _append_log(self, item: dict) -> None:
        level = str(item.get("level", "INFO")).upper()
        source = item.get("source", "unknown")
        script = item.get("script", "-")
        line = item.get("line")
        ts = item.get("timestamp_utc", "-")
        msg = item.get("message", "")
        stack = item.get("stack_trace", "")

        color = {"ERROR": "#d32f2f", "WARNING": "#f57c00", "INFO": "#9e9e9e"}.get(level, "#9e9e9e")
        line_info = f":{line}" if line is not None else ""
        self._log_view.append(
            f"<div style='color:{color};'>[{ts}] [{level}] [{source}] {script}{line_info} - {msg}</div>"
        )
        if stack:
            esc = stack.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            self._log_view.append(f"<div style='color:#757575;margin-left:12px;'>stack: {esc}</div>")

    # ═══════════════════════ Cleanup ══════════════════════════════════

    def closeEvent(self, event) -> None:
        self._rgb_socket.abort()
        self._depth_socket.abort()
        super().closeEvent(event)


# ═══════════════════════ Entry point ══════════════════════════════════


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
