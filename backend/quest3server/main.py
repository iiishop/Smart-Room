from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from .log_manager import add_log, add_python_log, install_python_logging_bridge, list_logs
from .tracking import TrackingEngine, TrackState
from .tracking.rgbd_final_alignment import (
    align_final_rgbd_capture_dir,
    align_final_rgbd_payload,
    decode_rgb_jpeg,
)
from .rgbd_stream_align import make_overlay_jpeg


app = FastAPI(title="Smart Room Receiver Backend")
install_python_logging_bridge()

_lock = Lock()
_state = {
    "connected": False,
    "active_connections": 0,
    "last_seen_utc": None,
    "last_payload": {},
    "last_tick": 0,
}
# Trigger pipeline state (survives after streaming removal)
_latest_rgb_bgr: Any = None
_latest_rgb_timestamp_ms: int | None = None
_latest_aligned_depth: np.ndarray | None = None
_latest_aligned_valid_mask: np.ndarray | None = None
_latest_debug_projection_meta: dict[str, Any] | None = None
_last_trigger_frame_jpeg: bytes | None = None
_last_trigger_pixel: tuple[int, int] | None = None
_last_rgb_intrinsics: np.ndarray | None = None
_last_trigger_bundle_meta: dict[str, Any] | None = None
_heartbeat_clients: set[WebSocket] = set()
_next_raycast_query_id = 1
_latest_raycast_result: dict = {}
_camera_intrinsics: dict = {}
_rgbd_overlay_clients: set[WebSocket] = set()
_latest_overlay_jpeg: bytes | None = None
# Nearest-depth index for hover queries (pre-built on each alignment cache)
_latest_depth_nearest_x: np.ndarray | None = None
_latest_depth_nearest_y: np.ndarray | None = None
_latest_depth_nearest_dist: np.ndarray | None = None

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


def _summarize_trigger_bundle(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(meta, dict):
        return None

    depth_snapshot = meta.get("depth_snapshot") or {}
    return {
        "trigger_timestamp_ms": meta.get("trigger_timestamp_ms"),
        "unity_frame_count": meta.get("unity_frame_count"),
        "pixel_xy": meta.get("pixel_xy"),
        "rgb_frame_wh": meta.get("rgb_frame_wh"),
        "rgb_requested_wh": meta.get("rgb_requested_wh"),
        "rgb_current_wh": meta.get("rgb_current_wh"),
        "depth_sampled_wh": depth_snapshot.get("sampled_wh"),
        "depth_source_wh": depth_snapshot.get("source_wh"),
        "depth_captured_at_unix_ms": depth_snapshot.get("captured_at_unix_ms"),
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/status")
async def status() -> dict:
    with _lock:
        snapshot = dict(_state)
        snapshot["camera_intrinsics"] = dict(_camera_intrinsics)
        snapshot["aligned_depth_meta"] = dict(_latest_debug_projection_meta or {})
    snapshot["latest_rgb_timestamp_ms"] = _latest_rgb_timestamp_ms
    snapshot["trigger_bundle"] = _summarize_trigger_bundle(_last_trigger_bundle_meta)
    snapshot["tracking"] = {
        "active": _tracking_engine is not None and _tracking_engine.state != TrackState.IDLE,
        "state": _tracking_engine.state.value if _tracking_engine else "idle",
        "label": _tracking_engine.label if _tracking_engine else "",
    }
    return snapshot


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


# ═══════════════════════ WebSocket: Trigger-only overlay ════════════

@app.websocket("/ws/rgbd-overlay")
async def rgbd_overlay_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    _rgbd_overlay_clients.add(websocket)
    add_python_log("info", "RGB-D overlay client connected on /ws/rgbd-overlay")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _rgbd_overlay_clients.discard(websocket)


# ═══════════════════════ WebSocket: Heartbeat ════════════════════════

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

                if payload_type == "camera_intrinsics":
                    global _camera_intrinsics
                    _camera_intrinsics = {
                        "fx": payload.get("fx"),
                        "fy": payload.get("fy"),
                        "cx": payload.get("cx"),
                        "cy": payload.get("cy"),
                        "projection_matrix": payload.get("projection_matrix"),
                        "sensor_width": payload.get("sensor_width"),
                        "sensor_height": payload.get("sensor_height"),
                        "requested_width": payload.get("requested_width"),
                        "requested_height": payload.get("requested_height"),
                        "current_width": payload.get("current_width"),
                        "current_height": payload.get("current_height"),
                        "stream_width": payload.get("stream_width"),
                        "stream_height": payload.get("stream_height"),
                        "preferred_width": payload.get("preferred_width"),
                        "preferred_height": payload.get("preferred_height"),
                        "supported_resolutions": payload.get("supported_resolutions") or [],
                        "timestamp_ms": payload.get("timestamp_ms"),
                    }

                elif payload_type == "client_log":
                    line = payload.get("line")
                    if isinstance(line, int) and line < 0:
                        line = None
                    add_log(
                        level=payload.get("level", "INFO"),
                        source=payload.get("source", "unity"),
                        message=payload.get("message", "(empty)"),
                        script=payload.get("script") or None,
                        line=line,
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


# ═══════════════════════ Packet parsers ═════════════════════════════


# ═══════════════════════ Broadcasting helpers ════════════════════════

async def _broadcast_rgbd_overlay(jpeg_bytes: bytes) -> None:
    """Send aligned RGB-D overlay JPEG to all connected overlay clients."""
    if not _rgbd_overlay_clients:
        return
    stale: list[WebSocket] = []
    for client in list(_rgbd_overlay_clients):
        try:
            await asyncio.wait_for(client.send_bytes(jpeg_bytes), timeout=1.0)
        except Exception:
            stale.append(client)
    for client in stale:
        _rgbd_overlay_clients.discard(client)


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




def _decode_base64_payload(body: dict[str, Any], *names: str) -> bytes:
    for name in names:
        value = body.get(name)
        if isinstance(value, str) and value:
            if "," in value and value.lstrip().startswith("data:"):
                value = value.split(",", 1)[1]
            return base64.b64decode(value)
    joined = " or ".join(names)
    raise HTTPException(status_code=400, detail=f"{joined} required")


def _parse_final_rgbd_meta(body: dict[str, Any]) -> dict[str, Any]:
    meta = body.get("meta")
    if isinstance(meta, dict):
        return meta
    meta_json = body.get("meta_json")
    if isinstance(meta_json, str) and meta_json.strip():
        try:
            parsed = json.loads(meta_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"meta_json is invalid JSON: {exc}") from exc
        if isinstance(parsed, dict):
            return parsed
    raise HTTPException(status_code=400, detail="meta or meta_json required")


def _decode_final_depth_raw(body: dict[str, Any], meta: dict[str, Any]) -> np.ndarray:
    depth_meta = meta.get("depth")
    if not isinstance(depth_meta, dict):
        raise HTTPException(status_code=400, detail="meta.depth required")
    try:
        depth_w = int(depth_meta["resolution_w"])
        depth_h = int(depth_meta["resolution_h"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="meta.depth resolution_w/resolution_h required") from exc
    if depth_w <= 0 or depth_h <= 0:
        raise HTTPException(status_code=400, detail="meta.depth resolution must be positive")

    depth_bytes = _decode_base64_payload(
        body,
        "depth_raw_f32_le_b64",
        "depth_raw_b64",
        "depth_raw",
    )
    expected = depth_w * depth_h * 4
    if len(depth_bytes) != expected:
        raise HTTPException(
            status_code=400,
            detail=f"depth raw length mismatch: expected {expected}, got {len(depth_bytes)}",
        )
    return np.frombuffer(depth_bytes, dtype="<f4").reshape((depth_h, depth_w)).copy()


def _timestamp_from_final_meta(meta: dict[str, Any]) -> int:
    value = meta.get("timestamp_unix_ms")
    if isinstance(value, (int, float)):
        return int(value)
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _resolve_pixel_for_frame(
    *,
    pixel_x: float,
    pixel_y: float,
    width: int,
    height: int,
    trigger_bundle_meta: dict[str, Any] | None = None,
) -> tuple[int, int]:
    viewport_xy = None
    if isinstance(trigger_bundle_meta, dict):
        viewport_xy = (
            trigger_bundle_meta.get("cursor_viewport_xy")
            or trigger_bundle_meta.get("viewport_xy")
        )
    if isinstance(viewport_xy, list) and len(viewport_xy) >= 2:
        try:
            vx = float(viewport_xy[0])
            vy = float(viewport_xy[1])
        except (TypeError, ValueError):
            vx = vy = -1.0
        if 0.0 <= vx <= 1.0 and 0.0 <= vy <= 1.0:
            return (
                int(np.clip(vx * width, 0, width - 1)),
                int(np.clip((1.0 - vy) * height, 0, height - 1)),
            )

    return (
        int(pixel_x * width) if pixel_x <= 1.0 else int(pixel_x),
        int(pixel_y * height) if pixel_y <= 1.0 else int(pixel_y),
    )


def _cache_final_rgbd_alignment(
    *,
    alignment,
    rgb_jpeg: bytes,
    timestamp_ms: int,
) -> None:
    global _latest_rgb_jpeg, _latest_rgb_bgr, _latest_rgb_timestamp_ms
    global _latest_aligned_depth, _latest_aligned_valid_mask
    global _latest_debug_projection_meta, _last_rgb_intrinsics

    rgb_h, rgb_w = alignment.rgb_bgr.shape[:2]
    with _lock:
        _latest_rgb_jpeg = rgb_jpeg
        _latest_rgb_bgr = alignment.rgb_bgr
        _latest_rgb_timestamp_ms = timestamp_ms
        _latest_aligned_depth = alignment.aligned_depth_m.copy()
        _latest_aligned_valid_mask = alignment.valid_mask.copy()
        _latest_debug_projection_meta = dict(alignment.summary)
        _last_rgb_intrinsics = alignment.rgb_intrinsics.copy()
        _state["last_rgb_frame_id"] = int(_state.get("last_rgb_frame_id", 0)) + 1
        _state["last_rgb_size"] = f"{rgb_w}x{rgb_h}"
        _state["last_seen_utc"] = _utc_now_iso()

    # Build nearest-depth index for hover queries (outside lock)
    global _latest_depth_nearest_x, _latest_depth_nearest_y, _latest_depth_nearest_dist
    _ad = alignment.aligned_depth_m
    _valid = _ad > 0
    if _valid.any():
        _mask = np.where(_valid, 0, 255).astype(np.uint8)
        _dist, _labels = cv2.distanceTransformWithLabels(
            _mask, cv2.DIST_L2, cv2.DIST_MASK_3, labelType=cv2.DIST_LABEL_PIXEL,
        )
        _vy, _vx = np.where(_valid)
        _max_lbl = int(_labels.max())
        _lx = np.full(_max_lbl + 1, -1, dtype=np.int32)
        _ly = np.full(_max_lbl + 1, -1, dtype=np.int32)
        _vl = _labels[_vy, _vx]
        _lx[_vl] = _vx
        _ly[_vl] = _vy
        _latest_depth_nearest_x = _lx[_labels]
        _latest_depth_nearest_y = _ly[_labels]
        _latest_depth_nearest_dist = _dist.astype(np.float32)
    else:
        _latest_depth_nearest_x = None
        _latest_depth_nearest_y = None
        _latest_depth_nearest_dist = None


async def _run_tracking_detection(
    *,
    pixel_x: float,
    pixel_y: float,
    rgb_bgr: np.ndarray,
    rgb_jpeg: bytes | None,
    aligned_depth: np.ndarray | None,
    rgb_intrinsics: np.ndarray | None,
    trigger_bundle_meta: dict[str, Any] | None,
    alignment_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if rgb_bgr is None:
        raise HTTPException(status_code=503, detail="No RGB frame available yet")
    if not _models_ready:
        raise HTTPException(status_code=503, detail="Tracking models not ready yet (still loading)")

    engine = _get_tracking_engine()
    engine.stop()
    h, w = rgb_bgr.shape[:2]
    px, py = _resolve_pixel_for_frame(
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        width=w,
        height=h,
        trigger_bundle_meta=trigger_bundle_meta,
    )
    px = int(np.clip(px, 0, w - 1))
    py = int(np.clip(py, 0, h - 1))

    global _last_trigger_frame_jpeg, _last_trigger_pixel
    global _last_trigger_bundle_meta, _last_rgb_intrinsics
    _last_trigger_pixel = (px, py)
    _last_trigger_bundle_meta = trigger_bundle_meta if isinstance(trigger_bundle_meta, dict) else None
    if rgb_jpeg is not None:
        _last_trigger_frame_jpeg = rgb_jpeg
    if rgb_intrinsics is not None:
        _last_rgb_intrinsics = rgb_intrinsics.copy()

    if isinstance(_last_trigger_bundle_meta, dict):
        trigger_ts = _last_trigger_bundle_meta.get("trigger_timestamp_ms")
        rgb_delta = None
        if isinstance(trigger_ts, (int, float)) and _latest_rgb_timestamp_ms is not None:
            rgb_delta = int(_latest_rgb_timestamp_ms - int(trigger_ts))
        add_python_log(
            "info",
            f"Trigger bundle stored: trigger_ts={trigger_ts}, latest_rgb_ts={_latest_rgb_timestamp_ms}, rgb_delta_ms={rgb_delta}, "
            f"rgb_frame_wh={_last_trigger_bundle_meta.get('rgb_frame_wh')}, depth_snapshot={(_last_trigger_bundle_meta.get('depth_snapshot') or {}).get('sampled_wh')}"
        )

    result = engine.detect(
        px,
        py,
        rgb_bgr.copy(),
        aligned_depth_m=aligned_depth.copy() if aligned_depth is not None else None,
        rgb_intrinsics=rgb_intrinsics.copy() if rgb_intrinsics is not None else None,
    )
    source = (alignment_summary or {}).get("source", "cached")
    add_python_log("info", f"Tracking detect at ({px},{py}) source={source} -> {result.label}")

    global _last_detection_crop_jpeg
    x0, y0, x1, y1 = result.box_xyxy
    x0_c = max(0, int(x0))
    y0_c = max(0, int(y0))
    x1_c = min(w, int(x1))
    y1_c = min(h, int(y1))
    if x1_c > x0_c and y1_c > y0_c:
        crop = rgb_bgr[y0_c:y1_c, x0_c:x1_c]
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ok:
            _last_detection_crop_jpeg = buf.tobytes()

    payload = result.to_payload()
    if alignment_summary is not None:
        payload.setdefault("diagnostics", {})["rgbd_alignment"] = alignment_summary
    await _broadcast_tracking(payload)
    response = {"ok": True, "result": payload}
    if alignment_summary is not None:
        response["alignment_summary"] = alignment_summary
    return response


# ═══════════════════════ Tracking API ════════════════════════════════

@app.post("/api/track/start")
async def track_start(body: dict) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail="Legacy tracking endpoint disabled. Use /api/track/start-final-rgbd.",
    )


@app.post("/api/track/start-final-rgbd")
async def track_start_final_rgbd(body: dict) -> dict[str, Any]:
    pixel_x = float(body.get("pixel_x", -1))
    pixel_y = float(body.get("pixel_y", -1))
    if pixel_x < 0 or pixel_y < 0:
        raise HTTPException(status_code=400, detail="pixel_x and pixel_y required")

    try:
        meta = _parse_final_rgbd_meta(body)
        rgb_jpeg = _decode_base64_payload(body, "rgb_jpeg_b64", "rgb_b64", "rgb_jpeg")
        raw_depth = _decode_final_depth_raw(body, meta)
        rgb_bgr = decode_rgb_jpeg(rgb_jpeg)
        alignment = align_final_rgbd_payload(
            rgb_bgr=rgb_bgr,
            raw_depth=raw_depth,
            meta=meta,
            min_depth=float(body.get("min_depth", 0.2)),
            max_depth=float(body.get("max_depth", 8.0)),
        )
    except HTTPException:
        raise
    except Exception as exc:
        add_python_log("warning", f"Final RGB-D alignment failed: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _cache_final_rgbd_alignment(
        alignment=alignment,
        rgb_jpeg=rgb_jpeg,
        timestamp_ms=_timestamp_from_final_meta(meta),
    )

    # Generate and broadcast RGB-D overlay from trigger's own aligned data
    rgb_rgb = cv2.cvtColor(alignment.rgb_bgr, cv2.COLOR_BGR2RGB)
    overlay_jpeg = make_overlay_jpeg(rgb_rgb, alignment.aligned_depth_m)
    if overlay_jpeg:
        _latest_overlay_jpeg = overlay_jpeg
        await _broadcast_rgbd_overlay(overlay_jpeg)

    trigger_bundle_meta = body.get("trigger_bundle_meta")
    if not isinstance(trigger_bundle_meta, dict):
        trigger_bundle_meta = None

    return await _run_tracking_detection(
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        rgb_bgr=alignment.rgb_bgr,
        rgb_jpeg=rgb_jpeg,
        aligned_depth=alignment.aligned_depth_m,
        rgb_intrinsics=alignment.rgb_intrinsics,
        trigger_bundle_meta=trigger_bundle_meta,
        alignment_summary=alignment.summary,
    )


@app.post("/api/track/start-final-rgbd-capture-dir")
async def track_start_final_rgbd_capture_dir(body: dict) -> dict[str, Any]:
    capture_dir_value = body.get("capture_dir")
    if not isinstance(capture_dir_value, str) or not capture_dir_value.strip():
        raise HTTPException(status_code=400, detail="capture_dir required")
    pixel_x = float(body.get("pixel_x", -1))
    pixel_y = float(body.get("pixel_y", -1))
    if pixel_x < 0 or pixel_y < 0:
        raise HTTPException(status_code=400, detail="pixel_x and pixel_y required")

    capture_dir = Path(capture_dir_value)
    try:
        alignment = align_final_rgbd_capture_dir(
            capture_dir,
            output_dir=body.get("output_dir"),
            min_depth=float(body.get("min_depth", 0.2)),
            max_depth=float(body.get("max_depth", 8.0)),
            write_outputs=bool(body.get("write_outputs", True)),
        )
        rgb_jpeg = (capture_dir / "rgb.jpg").read_bytes()
        meta = json.loads((capture_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception as exc:
        add_python_log("warning", f"Final RGB-D capture-dir alignment failed: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _cache_final_rgbd_alignment(
        alignment=alignment,
        rgb_jpeg=rgb_jpeg,
        timestamp_ms=_timestamp_from_final_meta(meta),
    )

    # Generate and broadcast RGB-D overlay from trigger's own aligned data
    rgb_rgb = cv2.cvtColor(alignment.rgb_bgr, cv2.COLOR_BGR2RGB)
    overlay_jpeg = make_overlay_jpeg(rgb_rgb, alignment.aligned_depth_m)
    if overlay_jpeg:
        _latest_overlay_jpeg = overlay_jpeg
        await _broadcast_rgbd_overlay(overlay_jpeg)

    trigger_bundle_meta = body.get("trigger_bundle_meta")
    if not isinstance(trigger_bundle_meta, dict):
        trigger_bundle_meta = None

    return await _run_tracking_detection(
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        rgb_bgr=alignment.rgb_bgr,
        rgb_jpeg=rgb_jpeg,
        aligned_depth=alignment.aligned_depth_m,
        rgb_intrinsics=alignment.rgb_intrinsics,
        trigger_bundle_meta=trigger_bundle_meta,
        alignment_summary=alignment.summary,
    )


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


@app.get("/api/trigger-bundle/latest")
async def trigger_bundle_latest() -> dict[str, Any]:
    return {
        "ok": _last_trigger_bundle_meta is not None,
        "bundle": _last_trigger_bundle_meta,
        "latest_rgb_timestamp_ms": _latest_rgb_timestamp_ms,
        "has_aligned_depth": _latest_aligned_depth is not None,
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


@app.post("/api/depth/aligned")
async def receive_aligned_depth(request: Request):
    """Receive strict sparse aligned depth from Unity as JSON + base64 payloads."""
    raise HTTPException(
        status_code=410,
        detail="Legacy aligned-depth upload disabled. Use /api/track/start-final-rgbd.",
    )
    global _latest_aligned_depth, _latest_aligned_valid_mask, _latest_debug_projection_meta, _last_rgb_intrinsics
    try:
        body = await request.json()
        w = int(body.get("width", 0))
        h = int(body.get("height", 0))
        sparse_b64 = body.get("sparse_depth_f32_le")
        valid_b64 = body.get("valid_mask_u8")
        rgb_intrinsics9 = body.get("rgb_intrinsics9")
        debug_meta = body.get("debug_projection_meta") or {}
        if w <= 0 or h <= 0:
            raise HTTPException(status_code=400, detail="width/height required")
        if not sparse_b64 or not valid_b64:
            raise HTTPException(status_code=400, detail="sparse_depth_f32_le and valid_mask_u8 required")

        sparse_bytes = base64.b64decode(sparse_b64)
        valid_bytes = base64.b64decode(valid_b64)
        expected_sparse = w * h * 4
        expected_mask = w * h
        if len(sparse_bytes) != expected_sparse:
            raise HTTPException(status_code=400, detail=f"sparse payload length mismatch: expected {expected_sparse}, got {len(sparse_bytes)}")
        if len(valid_bytes) != expected_mask:
            raise HTTPException(status_code=400, detail=f"mask payload length mismatch: expected {expected_mask}, got {len(valid_bytes)}")

        arr = np.frombuffer(sparse_bytes, dtype="<f4").reshape((h, w)).copy()
        valid_mask = np.frombuffer(valid_bytes, dtype=np.uint8).reshape((h, w)).copy()
        arr[valid_mask == 0] = np.nan
        _latest_aligned_depth = arr
        _latest_aligned_valid_mask = valid_mask
        _latest_debug_projection_meta = debug_meta

        if isinstance(rgb_intrinsics9, list) and len(rgb_intrinsics9) == 9:
            _last_rgb_intrinsics = np.array(rgb_intrinsics9, dtype=np.float32).reshape((3, 3))
        elif _last_rgb_intrinsics is None:
            raise HTTPException(status_code=400, detail="rgb_intrinsics9 missing and no trigger intrinsics stored yet")
        valid_count = int((valid_mask > 0).sum())
        valid_ratio = valid_count / float(w * h)
        add_python_log("debug",
            f"Aligned depth received: {w}×{h}, "
            f"range=[{np.nanmin(arr):.2f}, {np.nanmax(arr):.2f}]m, "
            f"valid={int((~np.isnan(arr)).sum())}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        add_python_log("warning", f"Aligned depth receive failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/depth/aligned-v2")
async def receive_aligned_depth_v2(request: Request):
    """Receive strict sparse aligned depth from Unity as JSON + base64 payloads."""
    raise HTTPException(
        status_code=410,
        detail="Legacy aligned-depth v2 upload disabled. Use /api/track/start-final-rgbd.",
    )
    global _latest_aligned_depth, _latest_aligned_valid_mask, _latest_debug_projection_meta, _last_rgb_intrinsics
    try:
        body = await request.json()
        w = int(body.get("width", 0))
        h = int(body.get("height", 0))
        sparse_b64 = body.get("sparse_depth_f32_le")
        valid_b64 = body.get("valid_mask_u8")
        rgb_intrinsics9 = body.get("rgb_intrinsics9")
        debug_meta = body.get("debug_projection_meta") or {}
        if w <= 0 or h <= 0:
            raise HTTPException(status_code=400, detail="width/height required")
        if not sparse_b64 or not valid_b64:
            raise HTTPException(status_code=400, detail="sparse_depth_f32_le and valid_mask_u8 required")

        sparse_bytes = base64.b64decode(sparse_b64)
        valid_bytes = base64.b64decode(valid_b64)
        expected_sparse = w * h * 4
        expected_mask = w * h
        if len(sparse_bytes) != expected_sparse:
            raise HTTPException(status_code=400, detail=f"sparse payload length mismatch: expected {expected_sparse}, got {len(sparse_bytes)}")
        if len(valid_bytes) != expected_mask:
            raise HTTPException(status_code=400, detail=f"mask payload length mismatch: expected {expected_mask}, got {len(valid_bytes)}")

        arr = np.frombuffer(sparse_bytes, dtype="<f4").reshape((h, w)).copy()
        valid_mask = np.frombuffer(valid_bytes, dtype=np.uint8).reshape((h, w)).copy()
        arr[valid_mask == 0] = np.nan
        _latest_aligned_depth = arr
        _latest_aligned_valid_mask = valid_mask
        _latest_debug_projection_meta = debug_meta

        if isinstance(rgb_intrinsics9, list) and len(rgb_intrinsics9) == 9:
            _last_rgb_intrinsics = np.array(rgb_intrinsics9, dtype=np.float32).reshape((3, 3))
        elif _last_rgb_intrinsics is None:
            raise HTTPException(status_code=400, detail="rgb_intrinsics9 missing and no trigger intrinsics stored yet")

        valid_count = int((valid_mask > 0).sum())
        valid_ratio = valid_count / float(w * h)
        add_python_log(
            "debug",
            f"Aligned depth v2 received: {w}x{h}, "
            f"range=[{np.nanmin(arr):.2f}, {np.nanmax(arr):.2f}]m, "
            f"valid={valid_count}, ratio={valid_ratio:.4f}, semantics={debug_meta.get('depth_value_semantics', 'unknown')}",
        )
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        add_python_log("warning", f"Aligned depth v2 receive failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/depth/at")
async def depth_at_pixel(px: int = Query(...), py: int = Query(...)):
    """Return aligned depth and RGB-camera XYZ (metres) at an RGB pixel.

    Falls back to the nearest valid depth pixel when the exact pixel has no
    depth, using a pre-built distance-transform index (updated every trigger).
    """
    if _latest_aligned_depth is None:
        return {
            "depth_m": None, "valid": False,
            "sample_px": px, "sample_py": py, "source": "none",
        }

    h, w = _latest_aligned_depth.shape
    if px < 0 or px >= w or py < 0 or py >= h:
        return {
            "depth_m": None, "valid": False,
            "sample_px": px, "sample_py": py, "source": "out_of_bounds",
        }

    depth = float(_latest_aligned_depth[py, px])
    sample_px, sample_py = px, py
    source = "exact"
    distance_px = 0.0

    if depth <= 0 and _latest_depth_nearest_x is not None:
        nx = int(_latest_depth_nearest_x[py, px])
        ny = int(_latest_depth_nearest_y[py, px])
        if nx >= 0 and ny >= 0:
            nd = float(_latest_aligned_depth[ny, nx])
            if nd > 0:
                depth = nd
                sample_px, sample_py = nx, ny
                source = "nearest"
                distance_px = round(float(_latest_depth_nearest_dist[py, px]), 1)

    if depth <= 0:
        return {
            "depth_m": None, "valid": False,
            "sample_px": px, "sample_py": py, "source": source,
        }

    # Compute RGB-camera XYZ (x→right, y→up, z→forward)
    if _last_rgb_intrinsics is not None:
        fx = float(_last_rgb_intrinsics[0, 0])
        fy = float(_last_rgb_intrinsics[1, 1])
        cx = float(_last_rgb_intrinsics[0, 2])
        cy = float(_last_rgb_intrinsics[1, 2])
        sensor_y = (h - 1) - float(sample_py)  # flip image→sensor
        x_cam = (float(sample_px) - cx) * depth / max(fx, 1e-6)
        y_cam = (sensor_y - cy) * depth / max(fy, 1e-6)
        return {
            "depth_m": round(depth, 4),
            "valid": True,
            "sample_px": sample_px,
            "sample_py": sample_py,
            "source": source,
            "distance_px": distance_px,
            "rgb_cam_x": round(x_cam, 4),
            "rgb_cam_y": round(y_cam, 4),
            "rgb_cam_z": round(depth, 4),
        }

    return {
        "depth_m": round(depth, 4),
        "valid": True,
        "sample_px": sample_px,
        "sample_py": sample_py,
        "source": source,
        "distance_px": distance_px,
    }


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


@app.get("/api/depth/debug-meta")
async def depth_debug_meta() -> dict[str, Any]:
    return {
        "ok": _latest_debug_projection_meta is not None,
        "debug_projection_meta": _latest_debug_projection_meta,
        "has_sparse_aligned_depth": _latest_aligned_depth is not None,
        "has_valid_mask": _latest_aligned_valid_mask is not None,
    }


@app.get("/api/depth/aligned-heatmap")
async def depth_aligned_heatmap():
    """Return the aligned-depth frame as a JPEG heatmap, same resolution as RGB.

    Uses depth values reprojected into the RGB frame via align_depth_to_rgb().
    Returns 204 if no aligned depth is available yet (no trigger with intrinsics).
    """
    if _latest_aligned_depth is None or _latest_aligned_valid_mask is None:
        return Response(status_code=204)

    # Depth (metres) → colour heatmap (near=red, far=blue)
    valid = (_latest_aligned_valid_mask > 0) & ~np.isnan(_latest_aligned_depth) & (_latest_aligned_depth > 0)
    d_max = float(np.nanmax(_latest_aligned_depth[valid])) if valid.any() else 5.0
    d_max = np.clip(d_max, 0.5, 8.0)

    d_norm = np.clip(_latest_aligned_depth / max(d_max, 0.01), 0, 1)
    d_norm = np.where(valid, 1.0 - d_norm, 0.0)

    hue = (d_norm * 120).astype(np.uint8)  # 0° red → 120° green (far)
    sat = np.full_like(hue, 200, dtype=np.uint8)
    val = np.where(valid, 255, 0).astype(np.uint8)
    hsv = np.stack([hue, sat, val], axis=2)
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    alpha = np.where(valid, 255, 0).astype(np.uint8)
    bgra = np.dstack([bgr, alpha])

    ok, buf = cv2.imencode(".png", bgra)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to encode")
    return Response(content=buf.tobytes(), media_type="image/png")


@app.get("/api/depth/verify-conversion")
async def depth_verify_conversion():
    """Cross-validate Meta's ZBP formula against t-34400's NDC→linear.

    Takes the latest raw depth packet (NDC buffer) and ZBufferParams,
    converts via both formulas, and reports any discrepancy.

    Returns 204 if no raw depth data is available.
    """
    from quest3server.tracking.depth_alignment import verify_depth_conversion

    global _depth_source_meta, _latest_depth_packet

    zbp = _depth_source_meta.get("zbuffer_params") if _depth_source_meta else None
    if zbp is None or len(zbp) < 2:
        return {"error": "no ZBufferParams available from depth source meta"}

    raw_array = _depth_packet_to_array(_latest_depth_packet) if _latest_depth_packet else None
    if raw_array is None:
        return Response(status_code=204)

    # The raw DEP1 packet contains NDC depth values [0, 1]
    zbp_arr = np.array([zbp[0], zbp[1], zbp[2] if len(zbp) > 2 else 0, zbp[3] if len(zbp) > 3 else 0], dtype=np.float32)
    result = verify_depth_conversion(raw_array, zbp_arr)

    return result


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
