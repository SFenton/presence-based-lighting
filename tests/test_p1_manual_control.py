"""Manual control behavior for per-entity presence automation."""

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.const import CONF_CONTROLLED_ENTITIES, CONF_DISABLE_ON_EXTERNAL_CONTROL
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
