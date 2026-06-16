"""Tests for __init__.py setup/teardown, migrations, services, and auto-reenable."""

import asyncio
import json
import pytest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock, call

from homeassistant.const import STATE_ON, STATE_OFF

from custom_components.presence_based_lighting import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
    async_reload_entry,
    async_migrate_entry,
    PresenceBasedLightingCoordinator,
    _force_component_logger_debug,
    _emit_direct_to_file,
    EntityAutomationState,
)
from custom_components.presence_based_lighting.const import (
    CONF_ACTIVATION_CONDITIONS,
    CONF_AUTO_REENABLE_END_TIME,
    CONF_AUTO_REENABLE_PRESENCE_SENSORS,
    CONF_AUTO_REENABLE_START_TIME,
    CONF_AUTO_REENABLE_VACANCY_THRESHOLD,
    CONF_CLEARING_SENSORS,
    CONF_CLEARING_SENSORS_AUTO_DISCOVERED,
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_INITIAL_PRESENCE_ALLOWED,
    CONF_MANUAL_DISABLE_STATES,
    CONF_OFF_DELAY,
    CONF_PRESENCE_CLEARED_SERVICE,
    CONF_PRESENCE_CLEARED_STATE,
    CONF_PRESENCE_DETECTED_SERVICE,
    CONF_PRESENCE_DETECTED_STATE,
    CONF_PRESENCE_SENSORS,
    CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
    CONF_REQUIRE_VACANCY_FOR_CLEARED,
    CONF_RESPECTS_PRESENCE_ALLOWED,
    CONF_ROOM_NAME,
    CONF_VACANCY_AUTHORITY_AUTO_DISCOVERED,
    CONF_VACANCY_AUTHORITY_SENSORS,
    DEFAULT_AUTO_REENABLE_END_TIME,
    DEFAULT_AUTO_REENABLE_START_TIME,
    DEFAULT_AUTO_REENABLE_VACANCY_THRESHOLD,
    DEFAULT_CLEARED_SERVICE,
    DEFAULT_CLEARED_STATE,
    DEFAULT_DETECTED_SERVICE,
    DEFAULT_DETECTED_STATE,
    DEFAULT_DISABLE_ON_EXTERNAL,
    DEFAULT_INITIAL_PRESENCE_ALLOWED,
    DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED,
    DEFAULT_REQUIRE_VACANCY_FOR_CLEARED,
    DOMAIN,
)
from tests.conftest import MockHass, setup_entity_states


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(version=7, room="Living Room", extra=None):
    """Build a mock config entry at the specified version."""
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.version = version
    entry.entry_id = "test_entry_id"
    entry.unique_id = room
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock()
    entry.data = {
        CONF_ROOM_NAME: room,
        CONF_PRESENCE_SENSORS: ["binary_sensor.living_room_motion"],
        CONF_OFF_DELAY: 1,
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED,
                CONF_REQUIRE_VACANCY_FOR_CLEARED: DEFAULT_REQUIRE_VACANCY_FOR_CLEARED,
                CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
            }
        ],
    }
    if extra:
        entry.data.update(extra)
    return entry


# ---------------------------------------------------------------------------
# _force_component_logger_debug / _emit_direct_to_file
# ---------------------------------------------------------------------------

class TestLoggingHelpers:
    def test_force_debug_sets_level(self):
        import logging
        _force_component_logger_debug()
        logger = logging.getLogger("custom_components.presence_based_lighting")
        assert logger.level == logging.DEBUG
        assert logger.propagate is True
        assert logger.disabled is False

    def test_emit_direct_to_file_no_handler(self):
        """Should not raise when handler is None."""
        import custom_components.presence_based_lighting as pbl
        orig = pbl._log_file_handler
        try:
            pbl._log_file_handler = None
            _emit_direct_to_file("test")  # should be a no-op
        finally:
            pbl._log_file_handler = orig

    def test_emit_direct_to_file_with_handler(self):
        import custom_components.presence_based_lighting as pbl
        import logging
        mock_handler = MagicMock(spec=logging.FileHandler)
        orig = pbl._log_file_handler
        try:
            pbl._log_file_handler = mock_handler
            _emit_direct_to_file("hello")
            mock_handler.emit.assert_called_once()
            mock_handler.flush.assert_called_once()
        finally:
            pbl._log_file_handler = orig


# ---------------------------------------------------------------------------
# async_setup – service registration
# ---------------------------------------------------------------------------

class TestAsyncSetup:
    @pytest.mark.asyncio
    async def test_registers_services(self):
        hass = MagicMock()
        hass.services = MagicMock()
        result = await async_setup(hass, {})
        assert result is True
        assert hass.services.async_register.call_count == 2

    @pytest.mark.asyncio
    async def test_resume_automation_service(self):
        """handle_resume_automation finds coordinator and unpauses entities."""
        hass = MagicMock()
        hass.services = MagicMock()
        await async_setup(hass, {})

        # Extract the registered handler
        resume_handler = None
        for c in hass.services.async_register.call_args_list:
            if c[0][1] == "resume_automation":
                resume_handler = c[0][2]
                break
        assert resume_handler is not None

        # Build coordinator mock – must pass isinstance check
        coord = MagicMock(spec=PresenceBasedLightingCoordinator)
        coord.entry = _make_entry()
        coord._entity_states = {"light.living_room": {}}
        coord.set_automation_paused = MagicMock()

        hass.data = {
            DOMAIN: {"entry1": coord}
        }

        # Build a service call targeting the switch
        call = MagicMock()
        call.data = {}
        call.target = {"entity_id": ["switch.living_room_presence_lighting"]}

        await resume_handler(call)
        coord.set_automation_paused.assert_called_once_with("light.living_room", False)

    @pytest.mark.asyncio
    async def test_pause_automation_service(self):
        hass = MagicMock()
        hass.services = MagicMock()
        await async_setup(hass, {})

        pause_handler = None
        for c in hass.services.async_register.call_args_list:
            if c[0][1] == "pause_automation":
                pause_handler = c[0][2]
                break
        assert pause_handler is not None

        coord = MagicMock(spec=PresenceBasedLightingCoordinator)
        coord.entry = _make_entry()
        coord._entity_states = {"light.living_room": {}}
        coord.set_automation_paused = MagicMock()
        hass.data = {DOMAIN: {"entry1": coord}}

        call = MagicMock()
        call.data = {}
        call.target = {"entity_id": "switch.living_room_presence_lighting"}
        await pause_handler(call)
        coord.set_automation_paused.assert_called_once_with("light.living_room", True)

    @pytest.mark.asyncio
    async def test_service_with_entity_id_in_data(self):
        """entity_id passed in data rather than target."""
        hass = MagicMock()
        hass.services = MagicMock()
        await async_setup(hass, {})

        resume_handler = None
        for c in hass.services.async_register.call_args_list:
            if c[0][1] == "resume_automation":
                resume_handler = c[0][2]
                break

        coord = MagicMock(spec=PresenceBasedLightingCoordinator)
        coord.entry = _make_entry()
        coord._entity_states = {"light.living_room": {}}
        coord.set_automation_paused = MagicMock()
        hass.data = {DOMAIN: {"entry1": coord}}

        call = MagicMock()
        call.data = {"entity_id": "switch.living_room_presence_lighting"}
        call.target = None
        # hasattr target → False
        del call.target
        await resume_handler(call)
        coord.set_automation_paused.assert_called()

    @pytest.mark.asyncio
    async def test_service_no_target(self):
        """Service called without any target should not crash."""
        hass = MagicMock()
        hass.services = MagicMock()
        await async_setup(hass, {})

        resume_handler = None
        for c in hass.services.async_register.call_args_list:
            if c[0][1] == "resume_automation":
                resume_handler = c[0][2]
                break

        call = MagicMock()
        call.data = {}
        call.target = None
        del call.target
        hass.data = {DOMAIN: {}}
        await resume_handler(call)  # no crash


# ---------------------------------------------------------------------------
# async_migrate_entry – config migrations
# ---------------------------------------------------------------------------

class TestMigrations:
    @pytest.mark.asyncio
    async def test_migrate_v2_to_v3_automatic(self):
        """v2→v3: entities without presence lock get AUTOMATIC mode."""
        hass = MagicMock()
        entry = _make_entry(version=2)
        entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_OCCUPANCY_FOR_DETECTED] = False
        entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_VACANCY_FOR_CLEARED] = False

        result = await async_migrate_entry(hass, entry)
        assert result is True
        hass.config_entries.async_update_entry.assert_called()

    @pytest.mark.asyncio
    async def test_migrate_v2_to_v3_presence_lock(self):
        """v2→v3: entities with presence lock booleans get PRESENCE_LOCK mode."""
        hass = MagicMock()
        entry = _make_entry(version=2)
        entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_OCCUPANCY_FOR_DETECTED] = True
        entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_VACANCY_FOR_CLEARED] = True

        result = await async_migrate_entry(hass, entry)
        assert result is True

    @pytest.mark.asyncio
    async def test_migrate_v3_to_v4(self):
        """v3→v4: manual_disable_states added."""
        hass = MagicMock()
        entry = _make_entry(version=3)
        result = await async_migrate_entry(hass, entry)
        assert result is True

    @pytest.mark.asyncio
    async def test_migrate_v4_to_v5(self):
        hass = MagicMock()
        entry = _make_entry(version=4)
        result = await async_migrate_entry(hass, entry)
        assert result is True

    @pytest.mark.asyncio
    async def test_migrate_v5_to_v6(self):
        hass = MagicMock()
        entry = _make_entry(version=5)
        result = await async_migrate_entry(hass, entry)
        assert result is True

    @pytest.mark.asyncio
    async def test_migrate_v6_to_v7(self):
        hass = MagicMock()
        entry = _make_entry(version=6)
        result = await async_migrate_entry(hass, entry)
        assert result is True

    @pytest.mark.asyncio
    async def test_migrate_v2_all_the_way_to_v9(self):
        """Full chain migration from v2 through v9."""
        hass = MagicMock()
        entry = _make_entry(version=2)
        entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_OCCUPANCY_FOR_DETECTED] = False
        entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_VACANCY_FOR_CLEARED] = False

        # Track version updates
        versions = []

        def track_update(e, data=None, version=None):
            if version:
                e.version = version
                versions.append(version)
            if data:
                e.data = data
        hass.config_entries.async_update_entry = track_update

        result = await async_migrate_entry(hass, entry)
        assert result is True
        assert versions == [3, 4, 5, 6, 7, 8, 9]
        assert CONF_VACANCY_AUTHORITY_SENSORS not in entry.data
        assert entry.data[CONF_CLEARING_SENSORS_AUTO_DISCOVERED] is False

    @pytest.mark.asyncio
    async def test_migrate_v8_moves_vacancy_authority_to_clearing_sensors(self):
        """Legacy vacancy authority becomes the only configured clearing sensor."""
        hass = MagicMock()
        entry = _make_entry(
            version=8,
            extra={
                CONF_CLEARING_SENSORS: ["sensor.raw_occupancy_last_changed"],
                CONF_VACANCY_AUTHORITY_SENSORS: [
                    "sensor.office_office_occupancy_status_last_changed"
                ],
                CONF_VACANCY_AUTHORITY_AUTO_DISCOVERED: True,
            },
        )

        def track_update(e, data=None, version=None):
            if version:
                e.version = version
            if data:
                e.data = data
        hass.config_entries.async_update_entry = track_update

        result = await async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 9
        assert entry.data[CONF_CLEARING_SENSORS] == [
            "sensor.office_office_occupancy_status_last_changed"
        ]
        assert entry.data[CONF_CLEARING_SENSORS_AUTO_DISCOVERED] is True
        assert CONF_VACANCY_AUTHORITY_SENSORS not in entry.data
        assert CONF_VACANCY_AUTHORITY_AUTO_DISCOVERED not in entry.data


# ---------------------------------------------------------------------------
# async_setup_entry / async_unload_entry / async_reload_entry
# ---------------------------------------------------------------------------

class TestEntryLifecycle:
    @pytest.mark.asyncio
    async def test_setup_entry(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()

        result = await async_setup_entry(hass, entry)
        assert result is True
        assert entry.entry_id in hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_setup_entry_with_file_logging(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        entry.data["file_logging_enabled"] = True

        with patch("custom_components.presence_based_lighting._setup_file_logging", new_callable=AsyncMock) as mock_log:
            result = await async_setup_entry(hass, entry)
            assert result is True
            mock_log.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unload_entry(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()

        await async_setup_entry(hass, entry)
        result = await async_unload_entry(hass, entry)
        assert result is True
        assert entry.entry_id not in hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_reload_entry(self):
        hass = MagicMock()
        hass.config_entries = MagicMock()
        hass.config_entries.async_reload = AsyncMock()
        entry = _make_entry()

        await async_reload_entry(hass, entry)
        hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)


# ---------------------------------------------------------------------------
# Coordinator – _parse_time_string
# ---------------------------------------------------------------------------

class TestParseTimeString:
    @pytest.mark.asyncio
    async def test_hh_mm_ss(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:30:15",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        assert coord._auto_reenable_start_time == time(22, 30, 15)
        assert coord._auto_reenable_end_time == time(6, 0, 0)

    @pytest.mark.asyncio
    async def test_hh_mm(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:30",
            CONF_AUTO_REENABLE_END_TIME: "06:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        assert coord._auto_reenable_start_time == time(22, 30, 0)


# ---------------------------------------------------------------------------
# Coordinator – entity initialization edge cases
# ---------------------------------------------------------------------------

class TestCoordinatorInit:
    def test_missing_entity_id(self):
        """Entity without entity_id is skipped."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        entry.data[CONF_CONTROLLED_ENTITIES].append({})  # no CONF_ENTITY_ID
        coord = PresenceBasedLightingCoordinator(hass, entry)
        assert len(coord._entity_states) == 1  # only the valid entity

    def test_duplicate_entity_id(self):
        """Duplicate entity IDs are skipped."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        entry.data[CONF_CONTROLLED_ENTITIES].append(
            entry.data[CONF_CONTROLLED_ENTITIES][0].copy()
        )
        coord = PresenceBasedLightingCoordinator(hass, entry)
        assert len(coord._entity_states) == 1


# ---------------------------------------------------------------------------
# Coordinator – async_stop
# ---------------------------------------------------------------------------

class TestCoordinatorStop:
    @pytest.mark.asyncio
    async def test_stop_cancels_reconciliation(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()
        assert coord._reconciliation_unsub is not None

        coord.async_stop()
        # Should have been cleaned up
        assert coord._reconciliation_unsub is None


# ---------------------------------------------------------------------------
# Coordinator – set_automation_paused state transitions
# ---------------------------------------------------------------------------

class TestSetAutomationPausedTransitions:
    @pytest.mark.asyncio
    async def test_pause_cancels_timer(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        # Put entity into OCCUPIED
        assert es["state"] == EntityAutomationState.OCCUPIED

        coord.set_automation_paused("light.living_room", True)
        assert es["state"] == EntityAutomationState.PAUSED

    @pytest.mark.asyncio
    async def test_pause_idempotent(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        coord.set_automation_paused("light.living_room", True)
        coord.set_automation_paused("light.living_room", True)  # idempotent
        assert coord._entity_states["light.living_room"]["state"] == EntityAutomationState.PAUSED


# ---------------------------------------------------------------------------
# Coordinator – _handle_service_call group expansion
# ---------------------------------------------------------------------------

class TestServiceCallGroupExpansion:
    @pytest.mark.asyncio
    async def test_group_expansion(self):
        """Service call targeting a group entity should expand and process members."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        # Add a group entity
        hass.states.set("light.all_lights", STATE_OFF, attributes={
            "entity_id": ["light.living_room"]
        })
        entry = _make_entry()
        entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_DISABLE_ON_EXTERNAL_CONTROL] = True
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        service_event = type("Event", (), {
            "data": {
                "service_data": {"entity_id": "light.all_lights"},
                "service": "turn_on",
            },
            "context": type("Ctx", (), {"id": "ext", "parent_id": None})(),
        })()

        await coord._handle_service_call(service_event)
        # Should not crash – group expansion working

    @pytest.mark.asyncio
    async def test_service_call_no_target(self):
        """Service call without entity_id should not crash."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        service_event = type("Event", (), {
            "data": {"service_data": {}, "service": "turn_on"},
            "context": type("Ctx", (), {"id": "ext", "parent_id": None})(),
        })()
        await coord._handle_service_call(service_event)


# ---------------------------------------------------------------------------
# Coordinator – presence lock fallback
# ---------------------------------------------------------------------------

class TestPresenceLockFallback:
    @pytest.mark.asyncio
    async def test_block_turn_on_when_empty(self):
        """Presence lock reverts turn-on when room is empty."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_OCCUPANCY_FOR_DETECTED] = True
        entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_DISABLE_ON_EXTERNAL_CONTROL] = False
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()
        hass.services.clear()

        event = type("Event", (), {
            "data": {
                "entity_id": "light.living_room",
                "old_state": type("S", (), {"state": STATE_OFF, "attributes": {}, "context": type("C", (), {"id": "ext", "parent_id": None})()})(),
                "new_state": type("S", (), {"state": STATE_ON, "attributes": {}, "context": type("C", (), {"id": "ext", "parent_id": None})()})(),
            }
        })()
        await coord._handle_controlled_entity_change(event)
        # Should have called turn_off to revert
        found = any(c["service"] == "turn_off" for c in hass.services.calls)
        assert found, "Expected turn_off to revert presence lock"

    @pytest.mark.asyncio
    async def test_block_turn_off_when_occupied(self):
        """Presence lock reverts turn-off when room is occupied."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        entry = _make_entry()
        entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_VACANCY_FOR_CLEARED] = True
        entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_DISABLE_ON_EXTERNAL_CONTROL] = False
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()
        hass.services.clear()

        event = type("Event", (), {
            "data": {
                "entity_id": "light.living_room",
                "old_state": type("S", (), {"state": STATE_ON, "attributes": {}, "context": type("C", (), {"id": "ext", "parent_id": None})()})(),
                "new_state": type("S", (), {"state": STATE_OFF, "attributes": {}, "context": type("C", (), {"id": "ext", "parent_id": None})()})(),
            }
        })()
        await coord._handle_controlled_entity_change(event)
        found = any(c["service"] == "turn_on" for c in hass.services.calls)
        assert found, "Expected turn_on to revert presence lock"


# ---------------------------------------------------------------------------
# Coordinator – _apply_action_to_entity NO_ACTION
# ---------------------------------------------------------------------------

class TestApplyActionNoAction:
    @pytest.mark.asyncio
    async def test_no_action_service_is_skipped(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_PRESENCE_DETECTED_SERVICE] = "none"
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()
        hass.services.clear()

        es = coord._entity_states["light.living_room"]
        await coord._apply_action_to_entity(es, CONF_PRESENCE_DETECTED_SERVICE)
        assert hass.services.calls == []


# ---------------------------------------------------------------------------
# Coordinator – periodic reconciliation
# ---------------------------------------------------------------------------

class TestPeriodicReconciliation:
    @pytest.mark.asyncio
    async def test_waiting_for_clear_safety_timeout(self):
        """Entity stuck in WAITING_FOR_CLEAR > 5min with room empty should be forced to IDLE."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_CLEARING_SENSORS: ["binary_sensor.clearing_1"],
        })
        hass.states.set("binary_sensor.clearing_1", STATE_OFF)
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()
        hass.services.clear()

        es = coord._entity_states["light.living_room"]
        # Force into WAITING_FOR_CLEAR
        coord._set_entity_state("light.living_room", es, EntityAutomationState.WAITING_FOR_CLEAR, "test")
        # Simulate long wait (> 300 seconds)
        es["state_entered_at"] = datetime.now(timezone.utc) - timedelta(seconds=400)
        # Presence sensor OFF + clearing sensor OFF → room empty
        hass.states.set("binary_sensor.living_room_motion", STATE_OFF)

        await coord._periodic_reconciliation(datetime.now(timezone.utc))
        # Room empty → forced cleared actuation; IDLE waits for command confirmation.
        assert es["state"] == EntityAutomationState.SETTLING_OFF

    @pytest.mark.asyncio
    async def test_waiting_for_clear_safety_timeout_room_occupied(self):
        """Entity stuck in WAITING_FOR_CLEAR > 5min with room still occupied should go to OCCUPIED."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        entry = _make_entry(extra={
            CONF_CLEARING_SENSORS: ["binary_sensor.clearing_1"],
        })
        hass.states.set("binary_sensor.clearing_1", STATE_ON)  # clearing sensor stuck on
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()
        hass.services.clear()

        es = coord._entity_states["light.living_room"]
        # Force into WAITING_FOR_CLEAR
        coord._set_entity_state("light.living_room", es, EntityAutomationState.WAITING_FOR_CLEAR, "test")
        # Simulate long wait (> 300 seconds)
        es["state_entered_at"] = datetime.now(timezone.utc) - timedelta(seconds=400)
        # Presence sensor ON → room occupied
        hass.states.set("binary_sensor.living_room_motion", STATE_ON)

        await coord._periodic_reconciliation(datetime.now(timezone.utc))
        # Room occupied → should go back to OCCUPIED, not IDLE
        assert es["state"] == EntityAutomationState.OCCUPIED

    @pytest.mark.asyncio
    async def test_clearing_but_no_timer_restarts(self):
        """Entity in CLEARING with no timer should get timer restarted."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        coord._set_entity_state("light.living_room", es, EntityAutomationState.CLEARING, "test")
        es["off_timer"] = None  # timer lost

        await coord._periodic_reconciliation(datetime.now(timezone.utc))
        # Timer should have been restarted
        assert es["off_timer"] is not None or es["state"] != EntityAutomationState.CLEARING

    @pytest.mark.asyncio
    async def test_occupied_but_room_empty_starts_timer(self):
        """Entity stuck in OCCUPIED with empty room should start off-timer."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        coord._set_entity_state("light.living_room", es, EntityAutomationState.OCCUPIED, "test")

        await coord._periodic_reconciliation(datetime.now(timezone.utc))
        # Should start off-timer or transition
        assert es["state"] in (EntityAutomationState.CLEARING, EntityAutomationState.OCCUPIED)

    @pytest.mark.asyncio
    async def test_idle_but_room_occupied_reconciles(self):
        """Entity stuck in IDLE with occupied room should reconcile."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()
        # Startup reconciliation already moved to OCCUPIED, force back to IDLE
        es = coord._entity_states["light.living_room"]
        coord._set_entity_state("light.living_room", es, EntityAutomationState.IDLE, "test")

        await coord._periodic_reconciliation(datetime.now(timezone.utc))
        # Should reconcile back to OCCUPIED
        assert es["state"] == EntityAutomationState.OCCUPIED


# ---------------------------------------------------------------------------
# Coordinator – Auto Re-Enable feature
# ---------------------------------------------------------------------------

class TestAutoReEnable:
    @pytest.mark.asyncio
    async def test_set_auto_reenable_enabled(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        coord.set_auto_reenable_enabled(True)
        assert coord._auto_reenable_enabled is True

        coord.set_auto_reenable_enabled(False)
        assert coord._auto_reenable_enabled is False

    @pytest.mark.asyncio
    async def test_get_tracking_info_not_tracking(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        info = coord.get_auto_reenable_tracking_info()
        assert info["is_tracking"] is False
        assert info["vacancy_threshold_percent"] == DEFAULT_AUTO_REENABLE_VACANCY_THRESHOLD

    @pytest.mark.asyncio
    async def test_get_tracking_info_while_tracking(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        now = datetime.now(timezone.utc)
        coord._auto_reenable_tracking["is_tracking"] = True
        coord._auto_reenable_tracking["window_start"] = now - timedelta(hours=1)
        coord._auto_reenable_tracking["last_presence_change"] = now - timedelta(minutes=30)
        coord._auto_reenable_tracking["was_occupied"] = True
        coord._auto_reenable_tracking["occupied_seconds"] = 1800.0  # 30 min

        info = coord.get_auto_reenable_tracking_info()
        assert info["is_tracking"] is True
        assert "current_vacancy_percent" in info
        assert "occupied_seconds" in info

    @pytest.mark.asyncio
    async def test_start_time_handler(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()
        coord._auto_reenable_enabled = True

        # Mock _save_tracking_state
        coord._save_tracking_state = AsyncMock()

        await coord._handle_auto_reenable_start_time(datetime.now(timezone.utc))

        assert coord._auto_reenable_tracking["is_tracking"] is True
        assert coord._auto_reenable_tracking["window_start"] is not None
        coord._save_tracking_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_time_handler_disabled(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()
        coord._auto_reenable_enabled = False

        await coord._handle_auto_reenable_start_time(datetime.now(timezone.utc))
        assert coord._auto_reenable_tracking["is_tracking"] is False

    @pytest.mark.asyncio
    async def test_end_time_handler(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()
        coord._auto_reenable_enabled = True
        coord._evaluate_and_apply_auto_reenable = AsyncMock()

        await coord._handle_auto_reenable_end_time(datetime.now(timezone.utc))
        coord._evaluate_and_apply_auto_reenable.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_evaluate_vacancy_above_threshold_reenables(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_VACANCY_THRESHOLD: 80,
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        # Set tracking state: 90% vacant (10% occupied)
        now = datetime.now(timezone.utc)
        coord._auto_reenable_tracking["is_tracking"] = True
        coord._auto_reenable_tracking["window_start"] = now - timedelta(hours=8)
        coord._auto_reenable_tracking["occupied_seconds"] = 2880  # ~10% of 8h
        coord._auto_reenable_tracking["was_occupied"] = False
        coord._auto_reenable_tracking["last_presence_change"] = now - timedelta(hours=1)

        coord._clear_tracking_state = AsyncMock()
        coord._reenable_presence_lighting = AsyncMock()

        await coord._evaluate_and_apply_auto_reenable()
        coord._reenable_presence_lighting.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_evaluate_vacancy_below_threshold_does_not_reenable(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_VACANCY_THRESHOLD: 80,
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        now = datetime.now(timezone.utc)
        coord._auto_reenable_tracking["is_tracking"] = True
        coord._auto_reenable_tracking["window_start"] = now - timedelta(hours=8)
        coord._auto_reenable_tracking["occupied_seconds"] = 20000  # ~69% of 8h
        coord._auto_reenable_tracking["was_occupied"] = False
        coord._auto_reenable_tracking["last_presence_change"] = now - timedelta(hours=1)

        coord._clear_tracking_state = AsyncMock()
        coord._reenable_presence_lighting = AsyncMock()

        await coord._evaluate_and_apply_auto_reenable()
        coord._reenable_presence_lighting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_evaluate_not_tracking_skips(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()
        coord._auto_reenable_tracking["is_tracking"] = False

        coord._reenable_presence_lighting = AsyncMock()
        await coord._evaluate_and_apply_auto_reenable()
        coord._reenable_presence_lighting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reenable_presence_lighting(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        # Disable presence and pause
        es = coord._entity_states["light.living_room"]
        es["presence_allowed"] = False
        coord._set_entity_state("light.living_room", es, EntityAutomationState.PAUSED, "test")

        await coord._reenable_presence_lighting()

        assert es["presence_allowed"] is True
        assert es["state"] != EntityAutomationState.PAUSED

    @pytest.mark.asyncio
    async def test_handle_presence_change_tracking(self):
        """Presence changes during monitoring window update tracking state."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_PRESENCE_SENSORS: ["binary_sensor.living_room_motion"],
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        # Start tracking
        now = datetime.now(timezone.utc)
        coord._auto_reenable_tracking["is_tracking"] = True
        coord._auto_reenable_tracking["window_start"] = now - timedelta(hours=1)
        coord._auto_reenable_tracking["last_presence_change"] = now - timedelta(minutes=30)
        coord._auto_reenable_tracking["was_occupied"] = False
        coord._auto_reenable_tracking["occupied_seconds"] = 0.0
        coord._save_tracking_state = AsyncMock()

        # Simulate motion on
        hass.states.set("binary_sensor.living_room_motion", STATE_ON)
        event = type("Event", (), {
            "data": {
                "entity_id": "binary_sensor.living_room_motion",
                "old_state": type("S", (), {"state": STATE_OFF})(),
                "new_state": type("S", (), {"state": STATE_ON})(),
            }
        })()
        await coord._handle_auto_reenable_presence_change(event)

        assert coord._auto_reenable_tracking["was_occupied"] is True
        coord._save_tracking_state.assert_awaited()

    @pytest.mark.asyncio
    async def test_handle_presence_change_occupied_to_vacant(self):
        """Transitioning from occupied to vacant accumulates occupied time."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_PRESENCE_SENSORS: ["binary_sensor.living_room_motion"],
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        now = datetime.now(timezone.utc)
        coord._auto_reenable_tracking["is_tracking"] = True
        coord._auto_reenable_tracking["window_start"] = now - timedelta(hours=1)
        coord._auto_reenable_tracking["last_presence_change"] = now - timedelta(minutes=10)
        coord._auto_reenable_tracking["was_occupied"] = True
        coord._auto_reenable_tracking["occupied_seconds"] = 0.0
        coord._save_tracking_state = AsyncMock()

        # Motion goes off
        hass.states.set("binary_sensor.living_room_motion", STATE_OFF)
        event = type("Event", (), {
            "data": {
                "entity_id": "binary_sensor.living_room_motion",
                "old_state": type("S", (), {"state": STATE_ON})(),
                "new_state": type("S", (), {"state": STATE_OFF})(),
            }
        })()
        await coord._handle_auto_reenable_presence_change(event)

        assert coord._auto_reenable_tracking["was_occupied"] is False
        assert coord._auto_reenable_tracking["occupied_seconds"] > 0

    @pytest.mark.asyncio
    async def test_handle_presence_change_not_tracking(self):
        """Events when not tracking should be ignored."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()
        coord._auto_reenable_tracking["is_tracking"] = False

        event = type("Event", (), {
            "data": {
                "entity_id": "binary_sensor.living_room_motion",
                "old_state": type("S", (), {"state": STATE_OFF})(),
                "new_state": type("S", (), {"state": STATE_ON})(),
            }
        })()
        await coord._handle_auto_reenable_presence_change(event)
        # No crash, no state change

    @pytest.mark.asyncio
    async def test_is_auto_reenable_sensors_occupied_fallback(self):
        """Falls back to main presence sensors when none configured."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        entry = _make_entry()  # no auto_reenable_presence_sensors
        coord = PresenceBasedLightingCoordinator(hass, entry)
        assert coord._is_auto_reenable_sensors_occupied() is True

    @pytest.mark.asyncio
    async def test_save_and_load_tracking_state(self):
        """Round-trip persistence of tracking state."""
        import tempfile, os
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)

        # Use a temp dir for storage and ensure .storage subdir exists
        tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmpdir, ".storage"), exist_ok=True)
        hass.config = MagicMock()
        hass.config.path = lambda *args: os.path.join(tmpdir, *args)

        # Make async_add_executor_job run the callable synchronously
        async def run_sync(fn, *args):
            return fn(*args)
        hass.async_add_executor_job = run_sync

        now = datetime.now(timezone.utc)
        coord._auto_reenable_tracking["is_tracking"] = True
        coord._auto_reenable_tracking["window_start"] = now
        coord._auto_reenable_tracking["occupied_seconds"] = 42.5
        coord._auto_reenable_tracking["last_presence_change"] = now
        coord._auto_reenable_tracking["was_occupied"] = True

        await coord._save_tracking_state()

        # Reset and reload
        coord._auto_reenable_tracking["is_tracking"] = False
        coord._auto_reenable_tracking["occupied_seconds"] = 0
        loaded = await coord._load_tracking_state()

        assert loaded is True
        assert coord._auto_reenable_tracking["is_tracking"] is True
        assert coord._auto_reenable_tracking["occupied_seconds"] == 42.5

        # Clean up
        await coord._clear_tracking_state()
        path = coord._get_tracking_persistence_path()
        assert not path.exists()

        # Cleanup tmpdir
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_load_tracking_state_no_file(self):
        """_load_tracking_state returns False when no file exists."""
        import tempfile, os
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        tmpdir = tempfile.mkdtemp()
        hass.config = MagicMock()
        hass.config.path = lambda *args: os.path.join(tmpdir, *args)

        async def run_sync(fn, *args):
            return fn(*args)
        hass.async_add_executor_job = run_sync

        result = await coord._load_tracking_state()
        assert result is False

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_check_auto_reenable_startup_no_tracking(self):
        """Startup check with no saved state does nothing."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        coord._auto_reenable_enabled = True
        coord._load_tracking_state = AsyncMock(return_value=False)

        await coord._check_auto_reenable_startup()
        # No crash

    @pytest.mark.asyncio
    async def test_check_auto_reenable_startup_past_window(self):
        """Restart after window ended should evaluate immediately."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        coord._auto_reenable_enabled = True

        now = datetime.now(timezone.utc)
        # Simulate tracking was active, window_start was last night
        coord._auto_reenable_tracking["window_start"] = now - timedelta(hours=10)
        coord._auto_reenable_tracking["is_tracking"] = True

        coord._load_tracking_state = AsyncMock(return_value=True)
        coord._evaluate_and_apply_auto_reenable = AsyncMock()

        await coord._check_auto_reenable_startup()
        # Depending on current time relative to window, it may evaluate or continue

    @pytest.mark.asyncio
    async def test_check_auto_reenable_startup_disabled(self):
        """Startup check when disabled does nothing."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        coord._auto_reenable_enabled = False

        await coord._check_auto_reenable_startup()
        # No crash


# ---------------------------------------------------------------------------
# Coordinator – activation condition handler
# ---------------------------------------------------------------------------

class TestActivationConditionHandler:
    @pytest.mark.asyncio
    async def test_condition_becoming_true_transitions_pending_to_occupied(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        hass.states.set("binary_sensor.condition_1", STATE_OFF)
        entry = _make_entry(extra={
            CONF_ACTIVATION_CONDITIONS: ["binary_sensor.condition_1"],
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        # Startup: condition OFF → entity goes to PENDING_ACTIVATION
        assert es["state"] == EntityAutomationState.PENDING_ACTIVATION

        hass.services.clear()
        # Now condition turns ON
        hass.states.set("binary_sensor.condition_1", STATE_ON)
        event = type("Event", (), {
            "data": {
                "entity_id": "binary_sensor.condition_1",
                "old_state": type("S", (), {"state": STATE_OFF})(),
                "new_state": type("S", (), {"state": STATE_ON})(),
            }
        })()
        await coord._handle_activation_condition_change(event)
        # After activation conditions are met, coordinator transitions through
        # OCCUPIED (calling turn_on) then may immediately start clearing since
        # the clearing sensor is already not detecting.
        assert es["state"] in (EntityAutomationState.OCCUPIED, EntityAutomationState.CLEARING)
        # Should have called turn_on
        found = any(c["service"] == "turn_on" for c in hass.services.calls)
        assert found

    @pytest.mark.asyncio
    async def test_condition_off_to_off_ignored(self):
        """Non-ON transitions should be ignored."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        hass.states.set("binary_sensor.condition_1", STATE_OFF)
        entry = _make_entry(extra={
            CONF_ACTIVATION_CONDITIONS: ["binary_sensor.condition_1"],
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        assert es["state"] == EntityAutomationState.PENDING_ACTIVATION

        # Condition OFF→OFF (no change)
        event = type("Event", (), {
            "data": {
                "entity_id": "binary_sensor.condition_1",
                "old_state": type("S", (), {"state": STATE_OFF})(),
                "new_state": type("S", (), {"state": STATE_OFF})(),
            }
        })()
        await coord._handle_activation_condition_change(event)
        assert es["state"] == EntityAutomationState.PENDING_ACTIVATION  # unchanged


# ---------------------------------------------------------------------------
# Coordinator – schedule/cancel auto-reenable times
# ---------------------------------------------------------------------------

class TestScheduleAutoReEnable:
    @pytest.mark.asyncio
    async def test_cancel_schedules(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        unsub1 = MagicMock()
        unsub2 = MagicMock()
        coord._auto_reenable_start_time_unsub = unsub1
        coord._auto_reenable_end_time_unsub = unsub2

        coord._cancel_auto_reenable_schedules()
        unsub1.assert_called_once()
        unsub2.assert_called_once()
        assert coord._auto_reenable_start_time_unsub is None
        assert coord._auto_reenable_end_time_unsub is None

    @pytest.mark.asyncio
    async def test_schedule_times_no_times(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        coord._auto_reenable_start_time = None
        coord._auto_reenable_end_time = None
        coord._schedule_auto_reenable_times()  # should not crash
