from __future__ import annotations

import tkinter as tk

import pytest

from ha_client.gui.state_renderer import (
    EXCLUDED_ATTRIBUTE_KEYS,
    StateRenderer,
)


@pytest.fixture(scope="module")
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


class TestGetDisplayAttributes:
    def test_excludes_internal_keys(self):
        attrs = {
            "friendly_name": "Desk Lamp",
            "entity_id": "light.desk_lamp",
            "supported_features": 3,
            "icon": "mdi:lamp",
            "attribution": "Some attribution",
            "brightness": 128,
        }
        result = StateRenderer.get_display_attributes(attrs)

        assert "entity_id" not in result
        assert "supported_features" not in result
        assert "icon" not in result
        assert "attribution" not in result
        assert "friendly_name" in result
        assert "brightness" in result

    def test_preserves_non_excluded_keys(self):
        attrs = {
            "brightness": 200,
            "color_temp": 370,
            "effect": "colorloop",
            "node_id": 1,
            "friendly_name": "Test Light",
        }
        result = StateRenderer.get_display_attributes(attrs)

        assert "brightness" in result
        assert "color_temp" in result
        assert "effect" in result
        assert "node_id" in result
        assert "friendly_name" in result

    def test_excluded_keys_set_is_correct(self):
        assert "entity_id" in EXCLUDED_ATTRIBUTE_KEYS
        assert "supported_features" in EXCLUDED_ATTRIBUTE_KEYS
        assert "icon" in EXCLUDED_ATTRIBUTE_KEYS
        assert "attribution" in EXCLUDED_ATTRIBUTE_KEYS


class TestRenderState:
    def test_renders_status_indicator_on(self, tk_root):
        widgets = StateRenderer.render_state(tk_root, "light.lamp", "on", {})

        assert len(widgets) >= 1
        status_frame = widgets[0]
        assert isinstance(status_frame, tk.Frame)
        children = status_frame.winfo_children()
        assert len(children) == 2
        assert isinstance(children[0], tk.Canvas)
        assert isinstance(children[1], tk.Label)

    def test_renders_status_indicator_off(self, tk_root):
        widgets = StateRenderer.render_state(tk_root, "light.lamp", "off", {})

        assert len(widgets) >= 1

    def test_renders_status_indicator_unavailable(self, tk_root):
        widgets = StateRenderer.render_state(tk_root, "sensor.temp", "unavailable", {})

        assert len(widgets) >= 1

    def test_skips_friendly_name_in_attributes(self, tk_root):
        attrs = {"friendly_name": "My Light", "brightness": 128}
        widgets = StateRenderer.render_state(tk_root, "light.lamp", "on", attrs)

        widget_count = len(widgets)
        assert widget_count >= 1

    def test_renders_bool_attribute(self, tk_root):
        attrs = {"is_on": True, "friendly_name": "Test"}
        widgets = StateRenderer.render_state(tk_root, "switch.test", "on", attrs)

        has_bool = any(
            isinstance(w, tk.Frame) and any(
                isinstance(c, tk.Canvas) for c in w.winfo_children()
            )
            for w in widgets[1:]
        )
        assert has_bool

    def test_renders_progress_bar_for_brightness(self, tk_root):
        attrs = {"brightness": 200, "friendly_name": "Desk Lamp"}
        widgets = StateRenderer.render_state(tk_root, "light.desk", "on", attrs)

        has_progress = any(
            isinstance(w, tk.Frame) and any(
                isinstance(c, tk.Canvas) and
                c.winfo_reqwidth() > 100
                for c in w.winfo_children()
            )
            for w in widgets[1:]
        )
        assert has_progress

    def test_renders_progress_bar_for_temperature(self, tk_root):
        attrs = {"temperature": 22.5, "friendly_name": "Thermostat"}
        widgets = StateRenderer.render_state(tk_root, "climate.living", "heat", attrs)

        assert len(widgets) >= 2

    def test_renders_progress_bar_for_humidity(self, tk_root):
        attrs = {"humidity": 65, "friendly_name": "Humidity Sensor"}
        widgets = StateRenderer.render_state(tk_root, "sensor.humidity", "65", attrs)

        assert len(widgets) >= 2

    def test_renders_color_block_for_rgb(self, tk_root):
        attrs = {"rgb_color": (255, 100, 50), "friendly_name": "RGB Light"}
        widgets = StateRenderer.render_state(tk_root, "light.rgb", "on", attrs)

        assert len(widgets) >= 2

    def test_renders_color_block_for_rgb_list(self, tk_root):
        attrs = {"rgb_color": [0, 128, 255], "friendly_name": "RGB Light"}
        widgets = StateRenderer.render_state(tk_root, "light.rgb", "on", attrs)

        assert len(widgets) >= 2

    def test_renders_time_ago_format(self, tk_root):
        attrs = {"last_changed": "2025-01-01T00:00:00+00:00", "friendly_name": "Sensor"}
        widgets = StateRenderer.render_state(tk_root, "sensor.old", "on", attrs)

        has_time = any(
            isinstance(w, tk.Frame) and any(
                isinstance(c, tk.Label) and "ago" in str(c.cget("text"))
                for c in w.winfo_children()
            )
            for w in widgets[1:]
        )
        assert has_time

    def test_renders_key_value_for_other_attributes(self, tk_root):
        attrs = {"node_id": 42, "friendly_name": "ZWave Device"}
        widgets = StateRenderer.render_state(tk_root, "switch.zwave", "on", attrs)

        assert len(widgets) >= 2

    def test_handles_empty_attributes(self, tk_root):
        widgets = StateRenderer.render_state(tk_root, "light.lamp", "on", {})

        assert len(widgets) == 1

    def test_handles_unknown_state(self, tk_root):
        widgets = StateRenderer.render_state(tk_root, "light.lamp", "unknown_state", {})

        assert len(widgets) == 1


class TestFormatTimeAgo:
    def test_seconds_ago(self):
        result = StateRenderer._format_time_ago("2026-05-14T00:00:00+00:00")
        assert "ago" in result

    def test_invalid_time_returns_raw(self):
        result = StateRenderer._format_time_ago("not-a-time")
        assert result == "not-a-time"

    def test_empty_string(self):
        result = StateRenderer._format_time_ago("")
        assert result == ""


class TestRenderAttribute:
    def test_bool_true(self, tk_root):
        widget = StateRenderer._render_attribute(tk_root, "enabled", True)
        assert widget is not None
        assert isinstance(widget, tk.Frame)

    def test_bool_false(self, tk_root):
        widget = StateRenderer._render_attribute(tk_root, "enabled", False)
        assert widget is not None
        assert isinstance(widget, tk.Frame)

    def test_int_progress_bar(self, tk_root):
        widget = StateRenderer._render_attribute(tk_root, "brightness", 200)
        assert widget is not None
        assert isinstance(widget, tk.Frame)

    def test_float_progress_bar(self, tk_root):
        widget = StateRenderer._render_attribute(tk_root, "temperature", 22.5)
        assert widget is not None
        assert isinstance(widget, tk.Frame)

    def test_rgb_tuple_color_block(self, tk_root):
        widget = StateRenderer._render_attribute(tk_root, "rgb_color", (100, 200, 50))
        assert widget is not None
        assert isinstance(widget, tk.Frame)

    def test_rgb_list_color_block(self, tk_root):
        widget = StateRenderer._render_attribute(tk_root, "rgb_color", [255, 0, 0])
        assert widget is not None
        assert isinstance(widget, tk.Frame)

    def test_non_rgb_tuple_renders_as_key_value(self, tk_root):
        widget = StateRenderer._render_attribute(tk_root, "some_list", [1, 2, 3])
        assert widget is not None
        assert isinstance(widget, tk.Frame)

    def test_time_attribute(self, tk_root):
        widget = StateRenderer._render_attribute(tk_root, "last_changed", "2026-01-01T00:00:00+00:00")
        assert widget is not None
        assert isinstance(widget, tk.Frame)

    def test_unit_attribute(self, tk_root):
        widget = StateRenderer._render_attribute(tk_root, "unit_of_measurement", "\u00b0C")
        assert widget is not None
        assert isinstance(widget, tk.Frame)

    def test_generic_key_value(self, tk_root):
        widget = StateRenderer._render_attribute(tk_root, "some_key", "some_value")
        assert widget is not None
        assert isinstance(widget, tk.Frame)
