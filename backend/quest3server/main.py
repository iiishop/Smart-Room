from __future__ import annotations

import base64
import asyncio
import json
import struct
from datetime import datetime, timezone
from threading import Lock

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from .log_manager import add_log, add_python_log, list_logs


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
_latest_depth_packet: bytes | None = None
_latest_rgb_packet: bytes | None = None
_rgb_preview_clients: set[WebSocket] = set()
_rgb_raw_preview_clients: set[WebSocket] = set()
_depth_preview_clients: set[WebSocket] = set()
_heartbeat_clients: set[WebSocket] = set()
_next_raycast_query_id = 1
_latest_raycast_result: dict = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/status")
async def status() -> dict:
    with _lock:
        return dict(_state)


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

            with _lock:
                global _latest_rgb_jpeg
                global _latest_rgb_packet
                _latest_rgb_jpeg = jpeg
                _latest_rgb_packet = data
                _state["last_rgb_frame_id"] = frame_id
                _state["last_rgb_size"] = f"{width}x{height}"
                _state["last_seen_utc"] = _utc_now_iso()

            await _broadcast_rgb_preview(jpeg)
            await _broadcast_rgb_raw_preview(data)
    except WebSocketDisconnect:
        add_python_log("warning", "RGB websocket disconnected")
    except Exception as ex:
        add_python_log("error", f"RGB websocket processing failed: {ex}")


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
        add_python_log("warning", "Dashboard preview client disconnected")


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
        add_python_log("warning", "Raw preview client disconnected")


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
        add_python_log("warning", "Dashboard preview depth client disconnected")


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
                        global _latest_rgb_jpeg
                        _latest_rgb_jpeg = base64.b64decode(payload_b64)
                        _state["last_rgb_frame_id"] = int(
                            payload.get("frame_id", _state["last_rgb_frame_id"])
                        )
                        _state["last_rgb_size"] = (
                            f"{payload.get('width', '?')}x{payload.get('height', '?')}"
                        )
                    else:
                        add_python_log(
                            "warning", "Received rgb_frame without payload_b64"
                        )

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


async def _broadcast_rgb_preview(jpeg_bytes: bytes) -> None:
    if not _rgb_preview_clients:
        return

    stale: list[WebSocket] = []
    for client in list(_rgb_preview_clients):
        try:
            await asyncio.wait_for(client.send_bytes(jpeg_bytes), timeout=0.02)
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
            await asyncio.wait_for(client.send_bytes(packet_bytes), timeout=0.02)
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
            await asyncio.wait_for(client.send_bytes(packet_bytes), timeout=0.02)
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
            await asyncio.wait_for(client.send_text(data), timeout=0.02)
            sent += 1
        except Exception:
            stale.append(client)

    for client in stale:
        _heartbeat_clients.discard(client)
    return sent


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("quest3server.main:app", host="0.0.0.0", port=8000, reload=True)
