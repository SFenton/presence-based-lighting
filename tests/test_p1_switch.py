"""Presence allowed switch behavior tests."""

import asyncio

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from tests.conftest import (
    assert_service_called,
    assert_service_not_called,
    setup_entity_states,
    setup_multi_entity_states,
)


def _presence_event(mock_hass, old_state, new_state, entity_id="binary_sensor.living_room_motion"):
    mock_hass.states.set(entity_id, new_state)
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": type("State", (), {"state": old_state, "context": None})(),
                "new_state": type("State", (), {"state": new_state, "context": None})(),
            }
        },
    )()


class TestPresenceSwitchBehavior:
    entity = "light.living_room"

    @pytest.mark.asyncio
    async def test_disabling_presence_prevents_turn_on(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator.async_set_presence_allowed(self.entity, False)
        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_OFF, STATE_ON)
        )
        assert_service_not_called(mock_hass, "light", "turn_on")

    @pytest.mark.asyncio
    async def test_enabling_presence_while_occupied_turns_on(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator.async_set_presence_allowed(self.entity, False)
        mock_hass.services.clear()

        await coordinator.async_set_presence_allowed(self.entity, True)
        assert_service_called(mock_hass, "light", "turn_on", self.entity)

    @pytest.mark.asyncio
    async def test_enabling_presence_when_empty_does_not_turn_on(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator.async_set_presence_allowed(self.entity, False)
        mock_hass.services.clear()

        await coordinator.async_set_presence_allowed(self.entity, True)
        assert_service_not_called(mock_hass, "light", "turn_on")

    @pytest.mark.asyncio
    async def test_reenabling_after_manual_off_respects_timer(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator.async_set_presence_allowed(self.entity, False)
        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_ON, STATE_OFF)
        )

        await asyncio.sleep(1.1)
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_presence_toggle_does_not_affect_other_entities(self, mock_hass, mock_config_entry_multi):
        setup_multi_entity_states(
            mock_hass,
            lights_states=[STATE_ON, STATE_ON],
            occupancy_states=[STATE_ON, STATE_ON],
        )
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_multi)
        await coordinator.async_start()

        await coordinator.async_set_presence_allowed("light.living_room_1", False)
        mock_hass.states.set("binary_sensor.motion_2", STATE_OFF)
        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_ON, STATE_OFF, entity_id="binary_sensor.motion_1")
        )
        await asyncio.sleep(1.1)

        assert_service_called(mock_hass, "light", "turn_off", "light.living_room_2")

        for call in mock_hass.services.calls:
            if call["domain"] == "light" and call["service"] == "turn_off":
                targets = call["service_data"].get("entity_id")
                if isinstance(targets, str):
                    targets = [targets]
                assert "light.living_room_1" not in targets
