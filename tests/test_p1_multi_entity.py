"""Multi-entity regression tests for Presence Based Lighting."""

import asyncio

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from tests.conftest import (
    assert_service_called,
    setup_multi_entity_states,
)


def _event(mock_hass, entity_id, old_state, new_state, context=None):
    mock_hass.states.set(entity_id, new_state)
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": type(
                    "State", (), {"state": old_state, "context": context}
                )()
                if old_state is not None
                else None,
                "new_state": type(
                    "State", (), {"state": new_state, "context": context}
                )()
                if new_state is not None
                else None,
            }
        },
    )()


class TestMultiEntity:
    @pytest.mark.asyncio
    async def test_all_entities_follow_presence(self, mock_hass, mock_config_entry_multi):
        setup_multi_entity_states(
            mock_hass,
            lights_states=[STATE_OFF, STATE_OFF],
            occupancy_states=[STATE_OFF, STATE_OFF],
        )
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_multi)
        await coordinator.async_start()

        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.motion_1", STATE_OFF, STATE_ON)
        )
        for entity in mock_config_entry_multi.data["controlled_entities"]:
            assert_service_called(mock_hass, "light", "turn_on", entity["entity_id"])

        mock_hass.services.clear()
        mock_hass.states.set("light.living_room_1", STATE_ON)
        mock_hass.states.set("light.living_room_2", STATE_ON)
        mock_hass.states.set("binary_sensor.motion_2", STATE_OFF)
        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.motion_1", STATE_ON, STATE_OFF)
        )
        await asyncio.sleep(1.1)
        for entity in mock_config_entry_multi.data["controlled_entities"]:
            assert_service_called(mock_hass, "light", "turn_off", entity["entity_id"])

    @pytest.mark.asyncio
    async def test_manual_override_is_per_entity(self, mock_hass, mock_config_entry_multi):
        setup_multi_entity_states(
            mock_hass,
            lights_states=[STATE_ON, STATE_ON],
            occupancy_states=[STATE_ON, STATE_ON],
        )
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_multi)
        await coordinator.async_start()

        manual_context = type("Ctx", (), {"id": "manual", "parent_id": None})()
        await coordinator._handle_controlled_entity_change(
            _event(mock_hass, "light.living_room_1", STATE_ON, STATE_OFF, context=manual_context)
        )

        mock_hass.services.clear()
        mock_hass.states.set("binary_sensor.motion_2", STATE_OFF)
        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.motion_1", STATE_ON, STATE_OFF)
        )
        await asyncio.sleep(1.1)

        assert coordinator.get_presence_allowed("light.living_room_1") is False
        assert coordinator.get_presence_allowed("light.living_room_2") is True

        assert_service_called(mock_hass, "light", "turn_off", "light.living_room_2")
        for call in mock_hass.services.calls:
            if call["domain"] == "light" and call["service"] == "turn_off":
                targets = call["service_data"].get("entity_id")
                if isinstance(targets, str):
                    targets = [targets]
                assert "light.living_room_1" not in targets
