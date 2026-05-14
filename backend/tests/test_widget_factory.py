from __future__ import annotations

import tkinter as tk

import pytest

from ha_client.gui.widget_factory import WidgetFactory, WidgetSpec
from ha_client.models.entity import EntityDomain


@pytest.fixture(scope="module")
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    return
    root.destroy()


def _noop(*args, **kwargs):
    pass


class TestWidgetSpec:
    def test_creation(self):
        spec = WidgetSpec(
            widget_type="toggle",
            label="Turn On",
            initial_value=True,
            domain="light",
            entity_id="light.lamp",
        )
        assert spec.widget_type == "toggle"
        assert spec.label == "Turn On"
        assert spec.initial_value is True
        assert spec.domain == "light"
        assert spec.entity_id == "light.lamp"
        assert spec.on_change is None
        assert spec.config == {}


class TestDomainHasControls:
    def test_light_has_controls(self):
        assert WidgetFactory.domain_has_controls(EntityDomain.LIGHT) is True

    def test_switch_has_controls(self):
        assert WidgetFactory.domain_has_controls(EntityDomain.SWITCH) is True

    def test_sensor_has_no_controls(self):
        assert WidgetFactory.domain_has_controls(EntityDomain.SENSOR) is False

    def test_binary_sensor_has_no_controls(self):
        assert WidgetFactory.domain_has_controls(EntityDomain.BINARY_SENSOR) is False

    def test_cover_has_controls(self):
        assert WidgetFactory.domain_has_controls(EntityDomain.COVER) is True

    def test_climate_has_controls(self):
        assert WidgetFactory.domain_has_controls(EntityDomain.CLIMATE) is True

    def test_media_player_has_controls(self):
        assert WidgetFactory.domain_has_controls(EntityDomain.MEDIA_PLAYER) is True

    def test_fan_has_controls(self):
        assert WidgetFactory.domain_has_controls(EntityDomain.FAN) is True

    def test_lock_has_controls(self):
        assert WidgetFactory.domain_has_controls(EntityDomain.LOCK) is True

    def test_scene_has_controls(self):
        assert WidgetFactory.domain_has_controls(EntityDomain.SCENE) is True

    def test_unknown_has_no_controls_by_default(self):
        assert WidgetFactory.domain_has_controls(EntityDomain.UNKNOWN) is False

    def test_unknown_with_brightness_attribute_has_controls(self):
        assert WidgetFactory.domain_has_controls(
            EntityDomain.UNKNOWN, attributes={"brightness": 200}
        ) is True

    def test_string_domain_accepted(self):
        assert WidgetFactory.domain_has_controls("light") is True
        assert WidgetFactory.domain_has_controls("sensor") is False


class TestGetAvailableActions:
    def test_light_actions(self):
        actions = WidgetFactory.get_available_actions(
            EntityDomain.LIGHT,
            attributes={"brightness": 128},
            state="on",
            supported_features=set(),
            services={},
        )
        action_types = {a["type"] for a in actions}
        assert "slider" in action_types
        assert "button" in action_types

    def test_switch_actions(self):
        actions = WidgetFactory.get_available_actions(
            EntityDomain.SWITCH, state="on"
        )
        assert len(actions) >= 1
        assert any(a["param"] == "toggle" for a in actions)

    def test_sensor_has_no_actions(self):
        actions = WidgetFactory.get_available_actions(
            EntityDomain.SENSOR,
            attributes={"temperature": 22},
            state="22",
        )
        assert len(actions) == 0

    def test_cover_actions(self):
        actions = WidgetFactory.get_available_actions(
            EntityDomain.COVER, state="open"
        )
        assert len(actions) >= 3

    def test_climate_actions(self):
        actions = WidgetFactory.get_available_actions(
            EntityDomain.CLIMATE,
            attributes={"temperature": 22, "hvac_modes": ["heat", "cool", "auto"]},
            state="heat",
        )
        action_types = {a["type"] for a in actions}
        assert "slider" in action_types
        assert "selector" in action_types

    def test_media_player_actions(self):
        actions = WidgetFactory.get_available_actions(
            EntityDomain.MEDIA_PLAYER,
            attributes={"volume_level": 0.5},
            state="playing",
        )
        assert len(actions) >= 2

    def test_fan_actions_with_percentage(self):
        actions = WidgetFactory.get_available_actions(
            EntityDomain.FAN,
            attributes={"percentage": 50},
            state="on",
        )
        assert any(a["type"] == "slider" for a in actions)

    def test_fan_actions_with_speed_list(self):
        actions = WidgetFactory.get_available_actions(
            EntityDomain.FAN,
            attributes={"speed_list": ["low", "medium", "high"], "speed": "low"},
            state="on",
        )
        assert any(a["type"] == "selector" for a in actions)

    def test_lock_actions(self):
        actions = WidgetFactory.get_available_actions(
            EntityDomain.LOCK, state="locked"
        )
        assert len(actions) >= 1

    def test_scene_actions(self):
        actions = WidgetFactory.get_available_actions(
            EntityDomain.SCENE, state="scening"
        )
        assert len(actions) >= 1
        assert any(a["param"] == "trigger" for a in actions)

    def test_light_with_color_actions(self):
        actions = WidgetFactory.get_available_actions(
            EntityDomain.LIGHT,
            attributes={"brightness": 200, "rgb_color": [255, 128, 0]},
            state="on",
        )
        assert any(a["param"] == "pick_color" for a in actions)

    def test_light_with_color_temp_actions(self):
        actions = WidgetFactory.get_available_actions(
            EntityDomain.LIGHT,
            attributes={"color_temp": 370, "min_mireds": 153, "max_mireds": 500},
            state="on",
        )
        assert any(a["param"] == "set_color_temp" for a in actions)

    def test_nonstandard_services(self):
        services = {
            "vacuum": {
                "start": {"description": "Start cleaning"},
                "stop": {"description": "Stop cleaning"},
                "return_to_base": {"description": "Return to base"},
            }
        }
        actions = WidgetFactory.get_available_actions(
            EntityDomain.UNKNOWN,
            state="cleaning",
            services=services,
            entity_id="vacuum.roborock",
        )
        assert len(actions) >= 1


class TestBuildWidgets:
    def test_light_build(self, tk_root):
        widgets = WidgetFactory.build_widgets(
            tk_root,
            EntityDomain.LIGHT,
            "light.lamp",
            {"brightness": 200, "rgb_color": [255, 0, 0]},
            "on",
            set(),
            {},
            _noop,
        )
        assert len(widgets) >= 2

    def test_light_build_with_color_temp(self, tk_root):
        widgets = WidgetFactory.build_widgets(
            tk_root,
            EntityDomain.LIGHT,
            "light.lamp",
            {"color_temp": 370, "min_mireds": 153, "max_mireds": 500},
            "on",
            set(),
            {},
            _noop,
        )
        assert len(widgets) >= 2

    def test_switch_build(self, tk_root):
        widgets = WidgetFactory.build_widgets(
            tk_root,
            EntityDomain.SWITCH,
            "switch.outlet",
            {},
            "off",
            set(),
            {},
            _noop,
        )
        assert len(widgets) >= 1

    def test_cover_build(self, tk_root):
        widgets = WidgetFactory.build_widgets(
            tk_root,
            EntityDomain.COVER,
            "cover.garage",
            {},
            "open",
            set(),
            {},
            _noop,
        )
        assert len(widgets) >= 1

    def test_climate_build(self, tk_root):
        widgets = WidgetFactory.build_widgets(
            tk_root,
            EntityDomain.CLIMATE,
            "climate.living",
            {"temperature": 22.0, "min_temp": 7, "max_temp": 35, "hvac_modes": ["heat", "cool"]},
            "heat",
            set(),
            {},
            _noop,
        )
        assert len(widgets) >= 1

    def test_media_player_build(self, tk_root):
        widgets = WidgetFactory.build_widgets(
            tk_root,
            EntityDomain.MEDIA_PLAYER,
            "media_player.tv",
            {"volume_level": 0.5, "is_volume_muted": False},
            "playing",
            set(),
            {},
            _noop,
        )
        assert len(widgets) >= 1

    def test_fan_build(self, tk_root):
        widgets = WidgetFactory.build_widgets(
            tk_root,
            EntityDomain.FAN,
            "fan.ceiling",
            {"percentage": 75},
            "on",
            set(),
            {},
            _noop,
        )
        assert len(widgets) >= 2

    def test_lock_build(self, tk_root):
        widgets = WidgetFactory.build_widgets(
            tk_root,
            EntityDomain.LOCK,
            "lock.front_door",
            {},
            "locked",
            set(),
            {},
            _noop,
        )
        assert len(widgets) >= 1

    def test_scene_build(self, tk_root):
        widgets = WidgetFactory.build_widgets(
            tk_root,
            EntityDomain.SCENE,
            "scene.movie",
            {},
            "scening",
            set(),
            {},
            _noop,
        )
        assert len(widgets) >= 1

    def test_build_with_services_fallback(self, tk_root):
        services = {
            "vacuum": {
                "start": {},
                "stop": {},
            }
        }
        widgets = WidgetFactory.build_widgets(
            tk_root,
            EntityDomain.UNKNOWN,
            "vacuum.roborock",
            {},
            "cleaning",
            set(),
            services,
            _noop,
        )
        assert len(widgets) >= 1

    def test_string_domain_accepted(self, tk_root):
        widgets = WidgetFactory.build_widgets(
            tk_root,
            "light",
            "light.test",
            {},
            "on",
            set(),
            {},
            _noop,
        )
        assert len(widgets) >= 1

    def test_none_supported_features_handled(self, tk_root):
        widgets = WidgetFactory.build_widgets(
            tk_root,
            EntityDomain.LIGHT,
            "light.test",
            {},
            "on",
            None,
            {},
            _noop,
        )
        assert len(widgets) >= 1

    def test_none_available_services_handled(self, tk_root):
        widgets = WidgetFactory.build_widgets(
            tk_root,
            EntityDomain.LIGHT,
            "light.test",
            {},
            "on",
            set(),
            None,
            _noop,
        )
        assert len(widgets) >= 1


class TestToEntityDomain:
    def test_valid_string(self):
        assert WidgetFactory._to_entity_domain("light") == EntityDomain.LIGHT
        assert WidgetFactory._to_entity_domain("switch") == EntityDomain.SWITCH
        assert WidgetFactory._to_entity_domain("sensor") == EntityDomain.SENSOR

    def test_invalid_string(self):
        assert WidgetFactory._to_entity_domain("vacuum") == EntityDomain.UNKNOWN
