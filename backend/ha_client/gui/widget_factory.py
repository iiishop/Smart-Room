from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk
from typing import Any, Callable

from ha_client.models.entity import EntityDomain


@dataclass
class WidgetSpec:
    widget_type: str
    label: str
    initial_value: Any
    domain: str
    entity_id: str
    on_change: Callable | None = None
    config: dict = field(default_factory=dict)


_LIGHT_SUPPORT_BRIGHTNESS = 1
_LIGHT_SUPPORT_COLOR_TEMP = 2
_LIGHT_SUPPORT_EFFECT = 4
_LIGHT_SUPPORT_FLASH = 8
_LIGHT_SUPPORT_COLOR = 16
_LIGHT_SUPPORT_TRANSITION = 32
_LIGHT_SUPPORT_WHITE_VALUE = 128

_COVER_SUPPORT_OPEN = 1
_COVER_SUPPORT_CLOSE = 2
_COVER_SUPPORT_STOP = 4
_COVER_SUPPORT_SET_POSITION = 8
_COVER_SUPPORT_SET_TILT = 16

_CLIMATE_SUPPORT_TARGET_TEMPERATURE = 1
_CLIMATE_SUPPORT_TARGET_HUMIDITY = 4
_CLIMATE_SUPPORT_FAN_MODE = 8
_CLIMATE_SUPPORT_SWING_MODE = 128

_MEDIA_SUPPORT_PAUSE = 1
_MEDIA_SUPPORT_VOLUME_SET = 4
_MEDIA_SUPPORT_VOLUME_MUTE = 8
_MEDIA_SUPPORT_PLAY = 16384
_MEDIA_SUPPORT_NEXT_TRACK = 4096
_MEDIA_SUPPORT_PREVIOUS_TRACK = 2048

_FAN_SUPPORT_SET_SPEED = 1
_FAN_SUPPORT_OSCILLATE = 2
_FAN_SUPPORT_DIRECTION = 4
_FAN_SUPPORT_PRESET_MODE = 8


class WidgetFactory:
    @staticmethod
    def domain_has_controls(
        domain: EntityDomain | str,
        attributes: dict | None = None,
        state: str = "",
    ) -> bool:
        if isinstance(domain, str):
            domain = WidgetFactory._to_entity_domain(domain)

        controllable = {
            EntityDomain.LIGHT,
            EntityDomain.SWITCH,
            EntityDomain.COVER,
            EntityDomain.CLIMATE,
            EntityDomain.MEDIA_PLAYER,
            EntityDomain.FAN,
            EntityDomain.LOCK,
            EntityDomain.SCENE,
            EntityDomain.SCRIPT,
            EntityDomain.AUTOMATION,
        }
        if domain in controllable:
            return True

        if attributes:
            if any(k in attributes for k in ("brightness", "color_temp", "rgb_color", "hs_color",
                                               "supported_features", "percentage", "speed_list")):
                return True

        return False

    @staticmethod
    def get_available_actions(
        domain: EntityDomain | str,
        attributes: dict | None = None,
        state: str = "",
        supported_features: set[int] | None = None,
        services: dict[str, dict] | None = None,
        entity_id: str = "",
    ) -> list[dict]:
        raw_domain = domain if isinstance(domain, str) else domain.value
        if isinstance(domain, str):
            domain = WidgetFactory._to_entity_domain(domain)
        elif domain == EntityDomain.UNKNOWN:
            raw_domain = entity_id.split(".")[0] if "." in entity_id else raw_domain

        attributes = attributes or {}
        supported_features = supported_features or set()
        services = services or {}

        actions: list[dict] = []

        actions.extend(WidgetFactory._infer_actions_from_attributes(
            domain, attributes, state, supported_features
        ))
        actions.extend(WidgetFactory._infer_actions_from_services(
            raw_domain, services
        ))

        seen = set()
        unique: list[dict] = []
        for a in actions:
            key = (a["type"], a.get("param", ""))
            if key not in seen:
                seen.add(key)
                unique.append(a)
        return unique

    @staticmethod
    def build_widgets(
        parent: tk.Widget,
        domain: EntityDomain | str,
        entity_id: str,
        attributes: dict,
        state: str,
        supported_features: set[int] | None,
        available_services: dict[str, dict] | None,
        on_change: Callable[[str, str, Any], None],
    ) -> list[tk.Widget]:
        raw_domain = domain if isinstance(domain, str) else domain.value
        if isinstance(domain, str):
            domain = WidgetFactory._to_entity_domain(domain)
        elif domain == EntityDomain.UNKNOWN:
            raw_domain = entity_id.split(".")[0] if "." in entity_id else entity_id

        supported_features = supported_features or set()
        available_services = available_services or {}

        domain_services = available_services.get(raw_domain, {})

        widgets: list[tk.Widget] = []

        if domain in (EntityDomain.LIGHT, EntityDomain.SWITCH):
            btn_frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")
            is_on = state == "on"

            on_btn = ttk.Button(btn_frame, text="ON",
                                command=lambda e=entity_id: on_change(e, "turn_on", {}))
            off_btn = ttk.Button(btn_frame, text="OFF",
                                 command=lambda e=entity_id: on_change(e, "turn_off", {}))
            toggle_btn = ttk.Button(btn_frame, text="Toggle",
                                    command=lambda e=entity_id: on_change(e, "toggle", {}))

            on_btn.pack(side=tk.LEFT, padx=2)
            off_btn.pack(side=tk.LEFT, padx=2)
            toggle_btn.pack(side=tk.LEFT, padx=2)
            widgets.append(btn_frame)

        if "brightness" in attributes:
            val = int(attributes["brightness"])
            frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")
            tk.Label(frame, text="Brightness", font=("Segoe UI", 9), fg="#555555",
                     bg=frame.cget("bg")).pack(anchor=tk.W)
            scale = ttk.Scale(frame, from_=0, to=255, orient=tk.HORIZONTAL, value=val)
            scale.pack(fill=tk.X)
            val_label = tk.Label(frame, text=str(val), font=("Segoe UI", 8), fg="#888888",
                                 bg=frame.cget("bg"))
            val_label.pack(anchor=tk.E)

            def _on_brightness(val_str, e=entity_id, s=scale, l=val_label):
                v = int(round(float(val_str)))
                l.config(text=str(v))
                on_change(e, "set_brightness", {"brightness": v})

            scale.config(command=lambda v, s=scale, l=val_label: _on_brightness(v, entity_id, s, l))
            widgets.append(frame)

        if "color_temp" in attributes:
            val = int(attributes.get("color_temp", 370))
            min_mireds = int(attributes.get("min_mireds", 153))
            max_mireds = int(attributes.get("max_mireds", 500))
            frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")
            tk.Label(frame, text="Color Temp", font=("Segoe UI", 9), fg="#555555",
                     bg=frame.cget("bg")).pack(anchor=tk.W)
            scale = ttk.Scale(frame, from_=min_mireds, to=max_mireds, orient=tk.HORIZONTAL, value=val)
            scale.pack(fill=tk.X)
            val_label = tk.Label(frame, text=f"{val} mired", font=("Segoe UI", 8), fg="#888888",
                                 bg=frame.cget("bg"))
            val_label.pack(anchor=tk.E)

            def _on_color_temp(val_str, e=entity_id, s=scale, l=val_label):
                v = int(round(float(val_str)))
                l.config(text=f"{v} mired")
                on_change(e, "set_color_temp", {"color_temp": v})

            scale.config(
                command=lambda v, s=scale, l=val_label: _on_color_temp(v, entity_id, s, l))
            widgets.append(frame)

        if "rgb_color" in attributes or "hs_color" in attributes:
            frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")
            btn = ttk.Button(frame, text="Pick Color",
                             command=lambda e=entity_id: on_change(e, "pick_color", {}))
            btn.pack(side=tk.LEFT, padx=2)
            if "rgb_color" in attributes:
                rgb = attributes["rgb_color"]
                if isinstance(rgb, (list, tuple)) and len(rgb) == 3:
                    hex_c = "#{:02x}{:02x}{:02x}".format(int(rgb[0]), int(rgb[1]), int(rgb[2]))
                    color_block = tk.Canvas(frame, width=20, height=20, highlightthickness=0,
                                            bg=frame.cget("bg"))
                    color_block.create_rectangle(2, 2, 18, 18, fill=hex_c, outline="#ccc")
                    color_block.pack(side=tk.LEFT, padx=4)
            widgets.append(frame)

        if domain == EntityDomain.COVER:
            btn_frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")

            def _cover_action(a, e=entity_id):
                on_change(e, f"cover_{a}", {})

            ttk.Button(btn_frame, text="Open", command=lambda a="open": _cover_action(a)).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_frame, text="Stop", command=lambda a="stop": _cover_action(a)).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_frame, text="Close", command=lambda a="close": _cover_action(a)).pack(side=tk.LEFT, padx=2)
            widgets.append(btn_frame)

        if domain == EntityDomain.CLIMATE:
            if attributes.get("temperature") is not None:
                temp = float(attributes["temperature"])
                min_temp = float(attributes.get("min_temp", 7))
                max_temp = float(attributes.get("max_temp", 35))
                frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")
                tk.Label(frame, text="Temperature", font=("Segoe UI", 9), fg="#555555",
                         bg=frame.cget("bg")).pack(anchor=tk.W)
                scale = ttk.Scale(frame, from_=min_temp, to=max_temp, orient=tk.HORIZONTAL, value=temp)
                scale.pack(fill=tk.X)
                val_label = tk.Label(frame, text=f"{temp}\u00b0C", font=("Segoe UI", 8), fg="#888888",
                                     bg=frame.cget("bg"))
                val_label.pack(anchor=tk.E)

                def _on_temp(val_str, e=entity_id, l=val_label):
                    v = round(float(val_str), 1)
                    l.config(text=f"{v}\u00b0C")
                    on_change(e, "set_temperature", {"temperature": v})

                scale.config(command=lambda v, l=val_label: _on_temp(v, entity_id, l))
                widgets.append(frame)

            hvac_modes = attributes.get("hvac_modes", [])
            if hvac_modes:
                frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")
                tk.Label(frame, text="Mode", font=("Segoe UI", 9), fg="#555555",
                         bg=frame.cget("bg")).pack(side=tk.LEFT, padx=(0, 6))
                current_mode = attributes.get("hvac_action", hvac_modes[0])
                mode_var = tk.StringVar(value=current_mode)
                combo = ttk.Combobox(frame, textvariable=mode_var, values=hvac_modes, state="readonly", width=12)
                combo.pack(side=tk.LEFT)

                def _on_mode(e=entity_id, mv=mode_var):
                    on_change(e, "set_hvac_mode", {"hvac_mode": mv.get()})

                combo.bind("<<ComboboxSelected>>", lambda e: _on_mode())
                widgets.append(frame)

        if domain == EntityDomain.MEDIA_PLAYER:
            btn_frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")

            def _media_action(a, e=entity_id):
                on_change(e, f"media_{a}", {})

            if state == "playing":
                ttk.Button(btn_frame, text="Pause", command=lambda a="pause": _media_action(a)).pack(side=tk.LEFT, padx=2)
            else:
                ttk.Button(btn_frame, text="Play", command=lambda a="play": _media_action(a)).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_frame, text="Next", command=lambda a="next": _media_action(a)).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_frame, text="Prev", command=lambda a="previous": _media_action(a)).pack(side=tk.LEFT, padx=2)
            widgets.append(btn_frame)

            if "volume_level" in attributes:
                vol = float(attributes.get("volume_level", 0))
                is_muted = attributes.get("is_volume_muted", False)
                frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")
                tk.Label(frame, text="Volume", font=("Segoe UI", 9), fg="#555555",
                         bg=frame.cget("bg")).pack(anchor=tk.W)
                scale = ttk.Scale(frame, from_=0, to=1.0, orient=tk.HORIZONTAL, value=vol)
                scale.pack(fill=tk.X)
                val_label = tk.Label(frame, text=f"{int(vol * 100)}%", font=("Segoe UI", 8), fg="#888888",
                                     bg=frame.cget("bg"))
                val_label.pack(anchor=tk.E)

                def _on_vol(val_str, e=entity_id, l=val_label):
                    v = round(float(val_str), 2)
                    l.config(text=f"{int(v * 100)}%")
                    on_change(e, "set_volume", {"volume_level": v})

                scale.config(command=lambda v, l=val_label: _on_vol(v, entity_id, l))
                widgets.append(frame)

        if domain == EntityDomain.FAN:
            btn_frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")

            def _fan_action(a, e=entity_id):
                on_change(e, f"fan_{a}", {})

            if state == "on":
                ttk.Button(btn_frame, text="Turn Off", command=lambda a="off": _fan_action(a)).pack(side=tk.LEFT, padx=2)
            else:
                ttk.Button(btn_frame, text="Turn On", command=lambda a="on": _fan_action(a)).pack(side=tk.LEFT, padx=2)

            percentage = attributes.get("percentage")
            speed_list = attributes.get("speed_list")
            if percentage is not None:
                frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")
                tk.Label(frame, text="Speed", font=("Segoe UI", 9), fg="#555555",
                         bg=frame.cget("bg")).pack(anchor=tk.W)
                scale = ttk.Scale(frame, from_=0, to=100, orient=tk.HORIZONTAL, value=percentage)
                scale.pack(fill=tk.X)
                val_label = tk.Label(frame, text=f"{int(percentage)}%", font=("Segoe UI", 8), fg="#888888",
                                     bg=frame.cget("bg"))
                val_label.pack(anchor=tk.E)

                def _on_speed(val_str, e=entity_id, l=val_label):
                    v = int(round(float(val_str)))
                    l.config(text=f"{v}%")
                    on_change(e, "set_percentage", {"percentage": v})

                scale.config(command=lambda v, l=val_label: _on_speed(v, entity_id, l))
                widgets.append(frame)
            elif speed_list:
                frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")
                tk.Label(frame, text="Speed", font=("Segoe UI", 9), fg="#555555",
                         bg=frame.cget("bg")).pack(side=tk.LEFT, padx=(0, 6))
                current_speed = attributes.get("speed", speed_list[0])
                speed_var = tk.StringVar(value=current_speed if current_speed else speed_list[0])
                combo = ttk.Combobox(frame, textvariable=speed_var, values=list(speed_list), state="readonly", width=12)
                combo.pack(side=tk.LEFT)

                def _on_fan_speed(e=entity_id, sv=speed_var):
                    on_change(e, "set_speed", {"speed": sv.get()})

                combo.bind("<<ComboboxSelected>>", lambda e: _on_fan_speed())
                widgets.append(frame)

            widgets.append(btn_frame)

        if domain == EntityDomain.LOCK:
            btn_frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")

            def _lock_action(a, e=entity_id):
                on_change(e, f"lock_{a}", {})

            if state == "locked":
                ttk.Button(btn_frame, text="Unlock", command=lambda a="unlock": _lock_action(a)).pack(side=tk.LEFT, padx=2)
            else:
                ttk.Button(btn_frame, text="Lock", command=lambda a="lock": _lock_action(a)).pack(side=tk.LEFT, padx=2)
            widgets.append(btn_frame)

        if domain in (EntityDomain.SCENE, EntityDomain.SCRIPT, EntityDomain.AUTOMATION):

            def _trigger_action(e=entity_id):
                on_change(e, "trigger", {})

            btn = ttk.Button(parent, text="Activate",
                             command=_trigger_action)
            widgets.append(btn)

        for service_name in domain_services:
            if service_name in ("turn_on", "turn_off", "toggle", "reload", "set",
                                "open_cover", "close_cover", "stop_cover",
                                "lock", "unlock",
                                "volume_up", "volume_down", "volume_mute",
                                "media_play", "media_pause", "media_stop",
                                "media_next_track", "media_previous_track"):
                continue

            display_name = service_name.replace("_", " ").title()

            def _svc_action(s=service_name, e=entity_id):
                on_change(e, s, {})

            btn = ttk.Button(parent, text=display_name, command=_svc_action)
            widgets.append(btn)

        return widgets

    @staticmethod
    def _infer_actions_from_attributes(
        domain: EntityDomain,
        attributes: dict,
        state: str,
        supported_features: set[int],
    ) -> list[dict]:
        actions: list[dict] = []

        if "brightness" in attributes:
            actions.append({"type": "slider", "label": "Brightness", "param": "set_brightness",
                            "config": {"min": 0, "max": 255}})
        if "color_temp" in attributes:
            min_m = int(attributes.get("min_mireds", 153))
            max_m = int(attributes.get("max_mireds", 500))
            actions.append({"type": "slider", "label": "Color Temp", "param": "set_color_temp",
                            "config": {"min": min_m, "max": max_m}})
        if "rgb_color" in attributes or "hs_color" in attributes:
            actions.append({"type": "button", "label": "Pick Color", "param": "pick_color", "config": {}})

        if domain in (EntityDomain.LIGHT, EntityDomain.SWITCH):
            actions.append({"type": "button", "label": "Toggle", "param": "toggle", "config": {}})

        if domain == EntityDomain.COVER:
            actions.append({"type": "button", "label": "Open", "param": "cover_open", "config": {}})
            actions.append({"type": "button", "label": "Stop", "param": "cover_stop", "config": {}})
            actions.append({"type": "button", "label": "Close", "param": "cover_close", "config": {}})

        if domain == EntityDomain.CLIMATE:
            if attributes.get("temperature") is not None:
                min_t = float(attributes.get("min_temp", 7))
                max_t = float(attributes.get("max_temp", 35))
                actions.append({"type": "slider", "label": "Temperature", "param": "set_temperature",
                                "config": {"min": min_t, "max": max_t}})
            if attributes.get("hvac_modes"):
                modes = attributes["hvac_modes"]
                actions.append({"type": "selector", "label": "HVAC Mode", "param": "set_hvac_mode",
                                "config": {"options": modes}})

        if domain == EntityDomain.MEDIA_PLAYER:
            actions.append({"type": "button", "label": "Play/Pause", "param": "media_play_pause", "config": {}})
            actions.append({"type": "button", "label": "Next", "param": "media_next", "config": {}})
            if "volume_level" in attributes:
                actions.append({"type": "slider", "label": "Volume", "param": "set_volume",
                                "config": {"min": 0, "max": 1.0}})

        if domain == EntityDomain.FAN:
            if "percentage" in attributes:
                actions.append({"type": "slider", "label": "Speed", "param": "set_percentage",
                                "config": {"min": 0, "max": 100}})
            elif "speed_list" in attributes:
                actions.append({"type": "selector", "label": "Speed", "param": "set_speed",
                                "config": {"options": list(attributes["speed_list"])}})

        if domain == EntityDomain.LOCK:
            actions.append({"type": "button", "label": "Lock/Unlock", "param": "lock_toggle", "config": {}})

        if domain in (EntityDomain.SCENE, EntityDomain.SCRIPT, EntityDomain.AUTOMATION):
            actions.append({"type": "button", "label": "Activate", "param": "trigger", "config": {}})

        return actions

    @staticmethod
    def _infer_actions_from_services(
        domain_str: str,
        services: dict[str, dict],
    ) -> list[dict]:
        actions: list[dict] = []
        known = {
            "turn_on", "turn_off", "toggle", "reload", "set",
            "open_cover", "close_cover", "stop_cover",
            "lock", "unlock",
            "volume_up", "volume_down", "volume_mute",
            "media_play", "media_pause", "media_stop",
            "media_next_track", "media_previous_track",
        }

        domain_services = services.get(domain_str, {})
        for svc_name in domain_services:
            if svc_name in known:
                continue
            actions.append({
                "type": "button",
                "label": svc_name.replace("_", " ").title(),
                "param": svc_name,
                "config": {},
            })

        return actions

    @staticmethod
    def _to_entity_domain(domain: str) -> EntityDomain:
        try:
            return EntityDomain(domain)
        except ValueError:
            return EntityDomain.UNKNOWN
