"""Tests for the two-boolean system: presence_allowed vs automation_paused.

This tests the separation of concerns between:
- presence_allowed: User-controlled, persisted across reboots
- automation_paused: Automatic, transient, based on manual_disable_states

Key behaviors to test:
1. Manual control sets automation_paused, NOT presence_allowed
2. presence_allowed is only changed by user toggle
3. Both must be favorable for automation to act
4. automation_paused is NOT persisted (resets on restart)
5. presence_allowed IS persisted (via RestoreEntity)
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from homeassistant.const import STATE_ON, STATE_OFF

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.const import (
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
    CONF_RESPECTS_PRESENCE_ALLOWED,
    CONF_ROOM_NAME,
    DEFAULT_CLEARED_SERVICE,
    DEFAULT_CLEARED_STATE,
    DEFAULT_DETECTED_SERVICE,
    DEFAULT_DETECTED_STATE,
)


def _make_entry(entity_configs):
    """Create a mock config entry with given entity configurations."""
    entry = MagicMock()
    entry.entry_id = "test_automation_paused"
    entry.data = {
        CONF_ROOM_NAME: "Test Room",
        CONF_PRESENCE_SENSORS: ["binary_sensor.motion"],
        CONF_OFF_DELAY: 30,
        CONF_CONTROLLED_ENTITIES: entity_configs,
    }
    return entry


def _make_entity_config(entity_id, manual_disable_states=None):
    """Create a standard entity config with optional manual_disable_states."""
    config = {
        CONF_ENTITY_ID: entity_id,
        CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
        CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
        CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
        CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
        CONF_RESPECTS_PRESENCE_ALLOWED: True,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
        CONF_INITIAL_PRESENCE_ALLOWED: True,
    }
    if manual_disable_states is not None:
        config[CONF_MANUAL_DISABLE_STATES] = manual_disable_states
    return config


def _state_change_event(mock_hass, entity_id, old_state, new_state):
    """Create a state change event data dict."""
    old = MagicMock()
    old.state = old_state
    new = MagicMock()
    new.state = new_state
    new.context = MagicMock()
    new.context.id = "external_context"
    new.context.parent_id = None
    return {
        "entity_id": entity_id,
        "old_state": old,
        "new_state": new,
    }


class TestTwoBooleanInitialization:
    """Test that both booleans are correctly initialized."""

    @pytest.mark.asyncio
    async def test_both_booleans_initialized(self, mock_hass):
        """Both presence_allowed and automation_paused should be initialized."""
        entry = _make_entry([_make_entity_config("light.test")])
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("light.test", STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        
        # presence_allowed should be True (from CONF_INITIAL_PRESENCE_ALLOWED)
        assert coordinator.get_presence_allowed("light.test") is True
        # automation_paused should be False (default)
        assert coordinator.get_automation_paused("light.test") is False

    @pytest.mark.asyncio
    async def test_initial_presence_allowed_respected(self, mock_hass):
        """CONF_INITIAL_PRESENCE_ALLOWED should set presence_allowed."""
        config = _make_entity_config("light.test")
        config[CONF_INITIAL_PRESENCE_ALLOWED] = False
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("light.test", STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        
        assert coordinator.get_presence_allowed("light.test") is False
        assert coordinator.get_automation_paused("light.test") is False


class TestManualControlSetsAutomationPaused:
    """Test that manual control affects automation_paused, not presence_allowed."""

    @pytest.mark.asyncio
    async def test_manual_off_pauses_automation_not_presence_allowed(self, mock_hass):
        """Manually turning light off should pause automation, not change presence_allowed."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("light.test", STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator.async_start()
        
        # Initial state: both should allow automation
        assert coordinator.get_presence_allowed("light.test") is True
        assert coordinator.get_automation_paused("light.test") is False
        
        # Simulate manual off
        event = MagicMock()
        event.data = _state_change_event(mock_hass, "light.test", STATE_ON, STATE_OFF)
        await coordinator._handle_controlled_entity_change(event)
        
        # presence_allowed should STILL be True
        assert coordinator.get_presence_allowed("light.test") is True
        # automation_paused should now be True
        assert coordinator.get_automation_paused("light.test") is True
        
        coordinator.async_stop()

    @pytest.mark.asyncio
    async def test_manual_on_resumes_automation_not_presence_allowed(self, mock_hass):
        """Manually turning light on should resume automation, not change presence_allowed."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("light.test", STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator.async_start()
        
        # First pause automation
        coordinator.set_automation_paused("light.test", True)
        assert coordinator.get_automation_paused("light.test") is True
        
        # Simulate manual on
        event = MagicMock()
        event.data = _state_change_event(mock_hass, "light.test", STATE_OFF, STATE_ON)
        await coordinator._handle_controlled_entity_change(event)
        
        # presence_allowed should still be True
        assert coordinator.get_presence_allowed("light.test") is True
        # automation_paused should now be False
        assert coordinator.get_automation_paused("light.test") is False
        
        coordinator.async_stop()


class TestPresenceAllowedOnlyChangedByUser:
    """Test that presence_allowed only changes via user toggle."""

    @pytest.mark.asyncio
    async def test_user_toggle_changes_presence_allowed(self, mock_hass):
        """User toggling switch should change presence_allowed."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("light.test", STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator.async_start()
        
        # User turns off presence_allowed
        await coordinator.async_set_presence_allowed("light.test", False)
        assert coordinator.get_presence_allowed("light.test") is False
        
        # User turns it back on
        await coordinator.async_set_presence_allowed("light.test", True)
        assert coordinator.get_presence_allowed("light.test") is True
        
        coordinator.async_stop()

    @pytest.mark.asyncio
    async def test_manual_control_does_not_change_presence_allowed(self, mock_hass):
        """Manual light control should NOT change presence_allowed."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("light.test", STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator.async_start()
        
        initial_presence_allowed = coordinator.get_presence_allowed("light.test")
        
        # Manual off
        event = MagicMock()
        event.data = _state_change_event(mock_hass, "light.test", STATE_ON, STATE_OFF)
        await coordinator._handle_controlled_entity_change(event)
        
        # Manual on
        event.data = _state_change_event(mock_hass, "light.test", STATE_OFF, STATE_ON)
        await coordinator._handle_controlled_entity_change(event)
        
        # presence_allowed should be unchanged
        assert coordinator.get_presence_allowed("light.test") == initial_presence_allowed
        
        coordinator.async_stop()


class TestBothMustBeFavorable:
    """Test that automation only acts when both booleans allow it."""

    @pytest.mark.asyncio
    async def test_automation_blocked_when_presence_allowed_false(self, mock_hass):
        """Automation should not act when presence_allowed is False."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_ON)
        mock_hass.states.set("light.test", STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator.async_start()
        
        # Disable via user toggle
        await coordinator.async_set_presence_allowed("light.test", False)
        
        entity_state = coordinator._entity_states["light.test"]
        assert coordinator._should_follow_presence(entity_state) is False
        
        coordinator.async_stop()

    @pytest.mark.asyncio
    async def test_automation_blocked_when_automation_paused(self, mock_hass):
        """Automation should not act when automation_paused is True."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_ON)
        mock_hass.states.set("light.test", STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator.async_start()
        
        # Pause via manual control
        coordinator.set_automation_paused("light.test", True)
        
        entity_state = coordinator._entity_states["light.test"]
        assert coordinator._should_follow_presence(entity_state) is False
        
        coordinator.async_stop()

    @pytest.mark.asyncio
    async def test_automation_allowed_when_both_favorable(self, mock_hass):
        """Automation should act when both presence_allowed and not paused."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_ON)
        mock_hass.states.set("light.test", STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator.async_start()
        
        entity_state = coordinator._entity_states["light.test"]
        assert coordinator._should_follow_presence(entity_state) is True
        
        coordinator.async_stop()

    @pytest.mark.asyncio
    async def test_automation_blocked_when_both_unfavorable(self, mock_hass):
        """Automation should not act when both are unfavorable."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_ON)
        mock_hass.states.set("light.test", STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator.async_start()
        
        await coordinator.async_set_presence_allowed("light.test", False)
        coordinator.set_automation_paused("light.test", True)
        
        entity_state = coordinator._entity_states["light.test"]
        assert coordinator._should_follow_presence(entity_state) is False
        
        coordinator.async_stop()


class TestAutomationPausedNotPersisted:
    """Test that automation_paused resets on coordinator restart."""

    @pytest.mark.asyncio
    async def test_automation_paused_resets_on_restart(self, mock_hass):
        """automation_paused should be False after coordinator restart."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("light.test", STATE_OFF)
        
        # First coordinator instance
        coordinator1 = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator1.async_start()
        
        # Pause automation
        coordinator1.set_automation_paused("light.test", True)
        assert coordinator1.get_automation_paused("light.test") is True
        
        coordinator1.async_stop()
        
        # New coordinator instance (simulating restart)
        coordinator2 = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator2.async_start()
        
        # automation_paused should be reset to False
        assert coordinator2.get_automation_paused("light.test") is False
        
        coordinator2.async_stop()


class TestSwitchReflectsPresenceAllowedNotPaused:
    """Test that switch state reflects presence_allowed, not automation_paused."""

    @pytest.mark.asyncio
    async def test_switch_stays_on_when_automation_paused(self, mock_hass):
        """Switch should stay ON even when automation is paused."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("light.test", STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator.async_start()
        
        # presence_allowed should be True (this is what switch shows)
        assert coordinator.get_presence_allowed("light.test") is True
        
        # Manual off pauses automation
        event = MagicMock()
        event.data = _state_change_event(mock_hass, "light.test", STATE_ON, STATE_OFF)
        await coordinator._handle_controlled_entity_change(event)
        
        # Switch should still show ON (presence_allowed unchanged)
        assert coordinator.get_presence_allowed("light.test") is True
        # But automation is paused
        assert coordinator.get_automation_paused("light.test") is True
        
        coordinator.async_stop()


class TestLegacyBehaviorUsesPaused:
    """Test that legacy mode (no manual_disable_states) also uses automation_paused."""

    @pytest.mark.asyncio
    async def test_legacy_mode_uses_automation_paused(self, mock_hass):
        """Legacy mode should pause/resume automation, not change presence_allowed."""
        # No manual_disable_states = legacy mode
        config = _make_entity_config("light.test", manual_disable_states=None)
        # Don't include CONF_MANUAL_DISABLE_STATES at all for legacy mode
        if CONF_MANUAL_DISABLE_STATES in config:
            del config[CONF_MANUAL_DISABLE_STATES]
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("light.test", STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator.async_start()
        
        initial_presence_allowed = coordinator.get_presence_allowed("light.test")
        
        # Manual off (cleared state)
        event = MagicMock()
        event.data = _state_change_event(mock_hass, "light.test", STATE_ON, STATE_OFF)
        await coordinator._handle_controlled_entity_change(event)
        
        # presence_allowed should be unchanged
        assert coordinator.get_presence_allowed("light.test") == initial_presence_allowed
        # automation_paused should be True
        assert coordinator.get_automation_paused("light.test") is True
        
        coordinator.async_stop()


class TestCompleteScenario:
    """Test complete real-world scenarios."""

    @pytest.mark.asyncio
    async def test_full_manual_control_cycle(self, mock_hass):
        """Test a full cycle: on -> manual off -> manual on -> automation resumes."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("light.test", STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator.async_start()
        
        entity_state = coordinator._entity_states["light.test"]
        
        # Initial: automation should work
        assert coordinator._should_follow_presence(entity_state) is True
        assert coordinator.get_presence_allowed("light.test") is True
        assert coordinator.get_automation_paused("light.test") is False
        
        # Manual off
        event = MagicMock()
        event.data = _state_change_event(mock_hass, "light.test", STATE_ON, STATE_OFF)
        await coordinator._handle_controlled_entity_change(event)
        
        # Automation paused, but user toggle unchanged
        assert coordinator._should_follow_presence(entity_state) is False
        assert coordinator.get_presence_allowed("light.test") is True
        assert coordinator.get_automation_paused("light.test") is True
        
        # Manual on
        event.data = _state_change_event(mock_hass, "light.test", STATE_OFF, STATE_ON)
        await coordinator._handle_controlled_entity_change(event)
        
        # Automation resumed, user toggle still unchanged
        assert coordinator._should_follow_presence(entity_state) is True
        assert coordinator.get_presence_allowed("light.test") is True
        assert coordinator.get_automation_paused("light.test") is False
        
        coordinator.async_stop()

    @pytest.mark.asyncio
    async def test_user_disable_survives_manual_control(self, mock_hass):
        """If user disables, manual control shouldn't re-enable."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("light.test", STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator.async_start()
        
        # User disables presence tracking
        await coordinator.async_set_presence_allowed("light.test", False)
        
        entity_state = coordinator._entity_states["light.test"]
        assert coordinator._should_follow_presence(entity_state) is False
        
        # Manual on (would normally resume automation_paused)
        event = MagicMock()
        event.data = _state_change_event(mock_hass, "light.test", STATE_OFF, STATE_ON)
        await coordinator._handle_controlled_entity_change(event)
        
        # Automation should STILL be blocked because presence_allowed is False
        assert coordinator.get_presence_allowed("light.test") is False
        assert coordinator.get_automation_paused("light.test") is False  # Not paused
        assert coordinator._should_follow_presence(entity_state) is False  # But still blocked
        
        coordinator.async_stop()

    @pytest.mark.asyncio
    async def test_reboot_scenario_toggle_preserved(self, mock_hass):
        """Simulate reboot: presence_allowed restored, automation_paused reset."""
        config = _make_entity_config("light.test", manual_disable_states=["off"])
        entry = _make_entry([config])
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("light.test", STATE_OFF)
        
        # First session: user enables, then automation gets paused
        coordinator1 = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator1.async_start()
        
        await coordinator1.async_set_presence_allowed("light.test", True)
        coordinator1.set_automation_paused("light.test", True)
        
        assert coordinator1.get_presence_allowed("light.test") is True
        assert coordinator1.get_automation_paused("light.test") is True
        
        coordinator1.async_stop()
        
        # Reboot: new coordinator, but switch restores presence_allowed
        coordinator2 = PresenceBasedLightingCoordinator(mock_hass, entry)
        await coordinator2.async_start()
        
        # Simulate RestoreEntity restoring presence_allowed to True
        coordinator2.register_presence_switch("light.test", True, lambda: None)
        
        # presence_allowed should be restored
        assert coordinator2.get_presence_allowed("light.test") is True
        # automation_paused should be reset (not persisted)
        assert coordinator2.get_automation_paused("light.test") is False
        
        # Automation should work
        entity_state = coordinator2._entity_states["light.test"]
        assert coordinator2._should_follow_presence(entity_state) is True
        
        coordinator2.async_stop()
