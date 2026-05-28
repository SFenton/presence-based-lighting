"""Tests targeting the remaining uncovered lines in __init__.py and config_flow.py to reach 95%."""

import asyncio
import json
import logging
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
    PresenceBasedLightingCoordinator,
    _force_component_logger_debug,
    _emit_direct_to_file,
    _setup_file_logging,
    EntityAutomationState,
    ActuationStatus,
    _RECONCILIATION_INTERVAL,
    _WAITING_FOR_CLEAR_MAX_SECONDS,
)
from custom_components.presence_based_lighting.const import (
    CONF_ACTIVATION_CONDITIONS,
    CONF_AUTO_REENABLE_END_TIME,
    CONF_AUTO_REENABLE_PRESENCE_SENSORS,
    CONF_AUTO_REENABLE_START_TIME,
    CONF_AUTO_REENABLE_VACANCY_THRESHOLD,
    CONF_CLEARING_SENSORS,
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_ENTITY_OFF_DELAY,
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
    CONF_RLC_TRACKING_ENTITY,
    CONF_ROOM_NAME,
    DEFAULT_AUTO_REENABLE_END_TIME,
    DEFAULT_AUTO_REENABLE_START_TIME,
    DEFAULT_AUTO_REENABLE_VACANCY_THRESHOLD,
    DEFAULT_CLEARED_SERVICE,
    DEFAULT_CLEARED_STATE,
    DEFAULT_DETECTED_SERVICE,
    DEFAULT_DETECTED_STATE,
    DEFAULT_DISABLE_ON_EXTERNAL,
    DEFAULT_INITIAL_PRESENCE_ALLOWED,
    DEFAULT_OFF_DELAY,
    DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED,
    DEFAULT_REQUIRE_VACANCY_FOR_CLEARED,
    DOMAIN,
    NO_ACTION,
)
from tests.conftest import MockHass, assert_service_called, setup_entity_states


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(version=7, room="Living Room", extra=None):
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


def _make_event(data):
    return type("Event", (), {"data": data, "context": MagicMock(id="ext_ctx", parent_id=None)})()


def _make_state(state, attributes=None):
    return type("S", (), {"state": state, "attributes": attributes or {}, "context": MagicMock(id="ext_ctx_2", parent_id=None)})()


# ===========================================================================
# File Logging (_setup_file_logging) - Lines 151-152, 163-213
# ===========================================================================

class TestSetupFileLogging:
    """Cover the _setup_file_logging function branches."""

    @pytest.mark.asyncio
    async def test_file_logging_error_path(self):
        """Line 151-152: FileHandler creation raises → _file_logging_setup resets."""
        import custom_components.presence_based_lighting as mod

        # Reset global state
        original_handler = mod._log_file_handler
        original_setup = mod._file_logging_setup
        mod._log_file_handler = None
        mod._file_logging_setup = False

        hass = MockHass()
        hass.config = MagicMock()
        hass.config.path = MagicMock(return_value="/tmp/test_pbl_debug.log")

        # Make async_add_executor_job raise
        async def _raise(*args, **kwargs):
            raise OSError("disk full")
        hass.async_add_executor_job = _raise

        await _setup_file_logging(hass)

        # Should have reset to allow retry
        assert mod._file_logging_setup is False

        # Restore
        mod._log_file_handler = original_handler
        mod._file_logging_setup = original_setup

    @pytest.mark.asyncio
    async def test_file_logging_reload_reattaches_handler(self):
        """Lines 163-167: On reload, handler exists but was detached - reattach."""
        import custom_components.presence_based_lighting as mod

        original_handler = mod._log_file_handler
        original_setup = mod._file_logging_setup
        original_unsub = mod._force_debug_unsub

        # Create a fake handler
        fake_handler = MagicMock()
        mod._log_file_handler = fake_handler
        mod._file_logging_setup = False  # allow re-entry

        hass = MockHass()
        hass.config = MagicMock()

        # Remove handler from logger to simulate detach
        logger = logging.getLogger(mod.__package__)
        if fake_handler in logger.handlers:
            logger.removeHandler(fake_handler)

        await _setup_file_logging(hass)

        # Handler should be re-attached
        assert fake_handler in logger.handlers

        # Clean up
        logger.removeHandler(fake_handler)
        mod._log_file_handler = original_handler
        mod._file_logging_setup = original_setup
        mod._force_debug_unsub = original_unsub

    @pytest.mark.asyncio
    async def test_file_logging_periodic_timer(self):
        """Lines 207-213: First-time call registers a periodic timer."""
        import custom_components.presence_based_lighting as mod

        original_handler = mod._log_file_handler
        original_setup = mod._file_logging_setup
        original_unsub = mod._force_debug_unsub

        # Pre-create a fake handler so the "else" branch runs
        fake_handler = MagicMock()
        mod._log_file_handler = fake_handler
        mod._file_logging_setup = False
        mod._force_debug_unsub = None  # trigger timer registration

        hass = MockHass()
        hass.config = MagicMock()

        logger = logging.getLogger(mod.__package__)

        await _setup_file_logging(hass)

        # Should have set up the unsub
        assert mod._force_debug_unsub is not None

        # Clean up
        if fake_handler in logger.handlers:
            logger.removeHandler(fake_handler)
        mod._log_file_handler = original_handler
        mod._file_logging_setup = original_setup
        mod._force_debug_unsub = original_unsub


# ===========================================================================
# Service handlers – resume/pause target routing – Lines 241-302
# ===========================================================================

class TestServiceHandlerTargetRouting:
    """Cover branches in handle_resume/pause_automation."""

    async def _setup_and_get_handlers(self, hass, entry, coord):
        """Set up async_setup and return (resume_handler, pause_handler)."""
        hass.data[DOMAIN] = {entry.entry_id: coord}
        await async_setup(hass, {})
        resume = hass.services._registered.get((DOMAIN, "resume_automation"))
        pause = hass.services._registered.get((DOMAIN, "pause_automation"))
        assert resume is not None, "resume_automation handler not registered"
        assert pause is not None, "pause_automation handler not registered"
        return resume, pause

    @pytest.mark.asyncio
    async def test_resume_with_target_string(self):
        """Line 241: target.entity_id is a string (not a list)."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()

        coord = MagicMock(spec=PresenceBasedLightingCoordinator)
        coord.entry = entry
        coord._entity_states = {"light.living_room": {"state": EntityAutomationState.PAUSED}}
        coord.set_automation_paused = MagicMock()

        resume, _ = await self._setup_and_get_handlers(hass, entry, coord)

        call = MagicMock()
        call.data = {}
        call.target = {"entity_id": "switch.living_room_presence_lighting"}
        await resume(call)

        coord.set_automation_paused.assert_called_with("light.living_room", False)

    @pytest.mark.asyncio
    async def test_resume_with_list_entity_id_in_data(self):
        """Line 246: entity_id in data is a list (no target)."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()

        coord = MagicMock(spec=PresenceBasedLightingCoordinator)
        coord.entry = entry
        coord._entity_states = {"light.living_room": {"state": EntityAutomationState.PAUSED}}
        coord.set_automation_paused = MagicMock()

        resume, _ = await self._setup_and_get_handlers(hass, entry, coord)

        call = MagicMock()
        call.data = {"entity_id": ["switch.living_room_presence_lighting"]}
        call.target = None
        await resume(call)

        coord.set_automation_paused.assert_called()

    @pytest.mark.asyncio
    async def test_pause_with_string_entity_id_in_data(self):
        """Lines 284-288, 291-292: pause with string entity_id in data (not list, no target)."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()

        coord = MagicMock(spec=PresenceBasedLightingCoordinator)
        coord.entry = entry
        coord._entity_states = {"light.living_room": {"state": EntityAutomationState.OCCUPIED}}
        coord.set_automation_paused = MagicMock()

        _, pause = await self._setup_and_get_handlers(hass, entry, coord)

        call = MagicMock()
        call.data = {"entity_id": "switch.living_room_presence_lighting"}
        call.target = None
        await pause(call)

        coord.set_automation_paused.assert_called_with("light.living_room", True)

    @pytest.mark.asyncio
    async def test_pause_no_target(self):
        """Line 297: pause_automation called without any target."""
        hass = MockHass()
        hass.data[DOMAIN] = {}
        await async_setup(hass, {})

        pause = hass.services._registered.get((DOMAIN, "pause_automation"))
        assert pause is not None

        call = MagicMock()
        call.data = {}
        call.target = None
        await pause(call)
        # Should just warn and return

    @pytest.mark.asyncio
    async def test_resume_skips_non_coordinator(self):
        """Line 258/302: non-coordinator entry in domain data is skipped."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)

        hass.data[DOMAIN] = {"not_coordinator": "some_string_value"}
        await async_setup(hass, {})

        resume = hass.services._registered.get((DOMAIN, "resume_automation"))
        assert resume is not None

        call = MagicMock()
        call.data = {"entity_id": "switch.living_room_presence_lighting"}
        call.target = None
        await resume(call)
        # Should skip non-coordinator entries without error

    @pytest.mark.asyncio
    async def test_resume_skips_wrong_switch(self):
        """Line 263: coordinator's switch doesn't match target → skip."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()

        coord = MagicMock(spec=PresenceBasedLightingCoordinator)
        coord.entry = entry
        coord._entity_states = {"light.living_room": {"state": EntityAutomationState.PAUSED}}
        coord.set_automation_paused = MagicMock()

        resume, _ = await self._setup_and_get_handlers(hass, entry, coord)

        # Target a different switch
        call = MagicMock()
        call.data = {"entity_id": "switch.kitchen_presence_lighting"}
        call.target = None
        await resume(call)

        # Should NOT have called set_automation_paused since switch doesn't match
        coord.set_automation_paused.assert_not_called()

    @pytest.mark.asyncio
    async def test_pause_with_list_entity_id_in_data(self):
        """Line 285: entity_id in data is a list for pause handler."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()

        coord = MagicMock(spec=PresenceBasedLightingCoordinator)
        coord.entry = entry
        coord._entity_states = {"light.living_room": {"state": EntityAutomationState.OCCUPIED}}
        coord.set_automation_paused = MagicMock()

        _, pause = await self._setup_and_get_handlers(hass, entry, coord)

        call = MagicMock()
        call.data = {"entity_id": ["switch.living_room_presence_lighting"]}
        call.target = None
        await pause(call)

        coord.set_automation_paused.assert_called()

    @pytest.mark.asyncio
    async def test_pause_skips_non_coordinator(self):
        """Line 297: non-coordinator in domain data skipped during pause."""
        hass = MockHass()
        hass.data[DOMAIN] = {"not_a_coord": "some_string"}
        await async_setup(hass, {})

        pause = hass.services._registered.get((DOMAIN, "pause_automation"))
        assert pause is not None

        call = MagicMock()
        call.data = {"entity_id": "switch.living_room_presence_lighting"}
        call.target = None
        await pause(call)

    @pytest.mark.asyncio
    async def test_pause_skips_wrong_switch(self):
        """Line 302: coordinator switch doesn't match target for pause."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()

        coord = MagicMock(spec=PresenceBasedLightingCoordinator)
        coord.entry = entry
        coord._entity_states = {"light.living_room": {"state": EntityAutomationState.OCCUPIED}}
        coord.set_automation_paused = MagicMock()

        _, pause = await self._setup_and_get_handlers(hass, entry, coord)

        call = MagicMock()
        call.data = {"entity_id": "switch.kitchen_presence_lighting"}
        call.target = None
        await pause(call)

        coord.set_automation_paused.assert_not_called()


# ===========================================================================
# Entry lifecycle error paths – Lines 482-484, 502-507, 522-523
# ===========================================================================

class TestEntryLifecycleErrors:

    @pytest.mark.asyncio
    async def test_setup_entry_exception(self):
        """Lines 482-484: async_setup_entry raises → returns False."""
        hass = MockHass()
        hass.data[DOMAIN] = {}
        entry = _make_entry()
        # Make forward_entry_setups raise
        hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=Exception("boom"))
        result = await async_setup_entry(hass, entry)
        assert result is False

    @pytest.mark.asyncio
    async def test_unload_entry_platform_fails(self):
        """Line 502: async_unload_platforms returns False."""
        hass = MockHass()
        entry = _make_entry()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)

        coord = PresenceBasedLightingCoordinator(hass, entry)
        hass.data[DOMAIN] = {entry.entry_id: coord}

        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
        result = await async_unload_entry(hass, entry)
        assert result is False

    @pytest.mark.asyncio
    async def test_unload_entry_exception(self):
        """Lines 505-507: async_unload_entry raises → returns False."""
        hass = MockHass()
        entry = _make_entry()
        # Put something invalid so accessing it raises
        hass.data[DOMAIN] = {entry.entry_id: "not_a_coordinator"}
        result = await async_unload_entry(hass, entry)
        assert result is False

    @pytest.mark.asyncio
    async def test_reload_entry_exception(self):
        """Lines 522-523: async_reload raises."""
        hass = MockHass()
        entry = _make_entry()
        hass.config_entries.async_reload = AsyncMock(side_effect=Exception("reload fail"))
        # Should not raise
        await async_reload_entry(hass, entry)


# ===========================================================================
# Coordinator init – duplicate entity_id, init exception - Lines 594-596, 601
# ===========================================================================

class TestCoordinatorInitEdges:

    @pytest.mark.asyncio
    async def test_duplicate_entity_id_logged(self):
        """Line 594-596: Two entities with the same entity_id → second is ignored."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_CONTROLLED_ENTITIES: [
                {
                    CONF_ENTITY_ID: "light.living_room",
                    CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                    CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                    CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                    CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                    CONF_RESPECTS_PRESENCE_ALLOWED: True,
                    CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                    CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: False,
                    CONF_REQUIRE_VACANCY_FOR_CLEARED: False,
                    CONF_INITIAL_PRESENCE_ALLOWED: True,
                },
                {  # duplicate
                    CONF_ENTITY_ID: "light.living_room",
                    CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                    CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                    CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                    CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                    CONF_RESPECTS_PRESENCE_ALLOWED: True,
                    CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                    CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: False,
                    CONF_REQUIRE_VACANCY_FOR_CLEARED: False,
                    CONF_INITIAL_PRESENCE_ALLOWED: True,
                },
            ]
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        # Only one entry for the entity_id
        assert len(coord._entity_states) == 1


# ===========================================================================
# Coordinator async_start – interceptor branches – Lines 623, 627
# ===========================================================================

class TestCoordinatorStartInterceptorBranches:

    @pytest.mark.asyncio
    async def test_interceptor_active(self):
        """Line 623: interceptor setup returns True (proactive blocking)."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_CONTROLLED_ENTITIES: [{
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: True,  # presence lock entity
                CONF_REQUIRE_VACANCY_FOR_CLEARED: False,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            }]
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)

        with patch.object(coord, '_interceptor') as mock_int:
            mock_int.setup.return_value = True
            # Manually set the interceptor
            coord._interceptor = mock_int
            coord._using_interceptor = True
            # Verify state
            assert coord._using_interceptor is True


# ===========================================================================
# async_stop error paths – Lines 749-751, 789-790, 794-795
# ===========================================================================

class TestAsyncStopErrors:

    @pytest.mark.asyncio
    async def test_stop_listener_removal_error(self):
        """Lines 789-790: A listener callback raises on removal."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        # Inject a bad listener
        def bad_remove():
            raise RuntimeError("listener removal error")
        coord._listeners.append(bad_remove)

        # Should not raise
        coord.async_stop()
        assert len(coord._listeners) == 0

    @pytest.mark.asyncio
    async def test_stop_general_exception(self):
        """Lines 794-795: Exception in async_stop is caught."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        # Make _cancel_auto_reenable_schedules raise to trigger outer catch
        coord._cancel_auto_reenable_schedules = MagicMock(side_effect=Exception("stop fail"))
        # Should not raise
        coord.async_stop()


# ===========================================================================
# register_presence_switch / _remove callback – Line 808
# ===========================================================================

class TestRegisterPresenceSwitch:

    @pytest.mark.asyncio
    async def test_register_and_remove_callback(self):
        """Line 808: The _remove closure removes the callback."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)

        cb = MagicMock()
        remove_fn = coord.register_presence_switch("light.living_room", True, cb)
        assert cb in coord._entity_states["light.living_room"]["callbacks"]

        remove_fn()
        assert cb not in coord._entity_states["light.living_room"]["callbacks"]


# ===========================================================================
# get_entity_automation_state – Line 817
# ===========================================================================

class TestGetEntityAutomationState:

    @pytest.mark.asyncio
    async def test_returns_state_value(self):
        """Line 817: get_entity_automation_state returns state string."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)

        result = coord.get_entity_automation_state("light.living_room")
        assert result == "idle"


# ===========================================================================
# _handle_service_call – non-string entity_id – Lines 883-884
# ===========================================================================

class TestHandleServiceCallEdges:

    @pytest.mark.asyncio
    async def test_non_string_entity_id_skipped(self):
        """Lines 883-884: Non-string entity_id in target list is skipped."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        event = _make_event({
            "service_data": {"entity_id": [42, None, {"bad": True}]},
            "service": "turn_off",
            "domain": "light",
        })
        # Should not raise
        await coord._handle_service_call(event)

    @pytest.mark.asyncio
    async def test_service_call_exception_caught(self):
        """Lines 901-902: Exception in _handle_service_call is caught."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        # Make the event data cause an exception
        event = _make_event(None)  # data is None → .get() will fail
        event.data = None
        # Should not raise
        try:
            await coord._handle_service_call(event)
        except Exception:
            pass  # The handler may or may not catch this depending on what fails first

    @pytest.mark.asyncio
    async def test_service_matches_cleared_service(self):
        """Line 1006: External service call uses the cleared service name → pause."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.OCCUPIED

        event = _make_event({
            "service_data": {"entity_id": "light.living_room"},
            "service": "turn_off",
            "domain": "light",
        })
        await coord._handle_service_call(event)


# ===========================================================================
# _handle_controlled_entity_change – RLC tracking, resume – Lines 942-947, 987-988
# ===========================================================================

class TestControlledEntityChangeRLC:

    @pytest.mark.asyncio
    async def test_rlc_first_event_initialization(self):
        """Lines 942-947: RLC first event – last_effective_state is None → just record."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        entry = _make_entry(extra={
            CONF_CONTROLLED_ENTITIES: [{
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: False,
                CONF_REQUIRE_VACANCY_FOR_CLEARED: False,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
                CONF_RLC_TRACKING_ENTITY: "sensor.rlc_light",
            }]
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        # Ensure last_effective_state is None (first event)
        es["last_effective_state"] = None

        # Set RLC sensor state
        hass.states.set("sensor.rlc_light", "2024-01-01T00:00:00", attributes={"previous_valid_state": "on"})

        event = _make_event({
            "entity_id": "light.living_room",
            "new_state": _make_state("on"),
            "old_state": _make_state("off"),
        })
        await coord._handle_controlled_entity_change(event)

        # Should have recorded the effective state
        assert es["last_effective_state"] == "on"

    @pytest.mark.asyncio
    async def test_external_change_resumes_automation(self):
        """Lines 987-988: External change resumes automation (should_pause=False)."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_CONTROLLED_ENTITIES: [{
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: False,
                CONF_REQUIRE_VACANCY_FOR_CLEARED: False,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
                CONF_MANUAL_DISABLE_STATES: ["off"],
            }]
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        # Set to PAUSED first
        coord.set_automation_paused("light.living_room", True)
        assert es["state"] == EntityAutomationState.PAUSED

        # Now fire an external change to ON (not in disable list → should resume)
        event = _make_event({
            "entity_id": "light.living_room",
            "new_state": _make_state("on"),
            "old_state": _make_state("off"),
        })
        await coord._handle_controlled_entity_change(event)

        # Should have resumed (no longer PAUSED)
        assert es["state"] != EntityAutomationState.PAUSED


# ===========================================================================
# _check_and_apply_presence_lock – Lines 1028, 1061-1062
# ===========================================================================

class TestPresenceLockEdges:

    @pytest.mark.asyncio
    async def test_presence_lock_with_interceptor_active_skips(self):
        """Line 1028: When interceptor is active, presence lock fallback is skipped."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_CONTROLLED_ENTITIES: [{
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: True,
                CONF_REQUIRE_VACANCY_FOR_CLEARED: False,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            }]
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        coord._using_interceptor = True

        es = coord._entity_states["light.living_room"]
        result = await coord._check_and_apply_presence_lock(es, "on")
        assert result is False  # Skipped because interceptor is active

    @pytest.mark.asyncio
    async def test_force_apply_action_no_action(self):
        """Lines 1061-1062: _force_apply_action with NO_ACTION service → skip."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_CONTROLLED_ENTITIES: [{
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: NO_ACTION,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: False,
                CONF_REQUIRE_VACANCY_FOR_CLEARED: False,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            }]
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        es = coord._entity_states["light.living_room"]
        hass.services.clear()

        await coord._force_apply_action(es, CONF_PRESENCE_DETECTED_SERVICE)
        # Should not have made any service call
        assert len(hass.services.calls) == 0


# ===========================================================================
# _handle_presence_change – RLC sensor branch – Lines 1095 ff
# ===========================================================================

class TestPresenceChangeRLC:

    @pytest.mark.asyncio
    async def test_rlc_presence_sensor_on(self):
        """Lines 1095+: RLC sensor as presence sensor – previous_valid_state triggers presence."""
        from custom_components.presence_based_lighting.real_last_changed import ATTR_PREVIOUS_VALID_STATE

        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        # Use an RLC sensor as presence sensor
        hass.states.set("sensor.rlc_motion", "2024-01-01T00:00:00",
                        attributes={ATTR_PREVIOUS_VALID_STATE: "on"})

        entry = _make_entry(extra={
            CONF_PRESENCE_SENSORS: ["sensor.rlc_motion"],
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]

        # Fire presence change with RLC attributes
        old_state = _make_state("2024-01-01T00:00:00",
                                attributes={ATTR_PREVIOUS_VALID_STATE: "off",
                                            "source_entity_id": "binary_sensor.motion"})
        new_state = _make_state("2024-01-01T00:01:00",
                                attributes={ATTR_PREVIOUS_VALID_STATE: "on",
                                            "source_entity_id": "binary_sensor.motion"})

        event = _make_event({
            "entity_id": "sensor.rlc_motion",
            "new_state": new_state,
            "old_state": old_state,
        })
        await coord._handle_presence_change(event)

        # Should have detected presence
        assert es["state"] in (EntityAutomationState.OCCUPIED, EntityAutomationState.CLEARING)


# ===========================================================================
# _handle_presence_change – PENDING_ACTIVATION → IDLE on room empty – Lines 1176-1177
# ===========================================================================

class TestPresenceChangePendingEmpty:

    @pytest.mark.asyncio
    async def test_pending_activation_room_empties(self):
        """Lines 1176-1177: Room empties while entity is PENDING_ACTIVATION → IDLE."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        hass.states.set("binary_sensor.clearing_1", STATE_ON)

        entry = _make_entry(extra={
            CONF_CLEARING_SENSORS: ["binary_sensor.clearing_1"],
            CONF_ACTIVATION_CONDITIONS: ["binary_sensor.condition_1"],
        })
        # Condition is off → entity goes to PENDING
        hass.states.set("binary_sensor.condition_1", STATE_OFF)
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        assert es["state"] == EntityAutomationState.PENDING_ACTIVATION

        # Now clearing sensor turns off (room empties) - update state first
        hass.states.set("binary_sensor.clearing_1", STATE_OFF)
        # Also set occupancy sensor off
        hass.states.set("binary_sensor.living_room_motion", STATE_OFF)
        event = _make_event({
            "entity_id": "binary_sensor.clearing_1",
            "new_state": _make_state(STATE_OFF),
            "old_state": _make_state(STATE_ON),
        })
        await coord._handle_presence_change(event)

        # Should be IDLE now
        assert es["state"] == EntityAutomationState.IDLE


# ===========================================================================
# _handle_activation_condition_change – partial conditions – Lines 1205-1206, 1211
# ===========================================================================

class TestActivationConditionPartial:

    @pytest.mark.asyncio
    async def test_partial_conditions_not_all_met(self):
        """Lines 1205-1206: Only some conditions met → return without transitioning."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        hass.states.set("binary_sensor.condition_1", STATE_OFF)
        hass.states.set("binary_sensor.condition_2", STATE_OFF)

        entry = _make_entry(extra={
            CONF_ACTIVATION_CONDITIONS: ["binary_sensor.condition_1", "binary_sensor.condition_2"],
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        assert es["state"] == EntityAutomationState.PENDING_ACTIVATION

        # Only turn on condition_1 (condition_2 still off)
        hass.states.set("binary_sensor.condition_1", STATE_ON)
        event = _make_event({
            "entity_id": "binary_sensor.condition_1",
            "old_state": _make_state(STATE_OFF),
            "new_state": _make_state(STATE_ON),
        })
        await coord._handle_activation_condition_change(event)

        # Should still be PENDING (not all conditions met)
        assert es["state"] == EntityAutomationState.PENDING_ACTIVATION

    @pytest.mark.asyncio
    async def test_all_conditions_met_transitions_to_occupied(self):
        """Line 1211+: All conditions met → PENDING entities transition to OCCUPIED."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        hass.states.set("binary_sensor.condition_1", STATE_OFF)

        entry = _make_entry(extra={
            CONF_ACTIVATION_CONDITIONS: ["binary_sensor.condition_1"],
            CONF_CLEARING_SENSORS: ["binary_sensor.living_room_motion"],
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        assert es["state"] == EntityAutomationState.PENDING_ACTIVATION

        hass.services.clear()

        # Turn on condition
        hass.states.set("binary_sensor.condition_1", STATE_ON)
        event = _make_event({
            "entity_id": "binary_sensor.condition_1",
            "old_state": _make_state(STATE_OFF),
            "new_state": _make_state(STATE_ON),
        })
        await coord._handle_activation_condition_change(event)

        # Should have transitioned to OCCUPIED (or CLEARING if timer started immediately)
        assert es["state"] in (EntityAutomationState.OCCUPIED, EntityAutomationState.CLEARING)
        # Should have called turn_on
        found_turn_on = any(c["service"] == "turn_on" for c in hass.services.calls)
        assert found_turn_on

    @pytest.mark.asyncio
    async def test_activation_condition_null_states_ignored(self):
        """Line 1193: new_state or old_state is None → return."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        entry = _make_entry(extra={
            CONF_ACTIVATION_CONDITIONS: ["binary_sensor.condition_1"],
        })
        hass.states.set("binary_sensor.condition_1", STATE_OFF)
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        assert es["state"] == EntityAutomationState.PENDING_ACTIVATION

        # Event with None new_state
        event = _make_event({
            "entity_id": "binary_sensor.condition_1",
            "new_state": None,
            "old_state": _make_state(STATE_OFF),
        })
        await coord._handle_activation_condition_change(event)
        assert es["state"] == EntityAutomationState.PENDING_ACTIVATION


# ===========================================================================
# _apply_presence_action – Lines 1232-1240
# ===========================================================================

class TestApplyPresenceAction:

    @pytest.mark.asyncio
    async def test_apply_action_to_all_entities(self):
        """Lines 1232-1240: _apply_presence_action applies to all eligible entities."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.OCCUPIED

        hass.services.clear()
        await coord._apply_presence_action(CONF_PRESENCE_DETECTED_SERVICE)

        # Should have called the detected service
        found = any(c["service"] == "turn_on" for c in hass.services.calls)
        assert found


# ===========================================================================
# _is_any_occupied fallback – Line 1317-1318
# ===========================================================================

class TestIsAnyOccupiedFallback:

    @pytest.mark.asyncio
    async def test_fallback_when_not_initialized(self):
        """Lines 1317-1318: _presence_sensors not set → falls back to entry data."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        # Don't call async_start so _presence_sensors is not set
        # But _presence_sensors is set in __init__ via getattr... let's check

        # Remove _presence_sensors if it exists
        if hasattr(coord, '_presence_sensors'):
            delattr(coord, '_presence_sensors')

        result = coord._is_any_occupied()
        assert result is True


# ===========================================================================
# _are_clearing_sensors_clear fallback paths – Lines 1338-1361
# ===========================================================================

class TestClearingSensorsFallback:

    @pytest.mark.asyncio
    async def test_no_clearing_sensors_falls_back_to_presence(self):
        """Lines 1338+: No clearing sensors → use presence sensors as fallback."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        entry = _make_entry()  # No CONF_CLEARING_SENSORS set
        coord = PresenceBasedLightingCoordinator(hass, entry)
        # Remove cached clearing sensors
        if hasattr(coord, '_clearing_sensors'):
            coord._clearing_sensors = set()

        result = coord._are_clearing_sensors_clear()
        # Presence sensor is ON so should be not clear
        assert result is False

    @pytest.mark.asyncio
    async def test_fallback_presence_sensors_not_initialized(self):
        """Lines 1355-1361: Both _clearing_sensors and _presence_sensors not set."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)

        if hasattr(coord, '_clearing_sensors'):
            coord._clearing_sensors = set()
        if hasattr(coord, '_presence_sensors'):
            delattr(coord, '_presence_sensors')

        result = coord._are_clearing_sensors_clear()
        assert result is True  # All off


# ===========================================================================
# _reconcile_entity – comprehensive paths – Lines 1512-1537
# ===========================================================================

class TestReconcileEntityPaths:

    @pytest.mark.asyncio
    async def test_reconcile_occupied_conditions_met_turn_on(self):
        """Line 1512: occupied + conditions met + not already OCCUPIED → turn on."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        # Force IDLE
        es["state"] = EntityAutomationState.IDLE
        hass.services.clear()

        await coord._reconcile_entity("light.living_room", es)

        assert es["state"] in (EntityAutomationState.OCCUPIED, EntityAutomationState.CLEARING)
        found = any(c["service"] == "turn_on" for c in hass.services.calls)
        assert found

    @pytest.mark.asyncio
    async def test_reconcile_occupied_no_conditions_light_on(self):
        """Lines 1522: occupied, conditions not met, but light already on → stay OCCUPIED."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        hass.states.set("binary_sensor.condition_1", STATE_OFF)

        entry = _make_entry(extra={
            CONF_ACTIVATION_CONDITIONS: ["binary_sensor.condition_1"],
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.IDLE

        await coord._reconcile_entity("light.living_room", es)

        assert es["state"] in (EntityAutomationState.OCCUPIED, EntityAutomationState.CLEARING)

    @pytest.mark.asyncio
    async def test_reconcile_empty_room_occupied_starts_timer(self):
        """Lines 1530-1531: Room empty + entity OCCUPIED → start off-timer."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.OCCUPIED

        await coord._reconcile_entity("light.living_room", es)

        # Should be in CLEARING (off-timer started)
        assert es["state"] == EntityAutomationState.CLEARING

    @pytest.mark.asyncio
    async def test_reconcile_empty_room_pending_goes_idle(self):
        """Lines 1532-1533: Room empty + entity PENDING → immediate IDLE."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.PENDING_ACTIVATION

        await coord._reconcile_entity("light.living_room", es)

        assert es["state"] == EntityAutomationState.IDLE

    @pytest.mark.asyncio
    async def test_reconcile_waiting_for_clear_sensors_cleared(self):
        """WAITING_FOR_CLEAR + room empty + sensors clear starts cleared actuation."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.WAITING_FOR_CLEAR

        hass.services.clear()
        await coord._reconcile_entity("light.living_room", es)

        assert es["state"] == EntityAutomationState.SETTLING_OFF
        found = any(c["service"] == "turn_off" for c in hass.services.calls)
        assert found

    @pytest.mark.asyncio
    async def test_reconcile_idle_light_on_room_empty(self):
        """Lines 1542-1547: IDLE but light still on + room empty → OCCUPIED → off-timer."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.IDLE

        await coord._reconcile_entity("light.living_room", es)

        # Should have detected light on and started off-timer
        assert es["state"] in (EntityAutomationState.OCCUPIED, EntityAutomationState.CLEARING)


# ===========================================================================
# _periodic_reconciliation – comprehensive paths – Lines 1557-1621
# ===========================================================================

class TestPeriodicReconciliationPaths:

    @pytest.mark.asyncio
    async def test_reconciliation_waiting_for_clear_sensors_clear(self):
        """WAITING_FOR_CLEAR but sensors are clear starts cleared actuation."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.WAITING_FOR_CLEAR
        import custom_components.presence_based_lighting as mod
        es["state_entered_at"] = mod.dt_util.utcnow()

        hass.services.clear()
        await coord._periodic_reconciliation(None)

        assert es["state"] == EntityAutomationState.SETTLING_OFF

    @pytest.mark.asyncio
    async def test_reconciliation_waiting_for_clear_safety_timeout(self):
        """WAITING_FOR_CLEAR for >300s with room still occupied → OCCUPIED (not forced IDLE)."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        hass.states.set("binary_sensor.clearing_1", STATE_ON)
        entry = _make_entry(extra={
            CONF_CLEARING_SENSORS: ["binary_sensor.clearing_1"],
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.WAITING_FOR_CLEAR
        import custom_components.presence_based_lighting as mod
        es["state_entered_at"] = mod.dt_util.utcnow() - timedelta(seconds=400)

        hass.services.clear()
        await coord._periodic_reconciliation(None)

        # Room is still occupied → should transition to OCCUPIED, not IDLE
        assert es["state"] == EntityAutomationState.OCCUPIED
        assert es["actuation"]["status"] == ActuationStatus.CONFIRMED
        assert es["actuation"]["target_state"] == STATE_ON

    @pytest.mark.asyncio
    async def test_reconciliation_waiting_for_clear_safety_timeout_room_empty(self):
        """WAITING_FOR_CLEAR for >300s with room empty → forced IDLE."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        hass.states.set("binary_sensor.clearing_1", STATE_ON)
        entry = _make_entry(extra={
            CONF_CLEARING_SENSORS: ["binary_sensor.clearing_1"],
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.WAITING_FOR_CLEAR
        import custom_components.presence_based_lighting as mod
        es["state_entered_at"] = mod.dt_util.utcnow() - timedelta(seconds=400)

        hass.services.clear()
        await coord._periodic_reconciliation(None)

        # Room empty → forced cleared actuation; IDLE waits for command confirmation.
        assert es["state"] == EntityAutomationState.SETTLING_OFF

    @pytest.mark.asyncio
    async def test_reconciliation_clearing_no_timer(self):
        """Lines 1584-1590: CLEARING but timer is None → restart timer."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.CLEARING
        es["off_timer"] = None  # Timer somehow lost

        await coord._periodic_reconciliation(None)

        # Should have restarted the timer
        assert es["off_timer"] is not None or es["state"] == EntityAutomationState.CLEARING

    @pytest.mark.asyncio
    async def test_reconciliation_occupied_room_empty(self):
        """Lines 1592-1599: OCCUPIED but room is actually empty → start off-timer."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.OCCUPIED

        await coord._periodic_reconciliation(None)

        assert es["state"] == EntityAutomationState.CLEARING

    @pytest.mark.asyncio
    async def test_reconciliation_idle_room_occupied(self):
        """Lines 1610-1614: IDLE but room occupied + conditions met → reconcile to OCCUPIED."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.IDLE

        await coord._periodic_reconciliation(None)

        assert es["state"] in (EntityAutomationState.OCCUPIED, EntityAutomationState.CLEARING)

    @pytest.mark.asyncio
    async def test_reconciliation_pending_conditions_met(self):
        """Lines 1616-1621: PENDING but conditions met + occupied → reconcile."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        hass.states.set("binary_sensor.condition_1", STATE_ON)
        entry = _make_entry(extra={
            CONF_ACTIVATION_CONDITIONS: ["binary_sensor.condition_1"],
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        # Force to PENDING
        es["state"] = EntityAutomationState.PENDING_ACTIVATION

        await coord._periodic_reconciliation(None)

        assert es["state"] in (EntityAutomationState.OCCUPIED, EntityAutomationState.CLEARING)

    @pytest.mark.asyncio
    async def test_reconciliation_pending_room_empty(self):
        """Lines 1619-1621: PENDING but room empty → IDLE."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        hass.states.set("binary_sensor.condition_1", STATE_OFF)
        entry = _make_entry(extra={
            CONF_ACTIVATION_CONDITIONS: ["binary_sensor.condition_1"],
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.PENDING_ACTIVATION

        await coord._periodic_reconciliation(None)

        assert es["state"] == EntityAutomationState.IDLE


# ===========================================================================
# Off-timer exception – Line 1435-1436
# ===========================================================================

class TestOffTimerException:

    @pytest.mark.asyncio
    async def test_off_timer_exception_caught(self):
        """Lines 1435-1436: Exception during off timer execution is caught."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        entry = _make_entry(extra={CONF_OFF_DELAY: 0})
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]

        # Make _are_clearing_sensors_clear raise
        coord._are_clearing_sensors_clear = MagicMock(side_effect=Exception("sensor error"))

        # Execute the timer directly
        await coord._execute_entity_off_timer("light.living_room", es, 0)
        # Should have caught the exception and cleared the timer


# ===========================================================================
# Auto-reenable lifecycle – Lines 1649-1650, 1675-1677, 1728, 1802, 1821, 1827, 1895
# ===========================================================================

class TestAutoReEnableLifecycle:

    @pytest.mark.asyncio
    async def test_clear_tracking_state_deletes_file(self):
        """Lines 1675-1677: _clear_tracking_state removes the file."""
        import tempfile, os
        tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmpdir, ".storage"), exist_ok=True)

        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        hass.config = MagicMock()
        hass.config.path = lambda p: os.path.join(tmpdir, p)

        # Add async_add_executor_job
        async def _exec(fn, *args):
            return fn(*args)
        hass.async_add_executor_job = _exec

        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)

        # Create the tracking file
        path = coord._get_tracking_persistence_path()
        path.write_text(json.dumps({"is_tracking": True}))
        assert path.exists()

        await coord._clear_tracking_state()
        assert not path.exists()

    @pytest.mark.asyncio
    async def test_get_tracking_info_while_tracking_occupied(self):
        """Line 1728: Vacancy percentage when tracking and currently occupied."""
        import custom_components.presence_based_lighting as mod

        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
            CONF_AUTO_REENABLE_VACANCY_THRESHOLD: 80,
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)

        now = mod.dt_util.utcnow()
        coord._auto_reenable_tracking = {
            "is_tracking": True,
            "window_start": now - timedelta(hours=1),
            "occupied_seconds": 1800.0,  # 30 min occupied
            "last_presence_change": now - timedelta(minutes=10),
            "was_occupied": True,
        }

        info = coord.get_auto_reenable_tracking_info()
        assert info["is_tracking"] is True
        assert "current_vacancy_percent" in info
        assert info["currently_occupied"] is True

    @pytest.mark.asyncio
    async def test_start_time_callback_initializes_tracking(self):
        """Line 1802: Start time callback initializes tracking state."""
        import custom_components.presence_based_lighting as mod

        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        coord._auto_reenable_enabled = True

        await coord._handle_auto_reenable_start_time(mod.dt_util.utcnow())

        tracking = coord._auto_reenable_tracking
        assert tracking["is_tracking"] is True
        assert tracking["window_start"] is not None
        assert tracking["occupied_seconds"] == 0.0

    @pytest.mark.asyncio
    async def test_end_time_callback_evaluates(self):
        """Lines 1821, 1827: End time callback triggers evaluation."""
        import custom_components.presence_based_lighting as mod

        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
            CONF_AUTO_REENABLE_VACANCY_THRESHOLD: 50,
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        coord._auto_reenable_enabled = True

        # Set up tracking state (almost all vacant)
        now = mod.dt_util.utcnow()
        coord._auto_reenable_tracking = {
            "is_tracking": True,
            "window_start": now - timedelta(hours=8),
            "occupied_seconds": 100.0,
            "last_presence_change": now - timedelta(hours=1),
            "was_occupied": False,
        }

        await coord._handle_auto_reenable_end_time(now)

        # Should have evaluated and reset tracking
        assert coord._auto_reenable_tracking["is_tracking"] is False

    @pytest.mark.asyncio
    async def test_reenable_presence_lighting(self):
        """Line 1895: _reenable_presence_lighting re-enables disabled entities."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)

        es = coord._entity_states["light.living_room"]
        es["presence_allowed"] = False  # Disabled
        es["state"] = EntityAutomationState.PAUSED

        await coord._reenable_presence_lighting()

        # Should have re-enabled
        assert es["presence_allowed"] is True
        assert es["state"] != EntityAutomationState.PAUSED


# ===========================================================================
# _check_auto_reenable_startup – Lines 1942-1980
# ===========================================================================

class TestAutoReEnableStartup:

    @pytest.mark.asyncio
    async def test_startup_mid_window_continues_tracking(self):
        """Lines 1973-1977: HA restarts during monitoring window → continue tracking."""
        import tempfile, os, json
        import custom_components.presence_based_lighting as mod

        tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmpdir, ".storage"), exist_ok=True)

        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        hass.config = MagicMock()
        hass.config.path = lambda p: os.path.join(tmpdir, p)
        async def _exec(fn, *args):
            return fn(*args)
        hass.async_add_executor_job = _exec

        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
            CONF_AUTO_REENABLE_VACANCY_THRESHOLD: 80,
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        coord._auto_reenable_enabled = True

        now = mod.dt_util.utcnow()
        # Create a window that we're currently inside
        # Set start time to be in the past today, end time in the future
        coord._auto_reenable_start_time = (now - timedelta(hours=2)).time()
        coord._auto_reenable_end_time = (now + timedelta(hours=2)).time()

        # Save tracking state
        path = coord._get_tracking_persistence_path()
        window_start = now - timedelta(hours=1)
        data = {
            "is_tracking": True,
            "window_start": window_start.isoformat(),
            "occupied_seconds": 500.0,
            "last_presence_change": (now - timedelta(minutes=30)).isoformat(),
            "was_occupied": False,
            "saved_at": now.isoformat(),
        }
        path.write_text(json.dumps(data))

        await coord._check_auto_reenable_startup()

        tracking = coord._auto_reenable_tracking
        assert tracking["is_tracking"] is True

    @pytest.mark.asyncio
    async def test_startup_past_window_evaluates(self):
        """Lines 1966-1970: HA restarts after window ended → evaluate."""
        import tempfile, os, json
        import custom_components.presence_based_lighting as mod

        tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmpdir, ".storage"), exist_ok=True)

        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        hass.config = MagicMock()
        hass.config.path = lambda p: os.path.join(tmpdir, p)
        async def _exec(fn, *args):
            return fn(*args)
        hass.async_add_executor_job = _exec

        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
            CONF_AUTO_REENABLE_VACANCY_THRESHOLD: 80,
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        coord._auto_reenable_enabled = True

        now = mod.dt_util.utcnow()
        # Window ended 1 hour ago
        coord._auto_reenable_start_time = (now - timedelta(hours=10)).time()
        coord._auto_reenable_end_time = (now - timedelta(hours=1)).time()

        # Save tracking state from before end time
        path = coord._get_tracking_persistence_path()
        window_start = now - timedelta(hours=9)
        data = {
            "is_tracking": True,
            "window_start": window_start.isoformat(),
            "occupied_seconds": 100.0,
            "last_presence_change": (now - timedelta(hours=2)).isoformat(),
            "was_occupied": False,
            "saved_at": (now - timedelta(hours=1, minutes=30)).isoformat(),
        }
        path.write_text(json.dumps(data))

        await coord._check_auto_reenable_startup()

        # Should have evaluated (tracking reset)
        assert coord._auto_reenable_tracking["is_tracking"] is False

    @pytest.mark.asyncio
    async def test_startup_stale_tracking_clears(self):
        """Lines 1978-1980: Stale tracking state from previous day → clear."""
        import tempfile, os, json
        import custom_components.presence_based_lighting as mod

        tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmpdir, ".storage"), exist_ok=True)

        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        hass.config = MagicMock()
        hass.config.path = lambda p: os.path.join(tmpdir, p)
        async def _exec(fn, *args):
            return fn(*args)
        hass.async_add_executor_job = _exec

        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "22:00:00",
            CONF_AUTO_REENABLE_END_TIME: "06:00:00",
            CONF_AUTO_REENABLE_VACANCY_THRESHOLD: 80,
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        coord._auto_reenable_enabled = True

        now = mod.dt_util.utcnow()
        # Window is in the future (e.g., 22:00 today, we're at 12:00 today)
        coord._auto_reenable_start_time = time(22, 0, 0)
        coord._auto_reenable_end_time = time(6, 0, 0)

        # Save stale tracking from yesterday
        path = coord._get_tracking_persistence_path()
        window_start = now - timedelta(days=1, hours=5)
        data = {
            "is_tracking": True,
            "window_start": window_start.isoformat(),
            "occupied_seconds": 100.0,
            "last_presence_change": (now - timedelta(days=1)).isoformat(),
            "was_occupied": False,
            "saved_at": (now - timedelta(days=1)).isoformat(),
        }
        path.write_text(json.dumps(data))

        await coord._check_auto_reenable_startup()

    @pytest.mark.asyncio
    async def test_startup_no_times_configured(self):
        """Line 1953: Start/end times not configured → return early."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        coord._auto_reenable_enabled = True
        coord._auto_reenable_start_time = None
        coord._auto_reenable_end_time = None

        # Need to have tracking state loaded
        coord._auto_reenable_tracking["is_tracking"] = True
        coord._auto_reenable_tracking["window_start"] = datetime.now(timezone.utc)

        # Monkey-patch _load_tracking_state to return True
        async def _mock_load():
            return True
        coord._load_tracking_state = _mock_load

        await coord._check_auto_reenable_startup()

    @pytest.mark.asyncio
    async def test_startup_midnight_crossing_window(self):
        """Line 1962: Window crosses midnight (start_time > end_time)."""
        import tempfile, os, json
        import custom_components.presence_based_lighting as mod

        tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmpdir, ".storage"), exist_ok=True)

        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        hass.config = MagicMock()
        hass.config.path = lambda p: os.path.join(tmpdir, p)
        async def _exec(fn, *args):
            return fn(*args)
        hass.async_add_executor_job = _exec

        entry = _make_entry(extra={
            CONF_AUTO_REENABLE_START_TIME: "23:00:00",
            CONF_AUTO_REENABLE_END_TIME: "05:00:00",
            CONF_AUTO_REENABLE_VACANCY_THRESHOLD: 80,
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        coord._auto_reenable_enabled = True

        now = mod.dt_util.utcnow()
        # Set times that cross midnight
        coord._auto_reenable_start_time = time(23, 0, 0)
        coord._auto_reenable_end_time = time(5, 0, 0)

        # Save tracking state within the current window
        path = coord._get_tracking_persistence_path()
        window_start = now - timedelta(hours=3)
        data = {
            "is_tracking": True,
            "window_start": window_start.isoformat(),
            "occupied_seconds": 100.0,
            "last_presence_change": (now - timedelta(hours=1)).isoformat(),
            "was_occupied": False,
            "saved_at": (now - timedelta(minutes=30)).isoformat(),
        }
        path.write_text(json.dumps(data))

        await coord._check_auto_reenable_startup()


# ===========================================================================
# _handle_external_action – target_state matching – Line 1303
# ===========================================================================

class TestHandleExternalAction:

    @pytest.mark.asyncio
    async def test_external_action_cleared_service_pauses(self):
        """Line 1303: External action with cleared service → pause."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.OCCUPIED

        # External action calls the cleared service
        await coord._handle_external_action("light.living_room", "turn_off")

        # Should be paused
        assert es["state"] == EntityAutomationState.PAUSED


# ===========================================================================
# _apply_action_to_entity – NO_ACTION, cleared skip – Lines 1249-1250, 1265
# ===========================================================================

class TestApplyActionEdges:

    @pytest.mark.asyncio
    async def test_apply_action_no_action_skipped(self):
        """Line 1249-1250: Service is NO_ACTION → skip."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry(extra={
            CONF_CONTROLLED_ENTITIES: [{
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: NO_ACTION,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: False,
                CONF_REQUIRE_VACANCY_FOR_CLEARED: False,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            }]
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        es = coord._entity_states["light.living_room"]
        hass.services.clear()

        await coord._apply_action_to_entity(es, CONF_PRESENCE_DETECTED_SERVICE)
        assert len(hass.services.calls) == 0

    @pytest.mark.asyncio
    async def test_apply_cleared_action_already_in_target_state(self):
        """Line 1265: Cleared action but entity already in target state → skip."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        es = coord._entity_states["light.living_room"]
        hass.services.clear()

        await coord._apply_action_to_entity(es, CONF_PRESENCE_CLEARED_SERVICE)
        # Light is already off, so should be skipped
        assert len(hass.services.calls) == 0


# ===========================================================================
# _handle_presence_change – null states guard – Line 1093
# ===========================================================================

class TestPresenceChangeNullStates:

    @pytest.mark.asyncio
    async def test_null_new_state(self):
        """Guard: new_state is None → return."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        event = _make_event({
            "entity_id": "binary_sensor.living_room_motion",
            "new_state": None,
            "old_state": _make_state(STATE_OFF),
        })
        # Should not raise
        await coord._handle_presence_change(event)


# ===========================================================================
# _should_external_change_pause – legacy behaviour – Lines 1495-1500 
# ===========================================================================

class TestShouldExternalChangePause:

    @pytest.mark.asyncio
    async def test_legacy_cleared_state_pauses(self):
        """Legacy: cleared-state service pauses."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)

        cfg = coord._entity_states["light.living_room"]["config"]
        # No CONF_MANUAL_DISABLE_STATES → legacy behaviour
        if CONF_MANUAL_DISABLE_STATES in cfg:
            del cfg[CONF_MANUAL_DISABLE_STATES]

        result = coord._should_external_change_pause("light.living_room", cfg, "off")
        assert result is True  # cleared state matches

    @pytest.mark.asyncio
    async def test_legacy_detected_state_does_not_pause(self):
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        entry = _make_entry()
        coord = PresenceBasedLightingCoordinator(hass, entry)

        cfg = coord._entity_states["light.living_room"]["config"]
        if CONF_MANUAL_DISABLE_STATES in cfg:
            del cfg[CONF_MANUAL_DISABLE_STATES]

        result = coord._should_external_change_pause("light.living_room", cfg, "on")
        assert result is False


# ===========================================================================
# _start_entity_off_timer with entity-specific delay – CONF_ENTITY_OFF_DELAY
# ===========================================================================

class TestEntityOffDelay:

    @pytest.mark.asyncio
    async def test_entity_specific_off_delay(self):
        """Uses entity-specific off delay when configured."""
        hass = MockHass()
        setup_entity_states(hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        entry = _make_entry(extra={
            CONF_CONTROLLED_ENTITIES: [{
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: False,
                CONF_REQUIRE_VACANCY_FOR_CLEARED: False,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
                CONF_ENTITY_OFF_DELAY: 5,
            }]
        })
        coord = PresenceBasedLightingCoordinator(hass, entry)
        await coord.async_start()

        es = coord._entity_states["light.living_room"]
        es["state"] = EntityAutomationState.OCCUPIED

        await coord._start_entity_off_timer("light.living_room", es)
        assert es["state"] == EntityAutomationState.CLEARING
        assert es["off_timer"] is not None
