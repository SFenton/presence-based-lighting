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


class TestPrimerSensorScenario:
    """Test scenarios where primer sensor triggers but clearing sensor is never activated."""

    @pytest.fixture
    def mock_config_entry_primer_scenario(self):
        """Config entry with 0 delay for faster tests."""
        from unittest.mock import MagicMock
        from custom_components.presence_based_lighting.const import (
            CONF_ROOM_NAME, CONF_PRESENCE_SENSORS, CONF_CLEARING_SENSORS,
            CONF_OFF_DELAY, CONF_CONTROLLED_ENTITIES, CONF_ENTITY_ID,
            CONF_PRESENCE_DETECTED_SERVICE, CONF_PRESENCE_CLEARED_SERVICE,
            CONF_PRESENCE_DETECTED_STATE, CONF_PRESENCE_CLEARED_STATE,
            CONF_RESPECTS_PRESENCE_ALLOWED, CONF_DISABLE_ON_EXTERNAL_CONTROL,
            DEFAULT_DETECTED_SERVICE, DEFAULT_CLEARED_SERVICE,
            DEFAULT_DETECTED_STATE, DEFAULT_CLEARED_STATE, DOMAIN,
        )
        
        entry = MagicMock()
        entry.domain = DOMAIN
        entry.version = 2
        entry.data = {
            CONF_ROOM_NAME: "Office",
            CONF_PRESENCE_SENSORS: ["binary_sensor.office_pir"],
            CONF_CLEARING_SENSORS: ["binary_sensor.office_occupancy"],
            CONF_OFF_DELAY: 0,  # Zero delay for immediate timer firing
            CONF_CONTROLLED_ENTITIES: [
                {
                    CONF_ENTITY_ID: "light.office",
                    CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                    CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                    CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                    CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                    CONF_RESPECTS_PRESENCE_ALLOWED: True,
                    CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                }
            ],
        }
        entry.entry_id = "test_entry_primer"
        entry.unique_id = "Office"
        entry.options = {}
        entry.async_on_unload = MagicMock()
        entry.add_update_listener = MagicMock()
        return entry

    @pytest.mark.asyncio
    async def test_primer_triggers_clearing_never_on_timer_turns_off(self, mock_hass, mock_config_entry_primer_scenario):
        """When primer triggers but person never enters main room, lights should still turn off.
        
        Scenario:
        - Presence sensors: PIR (primer)
        - Clearing sensors: occupancy sensor
        - PIR triggers (person near entrance)
        - Person never enters main room (occupancy sensor never goes "on")
        - After timeout, lights should turn off because clearing sensor is already cleared
        """
        import asyncio
        
        mock_hass.states.set("light.office", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_OFF)  # Never goes on

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_primer_scenario)
        await coordinator.async_start()

        # PIR triggers (primer) - lights turn on
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_pir", STATE_OFF, STATE_ON)
        )
        assert_service_called(mock_hass, "light", "turn_on", "light.office")
        
        # Update light state to reflect it's now on
        mock_hass.states.set("light.office", STATE_ON)
        
        # Wait for timer task to complete (delay is 0)
        # Give it a few event loop iterations
        for _ in range(5):
            await asyncio.sleep(0.01)
        
        # The cleared action should have been called because:
        # 1. Timer fired after off_delay (0)
        # 2. Clearing sensor (occupancy) was already in cleared state
        assert_service_called(mock_hass, "light", "turn_off", "light.office")

    @pytest.mark.asyncio
    async def test_primer_triggers_person_enters_lights_stay_on(self, mock_hass, mock_config_entry_primer_scenario):
        """When primer triggers and person enters main room, lights should stay on.
        
        Scenario:
        - PIR triggers (person near entrance)
        - Person enters main room (occupancy sensor goes "on")
        - Timer fires but clearing sensor is NOT cleared -> lights stay on
        """
        import asyncio
        
        mock_hass.states.set("light.office", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_OFF)

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_primer_scenario)
        await coordinator.async_start()

        # PIR triggers (primer) - lights turn on
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_pir", STATE_OFF, STATE_ON)
        )
        assert_service_called(mock_hass, "light", "turn_on", "light.office")
        mock_hass.states.set("light.office", STATE_ON)
        
        # Person enters main room BEFORE timer fires - occupancy goes on
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_ON)
        
        # Wait for timer to fire
        for _ in range(5):
            await asyncio.sleep(0.01)
        
        # turn_off should NOT have been called because occupancy sensor is on
        turn_off_calls = [c for c in mock_hass.services.calls 
                         if c["service"] == "turn_off" and c["entity_id"] == "light.office"]
        assert len(turn_off_calls) == 0, "Lights should not turn off when occupancy sensor is on"

    @pytest.mark.asyncio
    async def test_clearing_sensor_transition_restarts_timer(self, mock_hass, mock_config_entry_primer_scenario):
        """When clearing sensor actually clears, it should restart the timer.
        
        Scenario:
        - PIR triggers, person enters (occupancy on)
        - Timer fires, occupancy on -> lights stay on
        - Person leaves (occupancy off)
        - Clearing sensor transition should restart timer
        - Timer fires, occupancy off -> lights turn off
        """
        import asyncio
        
        mock_hass.states.set("light.office", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_OFF)

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_primer_scenario)
        await coordinator.async_start()

        # PIR triggers, lights on
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_pir", STATE_OFF, STATE_ON)
        )
        mock_hass.states.set("light.office", STATE_ON)
        
        # Person enters BEFORE initial timer fires - occupancy on
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_ON)
        
        # Wait for initial timer to fire (won't turn off because occupancy on)
        for _ in range(5):
            await asyncio.sleep(0.01)
        
        # Clear service calls
        mock_hass.services.clear()
        
        # Person leaves - occupancy clears
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_OFF)
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_occupancy", STATE_ON, STATE_OFF)
        )
        
        # Wait for new timer to fire
        for _ in range(5):
            await asyncio.sleep(0.01)
        
        # Now lights should turn off
        assert_service_called(mock_hass, "light", "turn_off", "light.office")
