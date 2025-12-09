"""Tests for RLC entity state initialization on startup.

This test suite verifies that:
1. last_effective_state is initialized from RLC sensors during async_start()
2. State transitions on startup (e.g., unavailable -> off) don't incorrectly
   trigger manual control logic when RLC is configured
3. The toggle state is preserved across reboots when RLC is configured
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.real_last_changed import ATTR_PREVIOUS_VALID_STATE
from custom_components.presence_based_lighting.const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_ENTITY_ID,
    CONF_OFF_DELAY,
    CONF_PRESENCE_SENSORS,
    CONF_CLEARING_SENSORS,
    CONF_PRESENCE_DETECTED_SERVICE,
    CONF_PRESENCE_DETECTED_STATE,
    CONF_PRESENCE_CLEARED_SERVICE,
    CONF_PRESENCE_CLEARED_STATE,
    CONF_RESPECTS_PRESENCE_ALLOWED,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_MANUAL_DISABLE_STATES,
    CONF_RLC_TRACKING_ENTITY,
    CONF_ROOM_NAME,
    CONF_INITIAL_PRESENCE_ALLOWED,
    DEFAULT_DETECTED_SERVICE,
    DEFAULT_DETECTED_STATE,
    DEFAULT_CLEARED_SERVICE,
    DEFAULT_CLEARED_STATE,
    DEFAULT_INITIAL_PRESENCE_ALLOWED,
)


def _entity_event(mock_hass, entity_id, old_state, new_state, old_attrs=None, new_attrs=None):
    """Create a mock entity state change event."""
    mock_hass.states.set(entity_id, new_state)
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": type(
                    "State",
                    (),
                    {"state": old_state, "attributes": old_attrs or {}, "context": type("Ctx", (), {"id": "old", "parent_id": None})()},
                )(),
                "new_state": type(
                    "State",
                    (),
                    {
                        "state": new_state,
                        "attributes": new_attrs or {},
                        "context": type("Ctx", (), {"id": "manual", "parent_id": None})(),
                    },
                )(),
            }
        },
    )()


@pytest.fixture
def mock_hass_with_rlc():
    """Create a mock Home Assistant with RLC sensor support."""
    from tests.conftest import MockHass
    hass = MockHass()
    return hass


@pytest.fixture
def mock_entry_with_rlc_tracking():
    """Create a config entry with RLC tracking entity and manual_disable_states."""
    entry = MagicMock()
    entry.entry_id = "test_entry_rlc_startup"
    entry.data = {
        CONF_ROOM_NAME: "Master Bedroom",
        CONF_OFF_DELAY: 30,
        CONF_PRESENCE_SENSORS: ["sensor.master_bedroom_presence_pir"],
        CONF_CLEARING_SENSORS: [],
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.master_bedroom",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
                CONF_MANUAL_DISABLE_STATES: [STATE_OFF],  # Off pauses automation
                CONF_RLC_TRACKING_ENTITY: "sensor.master_bedroom_lights",  # RLC sensor
            }
        ],
    }
    return entry


class TestRLCStateInitializationOnStartup:
    """Tests for initializing last_effective_state from RLC on startup."""

    @pytest.mark.asyncio
    async def test_last_effective_state_initialized_from_rlc_on_startup(
        self, mock_hass_with_rlc, mock_entry_with_rlc_tracking
    ):
        """last_effective_state should be initialized from RLC sensor during async_start."""
        # Set up the RLC sensor with a known state
        mock_hass_with_rlc.states.set(
            "sensor.master_bedroom_lights",
            "2024-01-01T12:00:00+00:00",
            attributes={ATTR_PREVIOUS_VALID_STATE: STATE_OFF}
        )
        mock_hass_with_rlc.states.set("light.master_bedroom", STATE_OFF)
        mock_hass_with_rlc.states.set("sensor.master_bedroom_presence_pir", STATE_OFF)

        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass_with_rlc, mock_entry_with_rlc_tracking)
            await coordinator.async_start()

        # Verify last_effective_state was initialized
        entity_state = coordinator._entity_states["light.master_bedroom"]
        assert entity_state["last_effective_state"] == STATE_OFF

    @pytest.mark.asyncio
    async def test_last_effective_state_initialized_as_on(
        self, mock_hass_with_rlc, mock_entry_with_rlc_tracking
    ):
        """last_effective_state should be 'on' when RLC reports 'on'."""
        mock_hass_with_rlc.states.set(
            "sensor.master_bedroom_lights",
            "2024-01-01T12:00:00+00:00",
            attributes={ATTR_PREVIOUS_VALID_STATE: STATE_ON}
        )
        mock_hass_with_rlc.states.set("light.master_bedroom", STATE_ON)
        mock_hass_with_rlc.states.set("sensor.master_bedroom_presence_pir", STATE_OFF)

        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass_with_rlc, mock_entry_with_rlc_tracking)
            await coordinator.async_start()

        entity_state = coordinator._entity_states["light.master_bedroom"]
        assert entity_state["last_effective_state"] == STATE_ON

    @pytest.mark.asyncio
    async def test_rlc_unavailable_leaves_last_effective_state_none(
        self, mock_hass_with_rlc, mock_entry_with_rlc_tracking
    ):
        """If RLC sensor is unavailable, last_effective_state should remain None."""
        # RLC sensor doesn't exist or is unavailable
        mock_hass_with_rlc.states.set("light.master_bedroom", STATE_OFF)
        mock_hass_with_rlc.states.set("sensor.master_bedroom_presence_pir", STATE_OFF)
        # Don't set sensor.master_bedroom_lights

        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass_with_rlc, mock_entry_with_rlc_tracking)
            await coordinator.async_start()

        entity_state = coordinator._entity_states["light.master_bedroom"]
        assert entity_state["last_effective_state"] is None


class TestNoFalseManualControlOnStartup:
    """Tests ensuring startup state changes don't falsely trigger manual control."""

    @pytest.mark.asyncio
    async def test_startup_state_change_does_not_disable_toggle(
        self, mock_hass_with_rlc, mock_entry_with_rlc_tracking
    ):
        """State change on startup should not disable presence_allowed when RLC is configured."""
        # Set up RLC sensor with "off" state (light was off before reboot)
        mock_hass_with_rlc.states.set(
            "sensor.master_bedroom_lights",
            "2024-01-01T12:00:00+00:00",
            attributes={ATTR_PREVIOUS_VALID_STATE: STATE_OFF}
        )
        mock_hass_with_rlc.states.set("light.master_bedroom", STATE_OFF)
        mock_hass_with_rlc.states.set("sensor.master_bedroom_presence_pir", STATE_OFF)

        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass_with_rlc, mock_entry_with_rlc_tracking)
            
            # Register presence switch to set initial_state = True (simulating restored state)
            coordinator.register_presence_switch(
                "light.master_bedroom",
                True,  # Toggle was ON before reboot
                lambda: None
            )
            
            await coordinator.async_start()

        # Verify presence_allowed is still True
        assert coordinator.get_presence_allowed("light.master_bedroom") is True

        # Simulate the startup state change event (unavailable -> off)
        # This happens when HA boots and lights report their state
        await coordinator._handle_controlled_entity_change(
            _entity_event(
                mock_hass_with_rlc,
                "light.master_bedroom",
                "unavailable",  # old state
                STATE_OFF,  # new state
            )
        )

        # Toggle should STILL be True - the startup event should be ignored
        # because last_effective_state matches the RLC state
        assert coordinator.get_presence_allowed("light.master_bedroom") is True

    @pytest.mark.asyncio
    async def test_repeated_same_rlc_state_is_ignored(
        self, mock_hass_with_rlc, mock_entry_with_rlc_tracking
    ):
        """Multiple state change events with same RLC effective state should be ignored."""
        mock_hass_with_rlc.states.set(
            "sensor.master_bedroom_lights",
            "2024-01-01T12:00:00+00:00",
            attributes={ATTR_PREVIOUS_VALID_STATE: STATE_OFF}
        )
        mock_hass_with_rlc.states.set("light.master_bedroom", STATE_OFF)
        mock_hass_with_rlc.states.set("sensor.master_bedroom_presence_pir", STATE_OFF)

        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass_with_rlc, mock_entry_with_rlc_tracking)
            coordinator.register_presence_switch("light.master_bedroom", True, lambda: None)
            await coordinator.async_start()

        # First state change event after startup
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass_with_rlc, "light.master_bedroom", "unavailable", STATE_OFF)
        )
        assert coordinator.get_presence_allowed("light.master_bedroom") is True

        # Another event with same RLC state (different raw state transitions)
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass_with_rlc, "light.master_bedroom", STATE_OFF, STATE_OFF)
        )
        assert coordinator.get_presence_allowed("light.master_bedroom") is True

    @pytest.mark.asyncio
    async def test_real_manual_change_still_disables_toggle(
        self, mock_hass_with_rlc, mock_entry_with_rlc_tracking
    ):
        """A real manual change (RLC state changes) should still disable the toggle."""
        # Start with light ON
        mock_hass_with_rlc.states.set(
            "sensor.master_bedroom_lights",
            "2024-01-01T12:00:00+00:00",
            attributes={ATTR_PREVIOUS_VALID_STATE: STATE_ON}
        )
        mock_hass_with_rlc.states.set("light.master_bedroom", STATE_ON)
        mock_hass_with_rlc.states.set("sensor.master_bedroom_presence_pir", STATE_OFF)

        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass_with_rlc, mock_entry_with_rlc_tracking)
            coordinator.register_presence_switch("light.master_bedroom", True, lambda: None)
            await coordinator.async_start()

        assert coordinator.get_presence_allowed("light.master_bedroom") is True

        # Now simulate a real manual change - user turns light OFF
        # Update the RLC sensor to reflect the new state
        mock_hass_with_rlc.states.set(
            "sensor.master_bedroom_lights",
            "2024-01-01T12:01:00+00:00",
            attributes={ATTR_PREVIOUS_VALID_STATE: STATE_OFF}
        )

        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass_with_rlc, "light.master_bedroom", STATE_ON, STATE_OFF)
        )

        # NOW it should be disabled - this was a real manual change
        assert coordinator.get_presence_allowed("light.master_bedroom") is False


class TestMultipleEntitiesWithRLC:
    """Tests for multiple entities with RLC tracking."""

    @pytest.fixture
    def mock_entry_multi_rlc(self):
        """Config entry with multiple entities, some with RLC tracking."""
        entry = MagicMock()
        entry.entry_id = "test_entry_multi_rlc"
        entry.data = {
            CONF_ROOM_NAME: "Master Bedroom",
            CONF_OFF_DELAY: 30,
            CONF_PRESENCE_SENSORS: ["sensor.master_bedroom_presence_pir"],
            CONF_CLEARING_SENSORS: [],
            CONF_CONTROLLED_ENTITIES: [
                {
                    CONF_ENTITY_ID: "light.master_bedroom",
                    CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                    CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                    CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                    CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                    CONF_RESPECTS_PRESENCE_ALLOWED: True,
                    CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                    CONF_INITIAL_PRESENCE_ALLOWED: True,
                    CONF_MANUAL_DISABLE_STATES: [STATE_OFF],
                    CONF_RLC_TRACKING_ENTITY: "sensor.master_bedroom_lights",
                },
                {
                    CONF_ENTITY_ID: "cover.master_bedroom_vents",
                    CONF_PRESENCE_DETECTED_SERVICE: "open_cover_tilt",
                    CONF_PRESENCE_DETECTED_STATE: STATE_ON,
                    CONF_PRESENCE_CLEARED_SERVICE: "close_cover_tilt",
                    CONF_PRESENCE_CLEARED_STATE: STATE_OFF,
                    CONF_RESPECTS_PRESENCE_ALLOWED: True,
                    CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                    CONF_INITIAL_PRESENCE_ALLOWED: True,
                    CONF_MANUAL_DISABLE_STATES: [],  # No RLC, empty disable states
                    # No CONF_RLC_TRACKING_ENTITY - not RLC tracked
                },
            ],
        }
        return entry

    @pytest.mark.asyncio
    async def test_only_rlc_entities_get_initialized(
        self, mock_hass_with_rlc, mock_entry_multi_rlc
    ):
        """Only entities with RLC tracking should have last_effective_state initialized."""
        mock_hass_with_rlc.states.set(
            "sensor.master_bedroom_lights",
            "2024-01-01T12:00:00+00:00",
            attributes={ATTR_PREVIOUS_VALID_STATE: STATE_OFF}
        )
        mock_hass_with_rlc.states.set("light.master_bedroom", STATE_OFF)
        mock_hass_with_rlc.states.set("cover.master_bedroom_vents", STATE_OFF)
        mock_hass_with_rlc.states.set("sensor.master_bedroom_presence_pir", STATE_OFF)

        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass_with_rlc, mock_entry_multi_rlc)
            await coordinator.async_start()

        # Light has RLC - should be initialized
        light_state = coordinator._entity_states["light.master_bedroom"]
        assert light_state["last_effective_state"] == STATE_OFF

        # Cover has no RLC - should remain None
        cover_state = coordinator._entity_states["cover.master_bedroom_vents"]
        assert cover_state["last_effective_state"] is None


class TestTogglePreservationAcrossReboot:
    """End-to-end tests for toggle preservation across simulated reboots."""

    @pytest.mark.asyncio
    async def test_full_reboot_scenario_toggle_preserved(
        self, mock_hass_with_rlc, mock_entry_with_rlc_tracking
    ):
        """
        Simulate full reboot scenario:
        1. Light is ON, toggle is ON before reboot
        2. HASS reboots
        3. Light reports unavailable -> off (typical Zigbee behavior)
        4. Toggle should remain ON because RLC says state hasn't really changed
        """
        # Initial state: light is ON (but will report OFF after reboot due to Zigbee quirk)
        # RLC sensor correctly remembers the light was ON
        mock_hass_with_rlc.states.set(
            "sensor.master_bedroom_lights",
            "2024-01-01T12:00:00+00:00",
            attributes={ATTR_PREVIOUS_VALID_STATE: STATE_ON}  # RLC knows light was ON
        )
        mock_hass_with_rlc.states.set("light.master_bedroom", STATE_ON)
        mock_hass_with_rlc.states.set("sensor.master_bedroom_presence_pir", STATE_OFF)

        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass_with_rlc, mock_entry_with_rlc_tracking)
            
            # Simulate switch restoration: toggle was ON before reboot
            coordinator.register_presence_switch("light.master_bedroom", True, lambda: None)
            
            await coordinator.async_start()

        # Verify initial state
        assert coordinator.get_presence_allowed("light.master_bedroom") is True
        assert coordinator._entity_states["light.master_bedroom"]["last_effective_state"] == STATE_ON

        # Simulate HASS receiving state updates after reboot
        # Light goes unavailable -> off (but RLC still says ON)
        # This should NOT disable the toggle
        
        # RLC still reports ON (the light was actually on, just Zigbee reported wrong)
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass_with_rlc, "light.master_bedroom", "unavailable", STATE_OFF)
        )

        # Toggle should still be ON - RLC says effective state is still ON
        assert coordinator.get_presence_allowed("light.master_bedroom") is True
