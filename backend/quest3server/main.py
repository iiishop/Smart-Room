from __future__ import annotations

import asyncio
import base64
import json
import struct
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from .log_manager import add_log, add_python_log, list_logs
from .tracking import TrackingEngine, TrackState
from .tracking.depth_alignment import align_depth_to_rgb, intrinsics_from_focal_principal


app = FastAPI(title="Smart Room Receiver Backend")

_lock = Lock()
_state = {
    "connected": False,
    "active_connections": 0,
    "last_seen_utc": None,
    "last_payload": {},
    "last_tick": 0,
    "last_rgb_frame_id": 0,
    "last_rgb_size": None,
    "last_depth_frame_id": 0,
    "last_depth_size": None,
}
_latest_rgb_jpeg: bytes | None = None
_latest_rgb_bgr: Any = None  # decoded BGR numpy array for tracking engine
_latest_depth_packet: bytes | None = None
_latest_aligned_depth: np.ndarray | None = None  # depth reprojected into RGB frame
_last_trigger_frame_jpeg: bytes | None = None  # original RGB frame from last trigger
_last_trigger_pixel: tuple[int, int] | None = None  # (px, py) from last trigger
_last_rgb_intrinsics: np.ndarray | None = None  # 3×3 K from last trigger
_latest_rgb_packet: bytes | None = None
_rgb_preview_clients: set[WebSocket] = set()
_rgb_raw_preview_clients: set[WebSocket] = set()
_depth_preview_clients: set[WebSocket] = set()
_heartbeat_clients: set[WebSocket] = set()
_next_raycast_query_id = 1
_latest_raycast_result: dict = {}
_camera_intrinsics: dict = {}

_tracking_engine: TrackingEngine | None = None
_tracking_clients: set[WebSocket] = set()
_models_ready = False
_last_detection_crop_jpeg: bytes | None = None  # cropped bbox region from last detection


@app.on_event("startup")
async def _warm_models() -> None:
    """Pre-load SAM2 + Florence-2 at startup so first /api/track/start doesn't timeout."""
    global _tracking_engine, _models_ready
    try:
        _tracking_engine = TrackingEngine()
        _tracking_engine._ensure_models()  # force model download + load
        _models_ready = True
        add_python_log("info", "Tracking engine models loaded (SAM2 + Florence-2)")
    except Exception as exc:
        add_python_log("error", f"Tracking engine model warm-up failed: {exc}")
        _models_ready = False


def _get_tracking_engine() -> TrackingEngine:
    global _tracking_engine
    if _tracking_engine is None:
        _tracking_engine = TrackingEngine()
    return _tracking_engine


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/status")
async def status() -> dict:
    with _lock:
        snapshot = dict(_state)
        snapshot["camera_intrinsics"] = dict(_camera_intrinsics)
    snapshot["tracking"] = {
        "active": _tracking_engine is not None and _tracking_engine.state != TrackState.IDLE,
        "state": _tracking_engine.state.value if _tracking_engine else "idle",
        "label": _tracking_engine.label if _tracking_engine else "",
    }
    return snapshot


@app.get("/api/latest-rgb")
async def latest_rgb():
    from fastapi import Response

    with _lock:
        if _latest_rgb_jpeg is None:
            return Response(status_code=204)
        return Response(content=_latest_rgb_jpeg, media_type="image/jpeg")


@app.get("/api/logs")
async def logs(since_id: int = Query(0), limit: int = Query(200)) -> dict:
    items = list_logs(since_id=since_id, limit=limit)
    return {"count": len(items), "logs": items}


@app.get("/api/raycast-result")
async def raycast_result() -> dict:
    with _lock:
        return dict(_latest_raycast_result)


@app.post("/api/raycast-query")
async def raycast_query(body: dict) -> dict:
    global _next_raycast_query_id
    u = float(body.get("u", -1.0))
    v = float(body.get("v", -1.0))

    if u < 0.0 or u > 1.0 or v < 0.0 or v > 1.0:
        return {"ok": False, "error": "u/v out of range [0,1]"}

    with _lock:
        query_id = _next_raycast_query_id
        _next_raycast_query_id += 1

    payload = {
        "type": "raycast_query",
        "query_id": query_id,
        "timestamp_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
        "u": u,
        "v": v,
    }

    sent = await _broadcast_heartbeat_control(payload)
    return {"ok": True, "query_id": query_id, "dispatched": sent}


# ═══════════════════════ WebSocket: RGB stream ═══════════════════════

@app.websocket("/ws/rgb")
async def rgb_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    add_python_log("info", "RGB websocket connected on /ws/rgb")

    try:
        while True:
            message = await websocket.receive()
            data = message.get("bytes")
            if not data:
                continue

            parsed = _parse_rgb_packet(data)
            if parsed is None:
                add_python_log(
                    "warning", f"Invalid rgb packet received (size={len(data)})"
                )
                continue

            frame_id, timestamp_ms, width, height, jpeg = parsed
            await _ingest_rgb_frame(
                frame_id=frame_id,
                timestamp_ms=timestamp_ms,
                width=width,
                height=height,
                jpeg=jpeg,
                raw_packet=data,
            )
    except WebSocketDisconnect:
        add_python_log("warning", "RGB websocket disconnected")
    except Exception as ex:
        add_python_log("error", f"RGB websocket processing failed: {ex}")


# ═══════════════════ WebSocket: Dashboard previews ══════════════════

@app.websocket("/ws/rgb-preview")
async def rgb_preview_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    _rgb_preview_clients.add(websocket)
    add_python_log("info", "Dashboard preview client connected on /ws/rgb-preview")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _rgb_preview_clients.discard(websocket)


@app.websocket("/ws/rgb-preview-raw")
async def rgb_preview_raw_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    _rgb_raw_preview_clients.add(websocket)
    add_python_log("info", "Raw preview client connected on /ws/rgb-preview-raw")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _rgb_raw_preview_clients.discard(websocket)


# ═══════════════════ WebSocket: Depth stream ════════════════════════

@app.websocket("/ws/depth")
async def depth_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    add_python_log("info", "Depth websocket connected on /ws/depth")

    try:
        while True:
            message = await websocket.receive()
            data = message.get("bytes")
            if not data:
                continue

            parsed = _parse_depth_packet(data)
            if parsed is None:
                add_python_log(
                    "warning", f"Invalid depth packet received (size={len(data)})"
                )
                continue

            frame_id, timestamp_ms, width, height, row_stride, pixel_stride, payload = (
                parsed
            )

            with _lock:
                global _latest_depth_packet
                _latest_depth_packet = data
                _state["last_depth_frame_id"] = frame_id
                _state["last_depth_size"] = f"{width}x{height}"
                _state["last_seen_utc"] = _utc_now_iso()

            await _broadcast_depth_preview(data)
    except WebSocketDisconnect:
        add_python_log("warning", "Depth websocket disconnected")
    except Exception as ex:
        add_python_log("error", f"Depth websocket processing failed: {ex}")


@app.websocket("/ws/depth-preview")
async def depth_preview_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    _depth_preview_clients.add(websocket)
    add_python_log("info", "Dashboard preview client connected on /ws/depth-preview")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _depth_preview_clients.discard(websocket)


# ═══════════════════ WebSocket: Heartbeat / control ═════════════════

@app.websocket("/ws/heartbeat")
async def heartbeat_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    add_python_log("info", "WebSocket client connected on /ws/heartbeat")
    _heartbeat_clients.add(websocket)

    with _lock:
        _state["active_connections"] += 1
        _state["connected"] = _state["active_connections"] > 0

    try:
        while True:
            message = await websocket.receive_text()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                payload = {"raw": message, "parse_error": True}
                add_python_log("warning", "Received non-JSON websocket message")

            with _lock:
                payload_type = payload.get("type")
                _state["last_payload"] = {
                    "type": payload_type,
                    "device_id": payload.get("device_id"),
                    "tick": payload.get("tick"),
                    "frame_id": payload.get("frame_id"),
                    "app_version": payload.get("app_version"),
                    "device_model": payload.get("device_model"),
                    "connection_mode": payload.get("connection_mode"),
                    "timestamp_ms": payload.get("timestamp_ms"),
                }
                _state["last_seen_utc"] = _utc_now_iso()
                if "tick" in payload and payload.get("tick") is not None:
                    _state["last_tick"] = int(payload["tick"])

                if payload_type == "rgb_frame":
                    payload_b64 = payload.get("payload_b64")
                    if payload_b64:
                        jpeg = base64.b64decode(payload_b64)
                        frame_id = int(payload.get("frame_id", _state["last_rgb_frame_id"]))
                        timestamp_ms = int(
                            payload.get(
                                "timestamp_ms",
                                int(datetime.now(timezone.utc).timestamp() * 1000),
                            )
                        )
                        width = int(payload.get("width", 0))
                        height = int(payload.get("height", 0))
                    else:
                        jpeg = None

                elif payload_type == "camera_intrinsics":
                    global _camera_intrinsics
                    _camera_intrinsics = {
                        "fx": payload.get("fx"),
                        "fy": payload.get("fy"),
                        "cx": payload.get("cx"),
                        "cy": payload.get("cy"),
                        "projection_matrix": payload.get("projection_matrix"),
                    }

                elif payload_type == "client_log":
                    add_log(
                        level=payload.get("level", "INFO"),
                        source=payload.get("source", "unity"),
                        message=payload.get("message", "(empty)"),
                        script=payload.get("script"),
                        line=payload.get("line"),
                        stack_trace=payload.get("stack_trace"),
                    )

                elif payload_type == "raycast_result":
                    global _latest_raycast_result
                    _latest_raycast_result = {
                        "query_id": payload.get("query_id"),
                        "timestamp_ms": payload.get("timestamp_ms"),
                        "u": payload.get("u"),
                        "v": payload.get("v"),
                        "depth_m": payload.get("depth_m"),
                        "world_xyz": payload.get("world_xyz"),
                        "camera_xyz": payload.get("camera_xyz"),
                        "hit_surface_label": payload.get("hit_surface_label"),
                        "hit": payload.get("hit", False),
                    }

            if payload_type == "rgb_frame" and jpeg is not None:
                await _ingest_rgb_frame(
                    frame_id=frame_id,
                    timestamp_ms=timestamp_ms,
                    width=width,
                    height=height,
                    jpeg=jpeg,
                    raw_packet=None,
                )

            await websocket.send_text("ack")
    except WebSocketDisconnect:
        add_python_log("warning", "WebSocket client disconnected")
    except Exception as ex:
        add_python_log("error", f"WebSocket processing failed: {ex}")
    finally:
        _heartbeat_clients.discard(websocket)
        with _lock:
            _state["active_connections"] = max(0, _state["active_connections"] - 1)
            _state["connected"] = _state["active_connections"] > 0


# ═══════════════════════ Packet parsers ═════════════════════════════

def _parse_rgb_packet(data: bytes):
    if len(data) < 28:
        return None
    try:
        magic, frame_id, timestamp_ms, width, height, payload_len = struct.unpack_from(
            "<4sI q I I I", data, 0
        )
    except struct.error:
        return None
    if magic != b"RGB1":
        return None
    if payload_len <= 0:
        return None
    if len(data) < 28 + payload_len:
        return None
    payload = data[28 : 28 + payload_len]
    return frame_id, timestamp_ms, width, height, payload


def _parse_depth_packet(data: bytes):
    if len(data) < 36:
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
        ) = struct.unpack_from("<4sI q I I I I I", data, 0)
    except struct.error:
        return None
    if magic != b"DEP1":
        return None
    if payload_len <= 0:
        return None
    if len(data) < 36 + payload_len:
        return None
    payload = data[36 : 36 + payload_len]
    return frame_id, timestamp_ms, width, height, row_stride, pixel_stride, payload


def _depth_packet_to_array(packet: bytes) -> np.ndarray | None:
    """Parse a binary DEP1 packet into a float32 depth array (metres)."""
    parsed = _parse_depth_packet(packet)
    if parsed is None:
        return None
    _, _, width, height, _, _, payload = parsed
    expected = width * height * 4
    if len(payload) != expected:
        return None
    arr = np.frombuffer(payload, dtype=np.float32).reshape((height, width)).copy()
    return arr


# ═══════════════════════ Broadcasting helpers ════════════════════════

async def _broadcast_rgb_preview(jpeg_bytes: bytes) -> None:
    if not _rgb_preview_clients:
        return
    stale: list[WebSocket] = []
    for client in list(_rgb_preview_clients):
        try:
            await asyncio.wait_for(client.send_bytes(jpeg_bytes), timeout=1.0)
        except Exception:
            stale.append(client)
    for client in stale:
        _rgb_preview_clients.discard(client)


async def _broadcast_rgb_raw_preview(packet_bytes: bytes) -> None:
    if not _rgb_raw_preview_clients:
        return
    stale: list[WebSocket] = []
    for client in list(_rgb_raw_preview_clients):
        try:
            await asyncio.wait_for(client.send_bytes(packet_bytes), timeout=1.0)
        except Exception:
            stale.append(client)
    for client in stale:
        _rgb_raw_preview_clients.discard(client)


async def _broadcast_depth_preview(packet_bytes: bytes) -> None:
    if not _depth_preview_clients:
        return
    stale: list[WebSocket] = []
    for client in list(_depth_preview_clients):
        try:
            await asyncio.wait_for(client.send_bytes(packet_bytes), timeout=1.0)
        except Exception:
            stale.append(client)
    for client in stale:
        _depth_preview_clients.discard(client)


async def _broadcast_heartbeat_control(payload: dict) -> int:
    if not _heartbeat_clients:
        return 0
    data = json.dumps(payload)
    stale: list[WebSocket] = []
    sent = 0
    for client in list(_heartbeat_clients):
        try:
            await asyncio.wait_for(client.send_text(data), timeout=1.0)
            sent += 1
        except Exception:
            stale.append(client)
    for client in stale:
        _heartbeat_clients.discard(client)
    return sent


# ═══════════════════════ RGB frame ingestion ═════════════════════════

async def _ingest_rgb_frame(
    *,
    frame_id: int,
    timestamp_ms: int,
    width: int,
    height: int,
    jpeg: bytes,
    raw_packet: bytes | None,
) -> None:
    with _lock:
        global _latest_rgb_jpeg
        global _latest_rgb_packet
        global _latest_rgb_bgr
        _latest_rgb_jpeg = jpeg
        if raw_packet is not None:
            _latest_rgb_packet = raw_packet
        _state["last_rgb_frame_id"] = frame_id
        _state["last_rgb_size"] = f"{width}x{height}"
        _state["last_seen_utc"] = _utc_now_iso()

    # Decode BGR for tracking engine (lightweight, ~3ms)
    decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is not None:
        _latest_rgb_bgr = decoded

    await _broadcast_rgb_preview(jpeg)
    if raw_packet is not None:
        await _broadcast_rgb_raw_preview(raw_packet)


# ═══════════════════════ Tracking API ════════════════════════════════

@app.post("/api/track/start")
async def track_start(body: dict) -> dict[str, Any]:
    pixel_x = float(body.get("pixel_x", -1))
    pixel_y = float(body.get("pixel_y", -1))
    if pixel_x < 0 or pixel_y < 0:
        raise HTTPException(status_code=400, detail="pixel_x and pixel_y required")
    if _latest_rgb_bgr is None:
        raise HTTPException(status_code=503, detail="No RGB frame available yet")
    if not _models_ready:
        raise HTTPException(status_code=503, detail="Tracking models not ready yet (still loading)")

    # ── optional: align depth to RGB if intrinsics provided ──────────
    global _latest_aligned_depth
    _latest_aligned_depth = None

    rgb_intrinsics_flat = body.get("rgb_intrinsics")
    depth_reproj_flat = body.get("depth_reproj")
    rgb_pose_flat = body.get("rgb_pose")

    if rgb_intrinsics_flat and depth_reproj_flat and _latest_depth_packet is not None:
        try:
            K = np.array(rgb_intrinsics_flat, dtype=np.float32).reshape(3, 3)
            global _last_rgb_intrinsics
            _last_rgb_intrinsics = K
            reproj = np.array(depth_reproj_flat, dtype=np.float32).reshape(4, 4)
            rgb_pose = np.array(rgb_pose_flat, dtype=np.float32) if rgb_pose_flat and len(rgb_pose_flat) == 7 else None
            depth_raw = _depth_packet_to_array(_latest_depth_packet)
            if depth_raw is not None:
                h_rgb, w_rgb = _latest_rgb_bgr.shape[:2]
                aligned = align_depth_to_rgb(
                    depth_raw, reproj, K, h_rgb, w_rgb, rgb_pose=rgb_pose,
                )
                if aligned is not None:
                    _latest_aligned_depth = aligned
                    add_python_log(
                        "debug",
                        f"Depth aligned: {aligned.shape[1]}×{aligned.shape[0]}, "
                        f"range=[{np.nanmin(aligned):.2f}, {np.nanmax(aligned):.2f}]m",
                    )
        except Exception as exc:
            add_python_log("warning", f"Depth alignment failed: {exc}")

    engine = _get_tracking_engine()
    engine.stop()
    h, w = _latest_rgb_bgr.shape[:2]
    px = int(pixel_x * w) if pixel_x <= 1.0 else int(pixel_x)
    py = int(pixel_y * h) if pixel_y <= 1.0 else int(pixel_y)

    # Save the original frame + click pixel for dashboard preview
    global _last_trigger_frame_jpeg, _last_trigger_pixel
    _last_trigger_pixel = (px, py)
    if _latest_rgb_jpeg is not None:
        _last_trigger_frame_jpeg = _latest_rgb_jpeg

    result = engine.detect(px, py, _latest_rgb_bgr.copy())
    add_python_log("info", f"Tracking detect at ({px},{py}) -> {result.label}")

    # Crop and store the detected bbox region for preview
    global _last_detection_crop_jpeg
    x0, y0, x1, y1 = result.box_xyxy
    x0_c = max(0, int(x0))
    y0_c = max(0, int(y0))
    x1_c = min(w, int(x1))
    y1_c = min(h, int(y1))
    if x1_c > x0_c and y1_c > y0_c:
        crop = _latest_rgb_bgr[y0_c:y1_c, x0_c:x1_c]
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ok:
            _last_detection_crop_jpeg = buf.tobytes()

    return {"ok": True, "result": result.to_payload()}


@app.post("/api/track/stop")
async def track_stop() -> dict[str, Any]:
    if _tracking_engine is not None:
        _tracking_engine.stop()
    return {"ok": True}


@app.get("/api/track/status")
async def track_status() -> dict[str, Any]:
    if _tracking_engine is None:
        return {"active": False, "state": "idle"}
    return {
        "active": _tracking_engine.state != TrackState.IDLE,
        "state": _tracking_engine.state.value,
        "label": _tracking_engine.label,
    }


@app.get("/api/models/status")
async def models_status() -> dict[str, Any]:
    """Report which AI models are loaded and ready."""
    engine = _tracking_engine
    if engine is None:
        return {
            "ready": False,
            "sam2": False,
            "florence2": False,
            "clip": False,
        }
    return {
        "ready": _models_ready,
        "sam2": engine._sam2_image is not None,
        "florence2": engine._florence2 is not None,
        "clip": engine._clip_model is not None,
        "aligned_depth": _latest_aligned_depth is not None,
    }


@app.get("/api/track/last-crop")
async def track_last_crop():
    """Return the JPEG of the cropped bbox region from the last detection."""
    if _last_detection_crop_jpeg is None:
        raise HTTPException(status_code=404, detail="No detection crop available")
    return Response(content=_last_detection_crop_jpeg, media_type="image/jpeg")


@app.get("/api/track/last-original")
async def track_last_original():
    """Return the original RGB frame from the last trigger, with a
    red crosshair drawn at the detection pixel."""
    if _last_trigger_frame_jpeg is None or _last_trigger_pixel is None:
        raise HTTPException(status_code=404, detail="No trigger frame available")

    px, py = _last_trigger_pixel
    # Decode JPEG → draw crosshair → re-encode
    raw = np.frombuffer(_last_trigger_frame_jpeg, dtype=np.uint8)
    bgr = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if bgr is None:
        raise HTTPException(status_code=500, detail="Failed to decode frame")

    h, w = bgr.shape[:2]
    # Crosshair: 20px arms, red, 2px thick
    arm = 20
    color = (0, 0, 255)  # BGR red
    cv2.line(bgr, (max(0, px - arm), py), (min(w - 1, px + arm), py), color, 2)
    cv2.line(bgr, (px, max(0, py - arm)), (px, min(h - 1, py + arm)), color, 2)
    # Small circle at exact point
    cv2.circle(bgr, (px, py), 4, color, 2)

    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to encode frame")
    return Response(content=buf.tobytes(), media_type="image/jpeg")


@app.get("/api/depth/at")
async def depth_at_pixel(px: int = Query(...), py: int = Query(...)):
    """Return the aligned depth (metres) at a given RGB pixel."""
    if _latest_aligned_depth is None:
        return {"depth_m": None, "source": "none"}
    from .tracking.depth_alignment import query_depth_at_pixel

    d = query_depth_at_pixel(_latest_aligned_depth, px, py)
    return {"depth_m": d, "source": "aligned"}


@app.get("/api/depth/topdown")
async def depth_topdown():
    """Return a bird's-eye view JPEG of the depth data, with cursor marked."""
    if _latest_aligned_depth is None or _last_rgb_intrinsics is None:
        raise HTTPException(status_code=404, detail="No depth data or intrinsics")
    from .tracking.depth_alignment import render_topdown

    cx = _last_trigger_pixel[0] if _last_trigger_pixel else None
    cy = _last_trigger_pixel[1] if _last_trigger_pixel else None
    img = render_topdown(_latest_aligned_depth, _last_rgb_intrinsics, cx, cy)
    if img is None:
        raise HTTPException(status_code=500, detail="Failed to render top-down view")
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to encode")
    return Response(content=buf.tobytes(), media_type="image/jpeg")


@app.get("/api/depth/aligned-heatmap")
async def depth_aligned_heatmap():
    """Return the aligned-depth frame as a JPEG heatmap, same resolution as RGB.

    Uses depth values reprojected into the RGB frame via align_depth_to_rgb().
    Returns 204 if no aligned depth is available yet (no trigger with intrinsics).
    """
    if _latest_aligned_depth is None:
        return Response(status_code=204)

    # Depth (metres) → colour heatmap (near=red, far=blue)
    valid = ~np.isnan(_latest_aligned_depth) & (_latest_aligned_depth > 0)
    d_max = float(np.nanmax(_latest_aligned_depth[valid])) if valid.any() else 5.0
    d_max = np.clip(d_max, 0.5, 8.0)

    # Normalise 0..1, invert so near=bright
    d_norm = np.clip(_latest_aligned_depth / max(d_max, 0.01), 0, 1)
    d_norm = np.where(valid, 1.0 - d_norm, 0.0)

    hue = (d_norm * 120).astype(np.uint8)  # 0° red → 120° green (far)
    sat = np.full_like(hue, 200, dtype=np.uint8)
    val = np.where(valid, 220, 0).astype(np.uint8)
    hsv = np.stack([hue, sat, val], axis=2)
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to encode")
    return Response(content=buf.tobytes(), media_type="image/jpeg")


@app.websocket("/ws/tracking")
async def tracking_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    _tracking_clients.add(websocket)
    add_python_log("info", "Tracking WS connected")
    try:
        while True:
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    finally:
        _tracking_clients.discard(websocket)


async def _broadcast_tracking(result_payload: dict) -> None:
    if not _tracking_clients:
        return
    data = json.dumps(result_payload)
    stale: list[WebSocket] = []
    for client in list(_tracking_clients):
        try:
            await asyncio.wait_for(client.send_text(data), timeout=1.0)
        except Exception:
            stale.append(client)
    for client in stale:
        _tracking_clients.discard(client)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("quest3server.main:app", host="0.0.0.0", port=8500, reload=True)
