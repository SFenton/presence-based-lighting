"""Critical tests for multi-entity Presence Based Lighting automation."""

import asyncio

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
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
