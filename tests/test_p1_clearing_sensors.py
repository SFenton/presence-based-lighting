"""Tests for separate trigger vs clearing sensors."""

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from tests.conftest import assert_service_called, setup_entity_states


def _entity_event(mock_hass, entity_id, old_state, new_state, old_attrs=None, new_attrs=None):
    mock_hass.states.set(entity_id, new_state)
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": type("State", (), {"state": old_state, "attributes": old_attrs or {}, "context": type("Ctx", (), {"id": "old", "parent_id": None})()})(),
                "new_state": type(
                    "State",
                    (),
                    {
                        "state": new_state,
                        "attributes": new_attrs or {},
                        "context": type("Ctx", (), {"id": "ctx", "parent_id": None})(),
                    },
                )(),
            }
        },
    )()


class TestSeparateClearingSensors:
    """Test separate trigger and clearing sensors."""

    @pytest.mark.asyncio
    async def test_pir_triggers_but_occupancy_clears(self, mock_hass, mock_config_entry_separate_clearing):
        """PIR turning on should trigger detected, but only occupancy turning off should clear."""
        mock_hass.states.set("light.office", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_ON)

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_separate_clearing)
        await coordinator.async_start()

        # PIR triggers detected action
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_pir", STATE_OFF, STATE_ON)
        )
        assert_service_called(mock_hass, "light", "turn_on", "light.office")

        # PIR going off should NOT start cleared timer because occupancy is still on
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_pir", STATE_ON, STATE_OFF)
        )
        # No service calls since occupancy still on
        assert mock_hass.services.calls == []

        # Now occupancy goes off, should start cleared timer
        mock_hass.states.set("light.office", STATE_ON)
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_occupancy", STATE_ON, STATE_OFF)
        )
        # Timer started, after delay it would call turn_off (tested separately)

    @pytest.mark.asyncio
    async def test_occupancy_on_does_not_trigger_if_not_in_presence_sensors(self, mock_hass, mock_config_entry_separate_clearing):
        """Occupancy sensor turning on should NOT trigger detected since it's only a clearing sensor."""
        mock_hass.states.set("light.office", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_OFF)

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_separate_clearing)
        await coordinator.async_start()

        # Occupancy turning on should NOT trigger detected (it's only in clearing_sensors)
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_occupancy", STATE_OFF, STATE_ON)
        )
        assert mock_hass.services.calls == []

    @pytest.mark.asyncio
    async def test_fallback_to_presence_sensors_when_no_clearing_sensors(self, mock_hass, mock_config_entry):
        """When no clearing sensors configured, presence sensors are used for both."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Motion going off should start cleared timer (using presence sensors as fallback)
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.living_room_motion", STATE_ON, STATE_OFF)
        )
        # Timer started - the cleared action happens after delay

    @pytest.mark.asyncio
    async def test_pir_cancels_timer_even_if_only_in_trigger_sensors(self, mock_hass, mock_config_entry_separate_clearing):
        """PIR going on should cancel timers even if occupancy was the one that started them."""
        mock_hass.states.set("light.office", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_OFF)

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_separate_clearing)
        await coordinator.async_start()

        # Occupancy going off starts timer
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_occupancy", STATE_ON, STATE_OFF)
        )

        # PIR going on should cancel the timer and trigger detected
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_pir", STATE_OFF, STATE_ON)
        )
        # Detected action called
        assert_service_called(mock_hass, "light", "turn_on", "light.office")
