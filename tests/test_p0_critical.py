"""Critical tests for multi-entity Presence Based Lighting automation."""

import asyncio

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.const import (
    CONF_ENTITY_ID,
    CONF_PRESENCE_CLEARED_TRANSITION,
    CONF_PRESENCE_DETECTED_BRIGHTNESS_PCT,
    CONF_PRESENCE_DETECTED_TRANSITION,
    DEFAULT_PRESENCE_DETECTED_BRIGHTNESS_PCT,
    DEFAULT_PRESENCE_CLEARED_TRANSITION,
    DEFAULT_PRESENCE_DETECTED_TRANSITION,
)
from tests.conftest import assert_service_called, setup_entity_states


def _state(state, attributes=None):
    return type("State", (), {"state": state, "attributes": attributes or {}, "context": type("Ctx", (), {"id": "ctx", "parent_id": None})()})()


def _event(mock_hass, entity_id, old_state, new_state, old_attrs=None, new_attrs=None):
    mock_hass.states.set(entity_id, new_state)
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": _state(old_state, old_attrs),
                "new_state": _state(new_state, new_attrs),
            }
        },
    )()


class TestPresenceAutomation:
    """Core behavior validation for presence-driven automation."""

    @pytest.mark.asyncio
    async def test_presence_detected_turns_on_allowed_entities(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF, STATE_ON)
        )

        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")
        call = next(
            call
            for call in mock_hass.services.calls
            if call["domain"] == "light" and call["service"] == "turn_on"
        )
        assert (
            call["service_data"]["brightness_pct"]
            == DEFAULT_PRESENCE_DETECTED_BRIGHTNESS_PCT
        )
        assert call["service_data"]["transition"] == DEFAULT_PRESENCE_DETECTED_TRANSITION

    @pytest.mark.asyncio
    async def test_presence_detected_uses_configured_light_transition(
        self,
        mock_hass,
        mock_config_entry,
    ):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        mock_config_entry.data["controlled_entities"][0][
            CONF_PRESENCE_DETECTED_TRANSITION
        ] = 2.5
        mock_config_entry.data["controlled_entities"][0][
            CONF_PRESENCE_DETECTED_BRIGHTNESS_PCT
        ] = 75
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF, STATE_ON)
        )

        call = next(
            call
            for call in mock_hass.services.calls
            if call["domain"] == "light" and call["service"] == "turn_on"
        )
        assert call["service_data"] == {
            "entity_id": "light.living_room",
            "brightness_pct": 75,
            "transition": 2.5,
        }

    @pytest.mark.asyncio
    async def test_presence_detected_does_not_send_transition_to_non_light(
        self,
        mock_hass,
        mock_config_entry,
    ):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        mock_config_entry.data["controlled_entities"][0][CONF_ENTITY_ID] = "switch.living_room"
        mock_hass.states.set("switch.living_room", STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF, STATE_ON)
        )

        call = next(
            call
            for call in mock_hass.services.calls
            if call["domain"] == "switch" and call["service"] == "turn_on"
        )
        assert call["service_data"] == {"entity_id": "switch.living_room"}

    @pytest.mark.asyncio
    async def test_presence_cleared_turns_off_after_delay(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.living_room_motion", STATE_ON, STATE_OFF)
        )

        await asyncio.sleep(1.1)
        assert_service_called(mock_hass, "light", "turn_off", "light.living_room")
        call = next(
            call
            for call in mock_hass.services.calls
            if call["domain"] == "light" and call["service"] == "turn_off"
        )
        assert call["service_data"] == {
            "entity_id": "light.living_room",
            "transition": DEFAULT_PRESENCE_CLEARED_TRANSITION,
        }

    @pytest.mark.asyncio
    async def test_zero_clear_transition_omits_transition(
        self,
        mock_hass,
        mock_config_entry,
    ):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        mock_config_entry.data["controlled_entities"][0][
            CONF_PRESENCE_CLEARED_TRANSITION
        ] = 0
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.living_room_motion", STATE_ON, STATE_OFF)
        )

        await asyncio.sleep(1.1)
        call = next(
            call
            for call in mock_hass.services.calls
            if call["domain"] == "light" and call["service"] == "turn_off"
        )
        assert call["service_data"] == {"entity_id": "light.living_room"}

    def test_confirmation_delay_includes_transition_grace(
        self,
        mock_hass,
        mock_config_entry,
    ):
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        assert coordinator._actuation_confirmation_delay({"transition": 5}) == 6
        assert coordinator._actuation_confirmation_delay({}) == 2
