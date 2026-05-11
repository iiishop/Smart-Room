from __future__ import annotations

import json
import logging
import threading
from enum import Enum
from typing import Callable, Optional

import websocket

logger = logging.getLogger(__name__)

StateCallback = Callable[[str, str, Optional[dict]], None]


class _ConnState(Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    AUTH_SENDING = "auth_sending"
    CONNECTED = "connected"
    SUBSCRIBED = "subscribed"


class HAWebSocketClient:
    def __init__(
        self,
        ws_url: str,
        token: str,
        reconnect_interval: float = 5.0,
        max_reconnect_attempts: int = 10,
    ):
        self._ws_url = ws_url.rstrip("/")
        if not self._ws_url.endswith("/api/websocket"):
            self._ws_url += "/api/websocket"
        self._token = token
        self._reconnect_interval = reconnect_interval
        self._max_reconnect_attempts = max_reconnect_attempts

        self._state = _ConnState.IDLE
        self._state_lock = threading.Lock()

        self._ws_app: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None

        self._msg_id = 0
        self._msg_id_lock = threading.Lock()

        self._callbacks: list[StateCallback] = []
        self._callbacks_lock = threading.Lock()

        self._states_cache: dict[str, dict] = {}
        self._states_lock = threading.Lock()

        self._shutdown = threading.Event()
        self._reconnect_count = 0
        self._backoff = 0.0

    @property
    def connected(self) -> bool:
        return self._state in (_ConnState.CONNECTED, _ConnState.SUBSCRIBED)

    def _next_id(self) -> int:
        with self._msg_id_lock:
            self._msg_id += 1
            return self._msg_id

    def connect(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._shutdown.clear()
        self._reconnect_count = 0
        self._backoff = 0.0
        with self._state_lock:
            self._state = _ConnState.IDLE

        self._thread = threading.Thread(target=self._run_forever, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self._shutdown.set()
        if self._ws_app:
            try:
                self._ws_app.close()
            except Exception:
                pass
        with self._state_lock:
            self._state = _ConnState.IDLE

    def subscribe(self, callback: StateCallback) -> None:
        with self._callbacks_lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def unsubscribe(self, callback: StateCallback) -> None:
        with self._callbacks_lock:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass

    def call_service(
        self,
        domain: str,
        service: str,
        target_entity: str = None,
        service_data: dict = None,
    ) -> int:
        msg_id = self._next_id()
        payload: dict = {
            "id": msg_id,
            "type": "call_service",
            "domain": domain,
            "service": service,
        }
        if target_entity:
            payload["target"] = {"entity_id": target_entity}
        if service_data:
            payload["service_data"] = service_data

        if self._ws_app:
            try:
                self._ws_app.send(json.dumps(payload))
            except Exception as e:
                logger.error(f"call_service send failed: {e}")

        return msg_id

    def get_states(self) -> list[dict]:
        with self._states_lock:
            return list(self._states_cache.values())

    # ------------------------------------------------------------------
    # background thread
    # ------------------------------------------------------------------

    def _run_forever(self) -> None:
        while not self._shutdown.is_set():
            if self._reconnect_count >= self._max_reconnect_attempts:
                logger.error(
                    "Max reconnect attempts (%d) reached, giving up",
                    self._max_reconnect_attempts,
                )
                break

            with self._state_lock:
                self._state = _ConnState.CONNECTING

            self._ws_app = websocket.WebSocketApp(
                self._ws_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            try:
                self._ws_app.run_forever()
            except Exception as e:
                logger.error(f"run_forever exception: {e}")

            if self._shutdown.is_set():
                break

            self._reconnect_count += 1
            self._backoff = self._backoff or self._reconnect_interval

            logger.info(
                "Reconnecting in %.1fs (attempt %d/%d)",
                self._backoff,
                self._reconnect_count,
                self._max_reconnect_attempts,
            )
            self._shutdown.wait(timeout=self._backoff)
            self._backoff = min(self._backoff * 2, 60.0)

    # ------------------------------------------------------------------
    # WebSocket callbacks
    # ------------------------------------------------------------------

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        logger.info("WebSocket opened, waiting for auth_required")

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            logger.error("Failed to parse WebSocket message: %s", message)
            return

        msg_type = msg.get("type")

        if msg_type == "auth_required":
            self._handle_auth_required(ws, msg)

        elif msg_type == "auth_ok":
            self._handle_auth_ok(ws)

        elif msg_type == "auth_invalid":
            self._handle_auth_invalid(ws, msg)

        elif msg_type == "result":
            self._handle_result(msg)

        elif msg_type == "event":
            self._handle_event(msg)

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        logger.error("WebSocket error: %s", error)

    def _on_close(
        self,
        ws: websocket.WebSocketApp,
        close_status_code: Optional[int],
        close_msg: Optional[str],
    ) -> None:
        logger.info(
            "WebSocket closed (code=%s, msg=%s)", close_status_code, close_msg
        )
        was_connected = self._state in (
            _ConnState.CONNECTED,
            _ConnState.SUBSCRIBED,
        )
        with self._state_lock:
            self._state = _ConnState.IDLE

        if was_connected:
            self._notify_callbacks("disconnected", "", None)

    # ------------------------------------------------------------------
    # auth handlers
    # ------------------------------------------------------------------

    def _handle_auth_required(
        self, ws: websocket.WebSocketApp, msg: dict
    ) -> None:
        ha_version = msg.get("ha_version", "unknown")
        logger.info("HA version %s – sending auth token", ha_version)
        with self._state_lock:
            self._state = _ConnState.AUTH_SENDING
        ws.send(json.dumps({"type": "auth", "access_token": self._token}))

    def _handle_auth_ok(self, ws: websocket.WebSocketApp) -> None:
        with self._state_lock:
            self._state = _ConnState.CONNECTED
        self._reconnect_count = 0
        self._backoff = 0.0
        logger.info("Authentication successful")

        self._notify_callbacks("connected", "", None)

        sub_id = self._next_id()
        ws.send(
            json.dumps(
                {
                    "id": sub_id,
                    "type": "subscribe_events",
                    "event_type": "state_changed",
                }
            )
        )
        logger.info("Subscribed to state_changed events (id=%d)", sub_id)

        get_states_id = self._next_id()
        ws.send(
            json.dumps({"id": get_states_id, "type": "get_states"})
        )
        logger.info("Requested initial states (id=%d)", get_states_id)

    def _handle_auth_invalid(
        self, ws: websocket.WebSocketApp, msg: dict
    ) -> None:
        err_msg = msg.get("message", "unknown")
        logger.error("Authentication invalid: %s – not reconnecting", err_msg)
        self._shutdown.set()
        with self._state_lock:
            self._state = _ConnState.IDLE
        try:
            ws.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # result / event handlers
    # ------------------------------------------------------------------

    def _handle_result(self, msg: dict) -> None:
        result = msg.get("result")
        if not isinstance(result, list):
            return

        with self._states_lock:
            self._states_cache.clear()
            for entity_data in result:
                entity_id = entity_data.get("entity_id", "")
                self._states_cache[entity_id] = entity_data

        logger.info("Loaded %d initial states", len(self._states_cache))

        for entity_id, entity_data in self._states_cache.items():
            self._notify_callbacks(
                "initial", entity_id, entity_data
            )

        with self._state_lock:
            self._state = _ConnState.SUBSCRIBED

    def _handle_event(self, msg: dict) -> None:
        event = msg.get("event", {})
        event_type = event.get("event_type", "")
        if event_type != "state_changed":
            return

        event_data = event.get("data", {})
        entity_data = event_data.get("entity", {})
        entity_id = entity_data.get("entity_id", "")
        new_state = entity_data.get("new_state", {})

        if not entity_id:
            return

        if new_state and new_state.get("state") is not None:
            with self._states_lock:
                self._states_cache[entity_id] = new_state
            self._notify_callbacks("update", entity_id, new_state)
        else:
            with self._states_lock:
                self._states_cache.pop(entity_id, None)
            self._notify_callbacks("removed", entity_id, entity_data)

    # ------------------------------------------------------------------
    # callback dispatch
    # ------------------------------------------------------------------

    def _notify_callbacks(
        self,
        event: str,
        entity_id: str,
        entity_data: Optional[dict],
    ) -> None:
        with self._callbacks_lock:
            callbacks = list(self._callbacks)

        for cb in callbacks:
            try:
                cb(event, entity_id, entity_data)
            except Exception as e:
                logger.error("State callback error: %s", e)
