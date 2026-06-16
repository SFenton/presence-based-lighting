"""Tests for separate trigger vs clearing sensors."""

import asyncio
import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import (
    ActuationStatus,
    IntentReason,
    PresenceBasedLightingCoordinator,
    EntityAutomationState,
    _autofill_vacancy_authority_sensors,
    _get_exact_room_vacancy_authority_sensor,
)
from custom_components.presence_based_lighting.const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_OFF_DELAY,
    CONF_PRESENCE_SENSORS,
    CONF_PRESENCE_CLEARED_SERVICE,
    CONF_PRESENCE_DETECTED_SERVICE,
    CONF_REQUIRE_VACANCY_FOR_CLEARED,
    CONF_VACANCY_AUTHORITY_AUTO_DISCOVERED,
    CONF_VACANCY_AUTHORITY_SENSORS,
)
from tests.conftest import assert_service_called, setup_entity_states


def _entity_event(mock_hass, entity_id, old_state, new_state, old_attrs=None, new_attrs=None):
    mock_hass.states.set(entity_id, new_state, attributes=new_attrs or {})
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

    @pytest.mark.asyncio
    async def test_vacancy_authority_blocks_raw_clearing_flap(
        self, mock_hass, mock_config_entry_separate_clearing
    ):
        """Raw clearing flaps off must not clear while stable room occupancy is still on."""
        authority = "sensor.office_occupancy_status_last_changed"
        mock_config_entry_separate_clearing.data[CONF_OFF_DELAY] = 0
        mock_config_entry_separate_clearing.data[CONF_VACANCY_AUTHORITY_SENSORS] = [authority]

        mock_hass.states.set("light.office", STATE_ON)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_OFF)
        mock_hass.states.set(
            authority,
            "2026-06-16T06:08:28+00:00",
            attributes={"previous_valid_state": STATE_ON},
        )

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_separate_clearing)
        await coordinator.async_start()
        entity_state = coordinator._entity_states["light.office"]
        if entity_state["off_timer"]:
            entity_state["off_timer"].cancel()
            entity_state["off_timer"] = None
        coordinator._set_entity_state(
            "light.office",
            entity_state,
            EntityAutomationState.OCCUPIED,
            "test occupied",
        )

        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_occupancy", STATE_ON, STATE_OFF)
        )
        await asyncio.sleep(0.08)

        turn_off_calls = [
            call for call in mock_hass.services.calls
            if call["service"] == "turn_off" and call["service_data"]["entity_id"] == "light.office"
        ]
        assert turn_off_calls == []
        assert coordinator._entity_states["light.office"]["state"] == EntityAutomationState.OCCUPIED

    @pytest.mark.asyncio
    async def test_vacancy_authority_occupied_cancels_pending_clear(
        self, mock_hass, mock_config_entry_separate_clearing
    ):
        """Stable occupancy turning on cancels a pending raw-sensor clear."""
        authority = "sensor.office_occupancy_status_last_changed"
        mock_config_entry_separate_clearing.data[CONF_OFF_DELAY] = 10
        mock_config_entry_separate_clearing.data[CONF_VACANCY_AUTHORITY_SENSORS] = [authority]

        mock_hass.states.set("light.office", STATE_ON)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_OFF)
        mock_hass.states.set(
            authority,
            "2026-06-16T06:00:06+00:00",
            attributes={"previous_valid_state": STATE_OFF},
        )

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_separate_clearing)
        await coordinator.async_start()
        entity_state = coordinator._entity_states["light.office"]
        if entity_state["off_timer"]:
            entity_state["off_timer"].cancel()
            entity_state["off_timer"] = None
        coordinator._set_entity_state(
            "light.office",
            entity_state,
            EntityAutomationState.OCCUPIED,
            "test occupied",
        )

        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_occupancy", STATE_ON, STATE_OFF)
        )
        assert coordinator._entity_states["light.office"]["state"] == EntityAutomationState.CLEARING

        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(
                mock_hass,
                authority,
                "2026-06-16T06:00:06+00:00",
                "2026-06-16T06:08:28+00:00",
                old_attrs={"previous_valid_state": STATE_OFF},
                new_attrs={"previous_valid_state": STATE_ON},
            )
        )
        await asyncio.sleep(0.08)

        turn_off_calls = [
            call for call in mock_hass.services.calls
            if call["service"] == "turn_off" and call["service_data"]["entity_id"] == "light.office"
        ]
        assert turn_off_calls == []
        assert coordinator._entity_states["light.office"]["state"] == EntityAutomationState.OCCUPIED


class TestVacancyAuthorityAutoFill:
    """Test exact room-level vacancy authority auto-discovery."""

    def test_prefers_exact_room_rlc_occupancy_status(
        self, mock_hass, mock_config_entry_separate_clearing
    ):
        mock_hass.states.set(
            "sensor.office_office_occupancy_status_last_changed",
            "2026-06-16T06:08:28+00:00",
            attributes={"previous_valid_state": STATE_ON},
        )
        mock_hass.states.set("binary_sensor.office_office_occupancy_status", STATE_ON)

        assert _autofill_vacancy_authority_sensors(
            mock_hass,
            mock_config_entry_separate_clearing,
        )
        assert mock_config_entry_separate_clearing.data[CONF_VACANCY_AUTHORITY_SENSORS] == [
            "sensor.office_office_occupancy_status_last_changed"
        ]
        assert mock_config_entry_separate_clearing.data[CONF_VACANCY_AUTHORITY_AUTO_DISCOVERED]

    def test_uses_non_repeated_exact_room_binary_when_needed(
        self, mock_hass, mock_config_entry_separate_clearing
    ):
        mock_config_entry_separate_clearing.data["room_name"] = "Upper Deck"
        mock_hass.states.set("binary_sensor.upper_deck_occupancy_status", STATE_OFF)

        assert _get_exact_room_vacancy_authority_sensor(
            mock_hass,
            "Upper Deck",
        ) == "binary_sensor.upper_deck_occupancy_status"

    def test_does_not_use_parent_room_for_scoped_room(
        self, mock_hass, mock_config_entry_separate_clearing
    ):
        mock_config_entry_separate_clearing.data["room_name"] = "Master Bedroom Closet"
        mock_hass.states.set("sensor.master_bedroom_master_bedroom_occupancy_status_last_changed", STATE_ON)

        assert not _autofill_vacancy_authority_sensors(
            mock_hass,
            mock_config_entry_separate_clearing,
        )
        assert mock_config_entry_separate_clearing.data.get(CONF_VACANCY_AUTHORITY_SENSORS, []) == []

    def test_manual_clear_after_auto_discovery_is_respected(
        self, mock_hass, mock_config_entry_separate_clearing
    ):
        mock_config_entry_separate_clearing.data[CONF_VACANCY_AUTHORITY_SENSORS] = []
        mock_config_entry_separate_clearing.data[CONF_VACANCY_AUTHORITY_AUTO_DISCOVERED] = True
        mock_hass.states.set(
            "sensor.office_office_occupancy_status_last_changed",
            "2026-06-16T06:08:28+00:00",
            attributes={"previous_valid_state": STATE_ON},
        )

        assert not _autofill_vacancy_authority_sensors(
            mock_hass,
            mock_config_entry_separate_clearing,
        )
        assert mock_config_entry_separate_clearing.data[CONF_VACANCY_AUTHORITY_SENSORS] == []


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
    async def test_primer_timer_respects_vacancy_requirement(
        self, mock_hass, mock_config_entry_primer_scenario
    ):
        """A primer sensor that is still on when the timer wakes should defer turn_off."""
        mock_config_entry_primer_scenario.data[CONF_OFF_DELAY] = 0.01
        mock_config_entry_primer_scenario.data[CONF_PRESENCE_SENSORS] = [
            "binary_sensor.office_pir",
            "binary_sensor.office_occupancy",
        ]
        mock_config_entry_primer_scenario.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_VACANCY_FOR_CLEARED] = True

        mock_hass.states.set("light.office", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_OFF)

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_primer_scenario)
        await coordinator.async_start()

        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_pir", STATE_OFF, STATE_ON)
        )
        assert_service_called(mock_hass, "light", "turn_on", "light.office")
        mock_hass.states.set("light.office", STATE_ON)
        mock_hass.services.clear()

        await asyncio.sleep(0.03)

        turn_off_calls = [
            call for call in mock_hass.services.calls
            if call["service"] == "turn_off" and call["entity_id"] == "light.office"
        ]
        assert turn_off_calls == []
        assert coordinator._entity_states["light.office"]["state"] == EntityAutomationState.CLEARING

        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        await asyncio.sleep(0.03)

        assert_service_called(mock_hass, "light", "turn_off", "light.office")

    @pytest.mark.asyncio
    async def test_presence_overrides_inflight_clear_even_when_light_still_reports_on(
        self, mock_hass, mock_config_entry_primer_scenario
    ):
        """Presence should send turn_on when superseding a pending turn_off actuation."""
        mock_hass.states.set("light.office", STATE_ON)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_OFF)

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_primer_scenario)
        await coordinator.async_start()

        entity_state = coordinator._entity_states["light.office"]
        if entity_state["off_timer"]:
            entity_state["off_timer"].cancel()
            entity_state["off_timer"] = None

        mock_hass.states.set("binary_sensor.office_pir", STATE_ON)
        coordinator._set_entity_state(
            "light.office",
            entity_state,
            EntityAutomationState.SETTLING_OFF,
            "test pending cleared actuation",
        )
        entity_state["actuation"].update(
            {
                "status": ActuationStatus.PENDING,
                "target_state": STATE_OFF,
                "service_key": CONF_PRESENCE_CLEARED_SERVICE,
            }
        )

        mock_hass.services.clear()
        await coordinator._apply_service_intent(
            "light.office",
            entity_state,
            CONF_PRESENCE_DETECTED_SERVICE,
            IntentReason.PRESENCE,
        )

        assert_service_called(mock_hass, "light", "turn_on", "light.office")

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


class TestOccupiedDoesNotTransitionToClearingWhileSensorsActive:
    """Test that OCCUPIED entities do NOT start the off-timer when clearing sensors are still active.

    This was a bug where every presence-ON event unconditionally started the off-timer,
    pushing the entity through OCCUPIED → CLEARING → WAITING_FOR_CLEAR → safety timeout →
    IDLE (forced off), even though sensors never indicated absence.
    """

    @pytest.mark.asyncio
    async def test_presence_on_stays_occupied_when_clearing_sensors_active(
        self, mock_hass, mock_config_entry_separate_clearing
    ):
        """When PIR triggers and occupancy sensor is ON, entity should stay OCCUPIED (no timer)."""
        mock_hass.states.set("light.office", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_ON)  # clearing sensor active

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_separate_clearing)
        await coordinator.async_start()

        # PIR triggers detected
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_pir", STATE_OFF, STATE_ON)
        )
        assert_service_called(mock_hass, "light", "turn_on", "light.office")

        es = coordinator._entity_states["light.office"]
        # Entity should be OCCUPIED, NOT CLEARING, because clearing sensor is still on
        assert es["state"] == EntityAutomationState.OCCUPIED
        assert es["off_timer"] is None, "Off-timer should not start while clearing sensors are active"

    @pytest.mark.asyncio
    async def test_presence_on_starts_timer_when_clearing_sensors_clear(
        self, mock_hass, mock_config_entry_separate_clearing
    ):
        """When PIR triggers and occupancy sensor is OFF (primer case), timer should start."""
        mock_hass.states.set("light.office", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_OFF)  # clearing sensor clear

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_separate_clearing)
        await coordinator.async_start()

        # PIR triggers detected
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_pir", STATE_OFF, STATE_ON)
        )
        assert_service_called(mock_hass, "light", "turn_on", "light.office")

        es = coordinator._entity_states["light.office"]
        # Entity should be CLEARING because clearing sensors are already clear (primer case)
        assert es["state"] == EntityAutomationState.CLEARING
        assert es["off_timer"] is not None, "Off-timer should start when clearing sensors are clear"

    @pytest.mark.asyncio
    async def test_same_sensor_for_presence_and_clearing_starts_timer(
        self, mock_hass, mock_config_entry
    ):
        """When same sensor is used for presence AND clearing, ON event should NOT start timer.

        The sensor going ON means the room is occupied. The off-timer should only start
        when ALL clearing sensors go OFF.
        """
        setup_entity_states(mock_hass, lights_state="off", occupancy_state="off")

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Motion triggers
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF, STATE_ON)
        )
        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")

        es = coordinator._entity_states["light.living_room"]
        # Same sensor = presence + clearing. Sensor is ON so clearing is NOT clear.
        # Entity should stay OCCUPIED.
        assert es["state"] == EntityAutomationState.OCCUPIED
        assert es["off_timer"] is None, "Off-timer should not start when the sole sensor (also clearing) is ON"

    @pytest.mark.asyncio
    async def test_continuous_presence_never_reaches_waiting_for_clear(
        self, mock_hass, mock_config_entry_separate_clearing
    ):
        """Simulate continuous presence: repeated PIR events should not push entity into CLEARING.

        This is the core bug scenario: user is in the room, sensors keep firing,
        but the entity should stay in OCCUPIED the entire time.
        """
        mock_hass.states.set("light.office", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
        mock_hass.states.set("binary_sensor.office_occupancy", STATE_ON)  # person in room

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_separate_clearing)
        await coordinator.async_start()

        # Initial trigger
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.office_pir", STATE_OFF, STATE_ON)
        )

        es = coordinator._entity_states["light.office"]
        assert es["state"] == EntityAutomationState.OCCUPIED

        # Simulate repeated PIR triggers (sensor goes off/on a few times)
        for _ in range(5):
            mock_hass.states.set("binary_sensor.office_pir", STATE_OFF)
            await coordinator._handle_presence_change(
                _entity_event(mock_hass, "binary_sensor.office_pir", STATE_ON, STATE_OFF)
            )
            mock_hass.states.set("binary_sensor.office_pir", STATE_ON)
            await coordinator._handle_presence_change(
                _entity_event(mock_hass, "binary_sensor.office_pir", STATE_OFF, STATE_ON)
            )

            # Must never leave OCCUPIED while occupancy sensor is active
            assert es["state"] == EntityAutomationState.OCCUPIED, (
                f"Entity left OCCUPIED after PIR cycle, got {es['state']}"
            )
            assert es["off_timer"] is None, "Off-timer should never start while clearing sensors are active"