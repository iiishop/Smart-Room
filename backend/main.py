from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import Lock

from fastapi import FastAPI, WebSocket, WebSocketDisconnect


app = FastAPI(title="Smart Room Receiver Backend")

_lock = Lock()
_state = {
    "connected": False,
    "active_connections": 0,
    "last_seen_utc": None,
    "last_payload": {},
    "last_tick": 0,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/status")
async def status() -> dict:
    with _lock:
        return dict(_state)


@app.websocket("/ws/heartbeat")
async def heartbeat_socket(websocket: WebSocket) -> None:
    await websocket.accept()

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

            with _lock:
                _state["last_payload"] = payload
                _state["last_seen_utc"] = _utc_now_iso()
                _state["last_tick"] = int(payload.get("tick", _state["last_tick"]))

            await websocket.send_text("ack")
    except WebSocketDisconnect:
        pass
    finally:
        with _lock:
            _state["active_connections"] = max(0, _state["active_connections"] - 1)
            _state["connected"] = _state["active_connections"] > 0


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
