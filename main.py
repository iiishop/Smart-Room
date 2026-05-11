import os
import sys
import tkinter as tk

from config import ConfigManager
from ha_client.core.event_bus import EventBus, EventType
from utils.reconnect import exponential_backoff

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
from ha import HAWebSocketClient

import requests


class HARestClient:
    def __init__(self, rest_url, token):
        self._rest_url = rest_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })

    def get_states(self):
        r = self._session.get(f"{self._rest_url}/states", timeout=10)
        r.raise_for_status()
        return r.json()

    def call_service(self, domain, service, entity_id=None, service_data=None):
        url = f"{self._rest_url}/services/{domain}/{service}"
        payload = {}
        if entity_id:
            payload["entity_id"] = entity_id
        if service_data:
            payload.update(service_data)
        r = self._session.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return r.json()

    def turn_on(self, entity_id):
        domain = entity_id.split(".")[0]
        return self.call_service(domain, "turn_on", entity_id=entity_id)

    def turn_off(self, entity_id):
        domain = entity_id.split(".")[0]
        return self.call_service(domain, "turn_off", entity_id=entity_id)

    def toggle(self, entity_id):
        domain = entity_id.split(".")[0]
        return self.call_service(domain, "toggle", entity_id=entity_id)

    def set_brightness(self, entity_id, value):
        domain = entity_id.split(".")[0]
        return self.call_service(domain, "turn_on", entity_id=entity_id,
                                 service_data={"brightness_pct": value})


class DeviceBridge:
    def __init__(self, ws_client, rest_client, root):
        self._ws = ws_client
        self._root = root
        self.event_bus = EventBus()
        self.devices = {}
        self.connection_mgr = self
        self.rest = rest_client

        self._ws.subscribe(self._on_ws_event)

    def disconnect(self):
        self._ws.disconnect()

    def _on_ws_event(self, event, entity_id, entity_data):
        if event == "initial":
            self.devices[entity_id] = entity_data
            self._root.after(0, lambda: self.event_bus.emit_sync(
                EventType.DEVICE_ADDED, entity_id=entity_id, device=entity_data
            ))
            self._root.after(0, lambda: self.event_bus.emit_sync(
                EventType.STATE_CHANGED, entity_id=entity_id, device=entity_data
            ))
        elif event == "update":
            self.devices[entity_id] = entity_data
            self._root.after(0, lambda: self.event_bus.emit_sync(
                EventType.STATE_CHANGED, entity_id=entity_id, device=entity_data
            ))
        elif event == "removed":
            self.devices.pop(entity_id, None)
            self._root.after(0, lambda: self.event_bus.emit_sync(
                EventType.DEVICE_REMOVED, entity_id=entity_id
            ))
        elif event == "connected":
            self._root.after(0, lambda: self.event_bus.emit_sync(EventType.CONNECTED))
        elif event == "disconnected":
            self._root.after(0, lambda: self.event_bus.emit_sync(EventType.DISCONNECTED))


def main():
    config = ConfigManager("config.json")

    if not config.validate():
        print("ERROR: Invalid configuration. Please check config.json has valid ha_token and ha_host/ha_port.")
        sys.exit(1)

    ws_client = HAWebSocketClient(
        ws_url=config.ha_ws_url,
        token=config.token,
        reconnect_interval=config.reconnect_interval,
        max_reconnect_attempts=config.max_reconnect_attempts,
    )
    rest_client = HARestClient(config.ha_rest_url, config.token)

    root = tk.Tk()
    device_manager = DeviceBridge(ws_client, rest_client, root)

    from gui.main_window import MainWindow
    app = MainWindow(root, device_manager)

    ws_client.connect()

    try:
        root.mainloop()
    finally:
        ws_client.disconnect()


if __name__ == "__main__":
    main()
