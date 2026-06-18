from __future__ import annotations

from collections import deque
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
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QFrame,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

API_BASE = "http://127.0.0.1:8500"


# ═══════════════════════ API helpers ══════════════════════════════════


def start_server() -> None:
    uvicorn.run("quest3server.main:app", host="0.0.0.0", port=8500, log_level="info", ws_ping_interval=0)


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


def _fmt_wh(width, height) -> str:
    if width in (None, "", 0) or height in (None, "", 0):
        return "-"
    return f"{width}x{height}"


# ═══════════════════════ Dashboard Window ═════════════════════════════


class DashboardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Smart Room Dashboard")
        self.resize(1100, 750)
        self._apply_styles()

        self._last_log_id = 0
        self._recent_events: deque[str] = deque(maxlen=10)

        # RGB preview state
        self._latest_rgb_bytes: bytes | None = None
        self._latest_rgb_width = 0
        self._latest_rgb_height = 0
        self._raw_rgb_pixmap: QPixmap | None = None
        self._rgb_draw_rect: tuple[int, int, int, int] | None = None
        self._rgb_hover_pixel: tuple[int, int] | None = None  # (x, y) in source coords

        # Depth preview state
        self._latest_depth_values: list[float] | None = None
        self._latest_depth_width = 0
        self._latest_depth_height = 0
        self._depth_preview_pixmap: QPixmap | None = None
        self._aligned_depth_pixmap: QPixmap | None = None  # from /api/depth/aligned-heatmap
        self._depth_draw_rect: tuple[int, int, int, int] | None = None

        # Overlay state
        self._rgbd_overlay_bytes: bytes | None = None
        self._rgbd_overlay_pixmap: QPixmap | None = None

        # Tracking state
        self._latest_track_result: dict | None = None
        self._track_bbox_pixel: tuple[int, int, int, int] | None = None  # (x0,y0,x1,y1)
        self._track_label: str = ""
        self._model_status: dict = {}
        self._crop_pixmap: QPixmap | None = None
        self._latest_status_snapshot: dict = {}

        # LUT
        self._lut_loaded = False
        self._lut_rgb_w = 0
        self._lut_rgb_h = 0
        self._lut_depth_w = 0
        self._lut_depth_h = 0

        # ── WebSocket connections ──

        self._rgbd_overlay_socket = QWebSocket()
        self._rgbd_overlay_socket.binaryMessageReceived.connect(
            self._on_rgbd_overlay_binary
        )

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
        self._status_timer.timeout.connect(self._refresh_status_v2)
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

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background-color: #0b1220;
                color: #dce7f5;
                font-size: 13px;
            }
            QTabWidget::pane {
                border: 1px solid #1f2b3d;
                background: #0f1727;
                border-radius: 10px;
                top: -1px;
            }
            QTabBar::tab {
                background: #142036;
                color: #8ea2bd;
                padding: 10px 16px;
                margin-right: 6px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
            QTabBar::tab:selected {
                background: #1c2c49;
                color: #f4f8fc;
            }
            QGroupBox {
                border: 1px solid #20314a;
                border-radius: 12px;
                margin-top: 10px;
                padding-top: 14px;
                background: #101a2b;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #f4f8fc;
            }
            QLabel[role="hero"] {
                font-size: 18px;
                font-weight: 700;
                color: #f8fbff;
                padding: 2px 0 10px 0;
            }
            QLabel[role="subtle"] {
                color: #8ea2bd;
            }
            QFrame[card="true"] {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #101a2b, stop:1 #0f1a2d);
                border: 1px solid #20314a;
                border-radius: 14px;
            }
            QLabel[role="cardTitle"] {
                color: #f8fbff;
                font-size: 14px;
                font-weight: 700;
                padding-bottom: 6px;
            }
            QLabel[role="pill"] {
                border-radius: 14px;
                padding: 7px 12px;
                font-weight: 700;
                color: #f8fbff;
            }
            QTextEdit {
                background: #0a1322;
                border: 1px solid #20314a;
                border-radius: 10px;
                padding: 8px;
            }
            QPushButton {
                background: #1a3156;
                border: 1px solid #2f4d78;
                border-radius: 8px;
                padding: 8px 12px;
                color: #f4f8fc;
            }
            QPushButton:hover {
                background: #21406f;
            }
            """
        )

    def _make_pill(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("role", "pill")
        self._set_pill_state(lbl, "idle")
        return lbl

    def _make_card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setProperty("card", "true")
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)
        title_lbl = QLabel(title)
        title_lbl.setProperty("role", "cardTitle")
        layout.addWidget(title_lbl)
        card.setLayout(layout)
        return card, layout

    def _set_pill_state(self, lbl: QLabel, state: str) -> None:
        palette = {
            "ok": ("#0f3b2f", "#1fd18a"),
            "warn": ("#4a3412", "#ffbe55"),
            "error": ("#4f1d24", "#ff6b81"),
            "idle": ("#1d2738", "#8ea2bd"),
        }
        bg, fg = palette.get(state, palette["idle"])
        lbl.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border: 1px solid {fg};"
        )

    def _update_recent_events_panel(self) -> None:
        if hasattr(self, "_txt_recent_events"):
            self._txt_recent_events.setPlainText("\n".join(self._recent_events))

    # ═══════════════════════ UI: Status Tab ═══════════════════════════

    def _build_status_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()
        self._lbl_status_hero = QLabel("Status board initializing")
        self._lbl_status_hero.setProperty("role", "hero")
        layout.addWidget(self._lbl_status_hero)

        self._lbl_status_summary = QLabel("Waiting for backend telemetry...")
        self._lbl_status_summary.setProperty("role", "subtle")
        layout.addWidget(self._lbl_status_summary)

        health_row = QHBoxLayout()
        self._pill_backend = self._make_pill("Backend")
        self._pill_heartbeat = self._make_pill("Heartbeat")
        self._pill_rgb = self._make_pill("RGB")
        self._pill_depth = self._make_pill("Depth")
        self._pill_aligned = self._make_pill("Aligned")
        self._pill_logs = self._make_pill("Logs")
        for pill in (
            self._pill_backend,
            self._pill_heartbeat,
            self._pill_rgb,
            self._pill_depth,
            self._pill_aligned,
            self._pill_logs,
        ):
            health_row.addWidget(pill)
        health_row.addStretch()
        layout.addLayout(health_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)

        backend_card, backend_layout = self._make_card("Backend")
        self._lbl_status = QLabel("State: -")
        self._lbl_status_error = QLabel("Last issue: -")
        self._lbl_status_error.setProperty("role", "subtle")
        self._lbl_headset = QLabel("Headset: -")
        self._lbl_last_seen = QLabel("Last seen: -")
        self._lbl_track_models = QLabel("Models: -")
        for lbl in (
            self._lbl_status,
            self._lbl_status_error,
            self._lbl_headset,
            self._lbl_last_seen,
            self._lbl_track_models,
        ):
            backend_layout.addWidget(lbl)

        unity_card, unity_layout = self._make_card("Unity Device")
        self._lbl_device = QLabel("Device: -")
        self._lbl_version = QLabel("App: -")
        self._lbl_unity_version = QLabel("Unity: -")
        self._lbl_network = QLabel("Connection: -")
        self._lbl_tick = QLabel("Tick: 0")
        for lbl in (
            self._lbl_device,
            self._lbl_version,
            self._lbl_unity_version,
            self._lbl_network,
            self._lbl_tick,
        ):
            unity_layout.addWidget(lbl)

        rgb_card, rgb_layout = self._make_card("RGB Capture")
        self._lbl_rgb = QLabel("RGB: -")
        self._lbl_rgb_stream = QLabel("Stream: -")
        self._lbl_intrinsics = QLabel("Intrinsics: -")
        self._lbl_rgb_pose = QLabel("Pose/Timestamp: -")
        self._lbl_rgb_risk = QLabel("Risk: -")
        self._lbl_rgb_risk.setProperty("role", "subtle")
        for lbl in (
            self._lbl_rgb,
            self._lbl_rgb_stream,
            self._lbl_intrinsics,
            self._lbl_rgb_pose,
            self._lbl_rgb_risk,
        ):
            rgb_layout.addWidget(lbl)

        depth_card, depth_layout = self._make_card("Depth Capture")
        self._lbl_depth = QLabel("Depth: -")
        self._lbl_depth_stream = QLabel("Source: -")
        self._lbl_depth_state = QLabel("Availability: -")
        self._lbl_depth_meta = QLabel("Meta: -")
        self._lbl_vram = QLabel("Aux: -")
        for lbl in (
            self._lbl_depth,
            self._lbl_depth_stream,
            self._lbl_depth_state,
            self._lbl_depth_meta,
            self._lbl_vram,
        ):
            depth_layout.addWidget(lbl)

        trigger_card, trigger_layout = self._make_card("Trigger / Alignment")
        self._lbl_track_state = QLabel("Tracking: idle")
        self._lbl_track_label = QLabel("Label: -")
        self._lbl_trigger_status = QLabel("Trigger: no trigger yet")
        self._lbl_alignment_status = QLabel("Aligned depth: unavailable")
        self._lbl_alignment_hint = QLabel("Validation: topdown uses trigger bundle intrinsics when available")
        self._lbl_alignment_hint.setProperty("role", "subtle")
        for lbl in (
            self._lbl_track_state,
            self._lbl_track_label,
            self._lbl_trigger_status,
            self._lbl_alignment_status,
            self._lbl_alignment_hint,
        ):
            trigger_layout.addWidget(lbl)

        events_card, events_layout = self._make_card("Recent Events")
        self._txt_recent_events = QTextEdit()
        self._txt_recent_events.setReadOnly(True)
        self._txt_recent_events.setMaximumHeight(180)
        self._txt_recent_events.setPlaceholderText("Recent pipeline events will appear here...")
        events_layout.addWidget(self._txt_recent_events)

        grid.addWidget(backend_card, 0, 0)
        grid.addWidget(unity_card, 0, 1)
        grid.addWidget(rgb_card, 1, 0)
        grid.addWidget(depth_card, 1, 1)
        grid.addWidget(trigger_card, 2, 0)
        grid.addWidget(events_card, 2, 1)
        layout.addLayout(grid)

        w.setLayout(layout)
        return w

    # ═══════════════════════ UI: Preview Tab ═════════════════════════

    def _build_preview_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()

        self._lbl_preview_hover = None

        # Unified RGB-D Overlay
        overlay_group = QGroupBox("RGB-D Aligned Overlay")
        overlay_layout = QVBoxLayout()
        self._lbl_rgbd_overlay = QLabel("Waiting for RGB-D overlay...")
        self._lbl_rgbd_overlay.setMinimumSize(640, 360)
        self._lbl_rgbd_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_rgbd_overlay.setMouseTracking(True)
        self._lbl_rgbd_overlay.installEventFilter(self)
        self._lbl_rgbd_overlay.setStyleSheet(
            "background-color: #0a1322; border-radius: 8px;"
        )
        overlay_layout.addWidget(self._lbl_rgbd_overlay)
        self._hover_tooltip = QLabel(self._lbl_rgbd_overlay)
        self._hover_tooltip.hide()
        self._hover_tooltip.setStyleSheet(
            """
            QLabel {
                background-color: #cc222222;
                color: #e0e0e0;
                font-size: 12px;
                padding: 4px 8px;
                border: 1px solid #444;
                border-radius: 4px;
            }
            """
        )
        self._hover_tooltip.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        overlay_group.setLayout(overlay_layout)
        layout.addWidget(overlay_group, stretch=1)

        # Metadata
        meta_group = QGroupBox("Stream Metadata")
        meta_layout = QVBoxLayout()
        self._lbl_preview_meta = QLabel("Waiting for runtime metadata...")
        self._lbl_preview_meta.setWordWrap(True)
        self._lbl_preview_meta.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        meta_layout.addWidget(self._lbl_preview_meta)
        meta_group.setLayout(meta_layout)
        layout.addWidget(meta_group)

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

    def _refresh_status_v2(self) -> None:
        try:
            data = fetch_json("/api/status")
            self._latest_status_snapshot = data
            p = data.get("last_payload", {})
            model_status = fetch_json("/api/models/status")
            self._model_status = model_status
            ci = data.get("camera_intrinsics", {})
            depth_meta = data.get("depth_source_meta", {})
            aligned_meta = data.get("aligned_depth_meta", {})
            connected = bool(data.get("connected"))
            rgb_size = data.get("last_rgb_size", "-")
            depth_size = data.get("last_depth_size", "-")
            aligned_ok = bool(model_status.get("aligned_depth"))
            trigger_bundle = data.get("trigger_bundle") or {}

            self._lbl_status_hero.setText(
                "Backend online" if connected else "Backend online, device waiting"
            )
            self._lbl_status_summary.setText(
                f"RGB {rgb_size} | Depth {depth_size} | Active connections {data.get('active_connections', 0)}"
            )
            self._lbl_status.setText("State: running")
            self._lbl_status_error.setText("Last issue: none visible from status endpoint")
            self._lbl_headset.setText(
                f"Headset: {'connected' if connected else 'disconnected'} (active={data.get('active_connections', 0)})"
            )
            self._lbl_last_seen.setText(f"Last seen: {data.get('last_seen_utc', '-')}")
            self._lbl_track_models.setText(
                "Models: "
                + " | ".join(
                    [
                        f"SAM2={'ready' if model_status.get('sam2') else 'off'}",
                        f"Florence2={'ready' if model_status.get('florence2') else 'off'}",
                        f"SigLIP2={'ready' if model_status.get('clip') else 'off'}",
                    ]
                )
            )

            self._lbl_device.setText(f"Device: {p.get('device_model', '-')}")
            self._lbl_version.setText(f"App: {p.get('app_version', '-')}")
            self._lbl_unity_version.setText(f"Unity: {p.get('unity_version', '-')}")
            self._lbl_network.setText(f"Connection: {p.get('connection_mode', '-')}")
            self._lbl_tick.setText(f"Tick: {data.get('last_tick', 0)}")

            self._lbl_rgb.setText(f"RGB: frame={data.get('last_rgb_frame_id', 0)} size={rgb_size}")
            self._lbl_rgb_stream.setText(
                f"Stream: requested={_fmt_wh(ci.get('requested_width'), ci.get('requested_height'))} | "
                f"current={_fmt_wh(ci.get('current_width'), ci.get('current_height'))} | "
                f"stream={_fmt_wh(ci.get('stream_width'), ci.get('stream_height'))}"
            )
            if ci.get("fx"):
                self._lbl_intrinsics.setText(
                    f"Intrinsics: fx={ci['fx']:.1f} fy={ci['fy']:.1f} cx={ci['cx']:.1f} cy={ci['cy']:.1f} | sensor={_fmt_wh(ci.get('sensor_width'), ci.get('sensor_height'))}"
                )
            else:
                self._lbl_intrinsics.setText("Intrinsics: waiting for camera metadata")
            self._lbl_rgb_pose.setText(
                f"Pose/Timestamp: latest_rgb_ts={data.get('latest_rgb_timestamp_ms')} | camera_meta_ts={ci.get('timestamp_ms', '-')}"
            )
            self._lbl_rgb_risk.setText(
                f"Risk: preferred={_fmt_wh(ci.get('preferred_width'), ci.get('preferred_height'))} | supported={len(ci.get('supported_resolutions') or [])} entries"
            )

            self._lbl_depth.setText(f"Depth: frame={data.get('last_depth_frame_id', 0)} size={depth_size}")
            overlay_state = "idle"
            if hasattr(self, "_rgbd_overlay_socket") and self._rgbd_overlay_socket is not None:
                overlay_state = "active" if self._rgbd_overlay_socket.state() == QAbstractSocket.SocketState.ConnectedState else "idle"
            self._lbl_depth_stream.setText(
                f"Source: trigger-only (overlay WS: {overlay_state})"
            )
            self._lbl_depth_state.setText(
                f"Availability: {'aligned uploaded' if aligned_ok else 'no aligned depth yet'}"
            )
            self._lbl_depth_meta.setText(
                f"Meta: raw={_fmt_wh(depth_meta.get('source_width'), depth_meta.get('source_height'))} | "
                f"sampled={_fmt_wh(depth_meta.get('sampled_width'), depth_meta.get('sampled_height'))} | "
                f"stride={depth_meta.get('stride', '-')}"
            )
            self._lbl_vram.setText(
                f"Aux: flip_vertical={depth_meta.get('flip_vertical', '-')} | preprocessed={depth_meta.get('preprocessed', '-')} | "
                f"LUT={'loaded' if self._lut_loaded else 'missing'} | raw depth cache={'ready' if self._latest_depth_values else 'empty'}"
            )

            tracking = data.get("tracking", {})
            self._lbl_track_state.setText(f"Tracking: {tracking.get('state', 'idle')}")
            self._lbl_track_label.setText(f"Label: {tracking.get('label', '-') or '-'}")
            self._lbl_trigger_status.setText(
                f"Trigger: ts={trigger_bundle.get('trigger_timestamp_ms')} px={trigger_bundle.get('pixel_xy')}"
                if trigger_bundle
                else "Trigger: no trigger frame yet"
            )
            self._lbl_alignment_status.setText(
                f"Aligned depth: sparse overlay available | depth={trigger_bundle.get('depth_sampled_wh')} | valid={aligned_meta.get('valid_points', '-')}"
                if aligned_ok or self._aligned_depth_pixmap is not None
                else "Aligned depth: unavailable"
            )
            self._lbl_alignment_hint.setText(
                "Validation: topdown and pixel query now use strict trigger intrinsics only"
            )

            self._set_pill_state(self._pill_backend, "ok")
            self._set_pill_state(self._pill_heartbeat, "ok" if connected else "warn")
            self._set_pill_state(self._pill_rgb, "ok" if data.get("last_rgb_frame_id", 0) > 0 else "warn")
            self._set_pill_state(self._pill_depth, "ok" if data.get("last_depth_frame_id", 0) > 0 else "warn")
            self._set_pill_state(
                self._pill_aligned,
                "ok" if aligned_ok else ("warn" if self._latest_rgb_width > 0 else "idle"),
            )
            self._set_pill_state(self._pill_logs, "ok" if self._last_log_id > 0 else "warn")
        except urllib.error.URLError:
            self._latest_status_snapshot = {}
            self._lbl_status_hero.setText("Backend unreachable")
            self._lbl_status_summary.setText("Status API request failed")
            self._lbl_status.setText("State: disconnected")
            self._lbl_status_error.setText("Last issue: backend request failed")
            for pill in (
                self._pill_backend,
                self._pill_heartbeat,
                self._pill_rgb,
                self._pill_depth,
                self._pill_aligned,
            ):
                self._set_pill_state(pill, "error")
        except Exception as ex:
            self._latest_status_snapshot = {}
            self._lbl_status_hero.setText("Status board degraded")
            self._lbl_status_summary.setText(str(ex))
            self._lbl_status.setText("State: error")
            self._lbl_status_error.setText(f"Last issue: {ex}")
            self._set_pill_state(self._pill_backend, "error")

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

    def _compute_draw_rect(
        self, label: QLabel, pixmap: QPixmap
    ) -> tuple[int, int, int, int]:
        """Compute the pixel rectangle of a scaled pixmap inside a QLabel."""
        contents = label.contentsRect()
        scaled = pixmap.scaled(
            contents.width(),
            contents.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        rx = contents.x() + max(0, (contents.width() - scaled.width()) // 2)
        ry = contents.y() + max(0, (contents.height() - scaled.height()) // 2)
        return rx, ry, scaled.width(), scaled.height()

    def _refresh_previews(self) -> None:
        self._fetch_aligned_depth()
        self._refresh_rgbd_overlay_panel()
        self._refresh_preview_metadata()

    def _refresh_rgbd_overlay_panel(self) -> None:
        """Paint the streaming RGB-D overlay, falls back to aligned heatmap."""
        pix = self._rgbd_overlay_pixmap
        if pix is not None and not pix.isNull():
            scaled = pix.scaled(
                self._lbl_rgbd_overlay.width(),
                self._lbl_rgbd_overlay.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._lbl_rgbd_overlay.setPixmap(scaled)
            self._lbl_rgbd_overlay.setText("")
            self._preview_draw_rect = self._compute_draw_rect(
                self._lbl_rgbd_overlay, pix
            )
        elif self._aligned_depth_pixmap is not None:
            # Fallback: show old aligned heatmap from tracking pipeline
            scaled = self._aligned_depth_pixmap.scaled(
                self._lbl_rgbd_overlay.width(),
                self._lbl_rgbd_overlay.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._lbl_rgbd_overlay.setPixmap(scaled)
            self._lbl_rgbd_overlay.setText("")

    def _refresh_preview_metadata(self) -> None:
        data = self._latest_status_snapshot or {}
        ci = data.get("camera_intrinsics", {})
        depth_meta = data.get("depth_source_meta", {})
        aligned_meta = data.get("aligned_depth_meta", {})
        supported = ci.get("supported_resolutions") or []
        supported_text = ", ".join(supported[:8]) if supported else "-"
        if len(supported) > 8:
            supported_text += f" ... (+{len(supported) - 8})"

        lines = [
            f"RGB supported: {supported_text}",
            f"RGB preferred/requested/current: {_fmt_wh(ci.get('preferred_width'), ci.get('preferred_height'))} / "
            f"{_fmt_wh(ci.get('requested_width'), ci.get('requested_height'))} / "
            f"{_fmt_wh(ci.get('current_width'), ci.get('current_height'))}",
            f"RGB stream/sensor/raw preview: {_fmt_wh(ci.get('stream_width'), ci.get('stream_height'))} / "
            f"{_fmt_wh(ci.get('sensor_width'), ci.get('sensor_height'))} / "
            f"{_fmt_wh(self._latest_rgb_width, self._latest_rgb_height)}",
            f"Depth raw texture/sample/raw preview: {_fmt_wh(depth_meta.get('source_width'), depth_meta.get('source_height'))} / "
            f"{_fmt_wh(depth_meta.get('sampled_width'), depth_meta.get('sampled_height'))} / "
            f"{_fmt_wh(self._latest_depth_width, self._latest_depth_height)}",
            f"Depth stride/preprocessed/flipVertical: {depth_meta.get('stride', '-')} / "
            f"{depth_meta.get('preprocessed', '-')} / {depth_meta.get('flip_vertical', '-')}",
            f"Sparse aligned valid/clipped/behind: {aligned_meta.get('valid_points', '-')} / "
            f"{aligned_meta.get('clipped_points', '-')} / {aligned_meta.get('points_behind_camera', '-')}",
            f"Sparse aligned rgbZ[min,max,avg]: {aligned_meta.get('min_rgb_camera_z', '-')} / "
            f"{aligned_meta.get('max_rgb_camera_z', '-')} / {aligned_meta.get('avg_rgb_camera_z', '-')}",
        ]
        self._lbl_preview_meta.setText("\n".join(lines))

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
        if self._rgbd_overlay_socket.state() not in (
            QAbstractSocket.SocketState.ConnectedState,
            QAbstractSocket.SocketState.ConnectingState,
        ):
            self._rgbd_overlay_socket.open(
                QUrl("ws://127.0.0.1:8500/ws/rgbd-overlay")
            )

    def _on_rgb_binary(self, payload) -> None:
        self._latest_rgb_bytes = bytes(payload)
        pixmap = QPixmap()
        if pixmap.loadFromData(self._latest_rgb_bytes):
            self._raw_rgb_pixmap = pixmap
            self._latest_rgb_width = pixmap.width()
            self._latest_rgb_height = pixmap.height()

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

    def _on_rgbd_overlay_binary(self, payload: bytes) -> None:
        """Receive aligned RGB-D overlay JPEG from streaming backend."""
        self._rgbd_overlay_bytes = payload
        pix = QPixmap()
        if pix.loadFromData(payload):
            self._rgbd_overlay_pixmap = pix
            self._latest_rgb_width = pix.width()
            self._latest_rgb_height = pix.height()

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
        if watched is self._lbl_rgbd_overlay:
            etype = event.type()
            if etype == QEvent.Type.MouseMove:
                pos = event.position().toPoint()
                self._update_preview_hover(pos.x(), pos.y())
                if self._hover_tooltip is not None and not self._hover_tooltip.isHidden():
                    tip_x = pos.x() + 16
                    tip_y = pos.y() + 16
                    overlay_w = self._lbl_rgbd_overlay.width()
                    overlay_h = self._lbl_rgbd_overlay.height()
                    tip_w = self._hover_tooltip.width()
                    tip_h = self._hover_tooltip.height()
                    if tip_x + tip_w > overlay_w:
                        tip_x = pos.x() - tip_w - 8
                    if tip_y + tip_h > overlay_h:
                        tip_y = pos.y() - tip_h - 8
                    self._hover_tooltip.move(max(0, tip_x), max(0, tip_y))
            elif etype == QEvent.Type.MouseButtonPress:
                pos = event.position().toPoint()
                self._on_rgb_click_v2(pos.x(), pos.y())
            elif etype == QEvent.Type.Leave:
                if self._hover_tooltip is not None:
                    self._hover_tooltip.hide()
        return super().eventFilter(watched, event)

    def _map_preview_pos_to_source_pixel(self, mx: int, my: int) -> tuple[int, int] | None:
        if not hasattr(self, "_preview_draw_rect") or self._preview_draw_rect is None:
            return None
        if self._latest_rgb_width <= 0 or self._latest_rgb_height <= 0:
            return None

        dx, dy, dw, dh = self._preview_draw_rect
        if dw <= 0 or dh <= 0:
            return None
        if mx < dx or my < dy or mx > dx + dw - 1 or my > dy + dh - 1:
            return None

        u = (mx - dx) / max(dw - 1, 1)
        v = (my - dy) / max(dh - 1, 1)
        px = int(round(u * max(self._latest_rgb_width - 1, 0)))
        py = int(round(v * max(self._latest_rgb_height - 1, 0)))
        px = max(0, min(self._latest_rgb_width - 1, px))
        py = max(0, min(self._latest_rgb_height - 1, py))
        return px, py

    def _update_preview_hover(self, mx: int, my: int) -> None:
        mapped = self._map_preview_pos_to_source_pixel(mx, my)
        if mapped is None:
            if self._hover_tooltip is not None:
                self._hover_tooltip.hide()
            return
        px, py = mapped

        text = f"({px},{py})  no depth"
        try:
            req = urllib.request.Request(
                f"{API_BASE}/api/depth/at?px={px}&py={py}", method="GET",
            )
            with urllib.request.urlopen(req, timeout=0.3) as res:
                d = json.loads(res.read().decode("utf-8"))
            dm = d.get("depth_m")
            if dm is not None and d.get("valid"):
                src = d.get("source", "?")
                x = d.get("rgb_cam_x", 0)
                y = d.get("rgb_cam_y", 0)
                z = d.get("rgb_cam_z", 0)
                text = f"XYZ = ({x:.3f}, {y:.3f}, {z:.3f}) m"
                if src == "nearest":
                    dist = d.get("distance_px", 0)
                    text += f"  (~{dist:.0f}px)"
            else:
                text = f"({px},{py})  no depth"
        except Exception:
            text = f"({px},{py})  (query failed)"

        if self._hover_tooltip is not None:
            self._hover_tooltip.setText(text)
            self._hover_tooltip.adjustSize()
            self._hover_tooltip.raise_()
            self._hover_tooltip.show()

    def _fetch_aligned_depth(self) -> None:
        """Fetch the aligned-depth heatmap from the backend (one HTTP request per refresh).

        The heatmap is produced by /api/depth/aligned-heatmap, which returns
        depth data reprojected into RGB coordinates — geometrically aligned.
        Falls back silently if unavailable (204 = no trigger with intrinsics yet).
        """
        try:
            req = urllib.request.Request(
                f"{API_BASE}/api/depth/aligned-heatmap", method="GET",
            )
            with urllib.request.urlopen(req, timeout=0.3) as res:
                if res.status == 204:
                    return
                data = res.read()
            pix = QPixmap()
            if pix.loadFromData(data):
                self._aligned_depth_pixmap = pix
        except Exception:
            pass  # silently skip if endpoint unavailable or no depth yet

    def _on_rgb_click_v2(self, mx: int, my: int) -> None:
        mapped = self._map_preview_pos_to_source_pixel(mx, my)
        if mapped is None:
            return
        px, py = mapped
        self._do_detect(px, py)

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

        short_msg = msg if len(msg) <= 120 else msg[:117] + "..."
        self._recent_events.appendleft(f"[{level}] [{source}] {short_msg}")
        self._update_recent_events_panel()
        if hasattr(self, "_pill_logs"):
            self._set_pill_state(self._pill_logs, "ok" if level != "ERROR" else "warn")

    # ═══════════════════════ Cleanup ══════════════════════════════════

    def closeEvent(self, event) -> None:
        for attr in ("_rgb_socket", "_depth_socket", "_rgbd_overlay_socket"):
            sock = getattr(self, attr, None)
            if sock is not None:
                try:
                    sock.abort()
                except Exception:
                    pass
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
