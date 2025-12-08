"""P1 High Priority Tests - Basic occupancy detection scenarios."""

import asyncio

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from tests.conftest import assert_service_called, assert_service_not_called, setup_entity_states


def _presence_event(mock_hass, old_state, new_state):
    mock_hass.states.set("binary_sensor.living_room_motion", new_state)
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": "binary_sensor.living_room_motion",
                "old_state": type("State", (), {"state": old_state, "attributes": {}, "context": None})(),
                "new_state": type("State", (), {"state": new_state, "attributes": {}, "context": None})(),
            }
        },
    )()


class TestBasicOccupancyDetection:
    @pytest.mark.asyncio
    async def test_no_occupancy_lights_stay_off(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await asyncio.sleep(0.1)
        assert_service_not_called(mock_hass, "light", "turn_on")

    @pytest.mark.asyncio
    async def test_occupied_room_no_extra_actions(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await asyncio.sleep(0.1)
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_clear_then_reoccupy_stays_on(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_ON, STATE_OFF)
        )
        await asyncio.sleep(0.5)
        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_OFF, STATE_ON)
        )

        await asyncio.sleep(0.7)
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_clear_then_timer_turns_off(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_ON, STATE_OFF)
        )
        await asyncio.sleep(1.1)
        assert_service_called(mock_hass, "light", "turn_off", "light.living_room")

    @pytest.mark.asyncio
    async def test_presence_disabled_blocks_actions(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator.async_set_presence_allowed("light.living_room", False)
        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_OFF, STATE_ON)
        )

        assert_service_not_called(mock_hass, "light", "turn_on")

    @pytest.mark.asyncio
    async def test_presence_disabled_ignores_cleared_timer(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator.async_set_presence_allowed("light.living_room", False)
        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_ON, STATE_OFF)
        )
        await asyncio.sleep(1.1)

        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_presence_disabled_stays_idle(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator.async_set_presence_allowed("light.living_room", False)
        await asyncio.sleep(1)

        assert_service_not_called(mock_hass, "light", "turn_off")
