"""Tests for real_last_changed.py uncovered helpers."""

import pytest
from unittest.mock import MagicMock

from custom_components.presence_based_lighting.real_last_changed import (
    ATTR_PREVIOUS_VALID_STATE,
    is_rlc_integration_available,
    get_rlc_sensors_for_entity,
    get_all_rlc_sensors,
)


def _make_state(entity_id, state="2024-01-01T12:00:00", attrs=None):
    s = MagicMock()
    s.entity_id = entity_id
    s.state = state
    s.attributes = attrs or {}
    return s


class TestIsRlcIntegrationAvailable:
    def test_returns_true_when_rlc_sensor_exists(self):
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_state("sensor.motion_rlc", attrs={ATTR_PREVIOUS_VALID_STATE: "on"}),
        ]
        assert is_rlc_integration_available(hass) is True

    def test_returns_false_when_no_rlc_sensors(self):
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_state("sensor.temperature", attrs={"unit": "C"}),
        ]
        assert is_rlc_integration_available(hass) is False

    def test_returns_false_when_non_sensor(self):
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_state("binary_sensor.motion", attrs={ATTR_PREVIOUS_VALID_STATE: "on"}),
        ]
        assert is_rlc_integration_available(hass) is False


class TestGetRlcSensorsForEntity:
    def test_match_by_entity_id_attribute(self):
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_state("sensor.lamp_rlc", attrs={
                ATTR_PREVIOUS_VALID_STATE: "on",
                "entity_id": "light.desk_lamp",
            }),
        ]
        result = get_rlc_sensors_for_entity(hass, "light.desk_lamp")
        assert "sensor.lamp_rlc" in result

    def test_match_by_name_contains(self):
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_state("sensor.desk_lamp_real_last_changed", attrs={
                ATTR_PREVIOUS_VALID_STATE: "off",
            }),
        ]
        result = get_rlc_sensors_for_entity(hass, "light.desk_lamp")
        assert "sensor.desk_lamp_real_last_changed" in result

    def test_no_match(self):
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_state("sensor.other_rlc", attrs={
                ATTR_PREVIOUS_VALID_STATE: "on",
                "entity_id": "light.kitchen",
            }),
        ]
        result = get_rlc_sensors_for_entity(hass, "light.desk_lamp")
        assert result == []

    def test_skips_non_sensor_entities(self):
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_state("light.desk_lamp", attrs={ATTR_PREVIOUS_VALID_STATE: "on"}),
        ]
        result = get_rlc_sensors_for_entity(hass, "light.desk_lamp")
        assert result == []

    def test_skips_sensors_without_rlc_attr(self):
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_state("sensor.desk_lamp_power", attrs={"unit": "W"}),
        ]
        result = get_rlc_sensors_for_entity(hass, "light.desk_lamp")
        assert result == []


class TestGetAllRlcSensors:
    def test_returns_all_rlc_sensors(self):
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_state("sensor.rlc_1", attrs={ATTR_PREVIOUS_VALID_STATE: "on"}),
            _make_state("sensor.rlc_2", attrs={ATTR_PREVIOUS_VALID_STATE: "off"}),
            _make_state("sensor.temperature", attrs={"unit": "C"}),
            _make_state("binary_sensor.motion", attrs={ATTR_PREVIOUS_VALID_STATE: "on"}),
        ]
        result = get_all_rlc_sensors(hass)
        assert result == ["sensor.rlc_1", "sensor.rlc_2"]

    def test_returns_empty_when_no_rlc(self):
        hass = MagicMock()
        hass.states.async_all.return_value = []
        assert get_all_rlc_sensors(hass) == []
