"""Manual control behavior for per-entity presence automation."""

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
    CONF_REQUIRE_VACANCY_FOR_CLEARED,
    CONF_RESPECTS_PRESENCE_ALLOWED,
)
from tests.conftest import assert_service_called, setup_entity_states


def _entity_event(mock_hass, entity_id, old_state, new_state):
    mock_hass.states.set(entity_id, new_state)
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": type("State", (), {"state": old_state, "context": type("Ctx", (), {"id": "old", "parent_id": None})()})(),
                "new_state": type(
                    "State",
                    (),
                    {
                        "state": new_state,
                        "context": type("Ctx", (), {"id": "manual", "parent_id": None})(),
                    },
                )(),
            }
        },
    )()


class TestManualOverrides:
    """Ensure manual interactions toggle per-entity presence as expected."""

    @pytest.mark.asyncio
    async def test_manual_off_blocks_presence_until_manual_on(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert coordinator.get_presence_allowed("light.living_room") is False

        # Occupancy should not turn the light back on while presence is disallowed
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF, STATE_ON)
        )
        assert mock_hass.services.calls == []

        # Manual on re-enables presence
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_OFF, STATE_ON)
        )
        assert coordinator.get_presence_allowed("light.living_room") is True

        mock_hass.services.clear()
        mock_hass.states.set("light.living_room", STATE_OFF)
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF, STATE_ON)
        )
        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")

    @pytest.mark.asyncio
    async def test_entity_can_opt_out_of_manual_disable(self, mock_hass, mock_config_entry):
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_DISABLE_ON_EXTERNAL_CONTROL] = False
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert coordinator.get_presence_allowed("light.living_room") is True

        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF, STATE_ON)
        )
        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")

    @pytest.mark.asyncio
    async def test_manual_disable_holds_even_when_switch_hidden(self, mock_hass, mock_config_entry):
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_RESPECTS_PRESENCE_ALLOWED] = False
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert coordinator.get_presence_allowed("light.living_room") is False

        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF, STATE_ON)
        )
        assert mock_hass.services.calls == []

    @pytest.mark.asyncio
    async def test_detected_state_blocked_when_room_empty(self, mock_hass, mock_config_entry):
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_OCCUPANCY_FOR_DETECTED] = True
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        mock_hass.services.clear()
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_OFF, STATE_ON)
        )
        assert_service_called(mock_hass, "light", "turn_off", "light.living_room")

    @pytest.mark.asyncio
    async def test_detected_state_allowed_when_room_occupied(self, mock_hass, mock_config_entry):
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_OCCUPANCY_FOR_DETECTED] = True
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        mock_hass.services.clear()
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_OFF, STATE_ON)
        )
        assert mock_hass.services.calls == []

    @pytest.mark.asyncio
    async def test_cleared_state_blocked_when_room_occupied(self, mock_hass, mock_config_entry):
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_VACANCY_FOR_CLEARED] = True
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        mock_hass.services.clear()
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")

    @pytest.mark.asyncio
    async def test_cleared_state_allowed_when_room_empty(self, mock_hass, mock_config_entry):
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_VACANCY_FOR_CLEARED] = True
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        mock_hass.services.clear()
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert mock_hass.services.calls == []

    @pytest.mark.asyncio
    async def test_detected_service_call_blocked_when_room_empty(self, mock_hass, mock_config_entry):
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_OCCUPANCY_FOR_DETECTED] = True
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        mock_hass.services.clear()
        service_event = type(
            "Event",
            (),
            {
                "data": {
                    "service_data": {"entity_id": "light.living_room"},
                    "service": "turn_on",
                },
                "context": type("Ctx", (), {"id": "manual", "parent_id": None})(),
            },
        )()
        await coordinator._handle_service_call(service_event)
        assert_service_called(mock_hass, "light", "turn_off", "light.living_room")

    @pytest.mark.asyncio
    async def test_group_service_expands_targets_for_presence_lock(self, mock_hass, mock_config_entry):
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_OCCUPANCY_FOR_DETECTED] = True
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        mock_hass.states.set(
            "light.important_lights",
            STATE_OFF,
            attributes={"entity_id": ["light.living_room"]},
        )
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        mock_hass.services.clear()
        service_event = type(
            "Event",
            (),
            {
                "data": {
                    "service_data": {"entity_id": "light.important_lights"},
                    "service": "turn_on",
                },
                "context": type("Ctx", (), {"id": "manual", "parent_id": None})(),
            },
        )()
        await coordinator._handle_service_call(service_event)
        assert_service_called(mock_hass, "light", "turn_off", "light.living_room")
