"""Tests for manual_disable_states configuration option.

This feature allows users to specify which states should pause automation when
manually set (e.g., "off" pauses automation), and which states should resume
automation (any state NOT in the list re-enables automation).
"""

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_MANUAL_DISABLE_STATES,
)
from tests.conftest import assert_service_called, setup_entity_states


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


def _service_event(entity_id, service):
    """Create a mock service call event."""
    return type(
        "Event",
        (),
        {
            "data": {
                "service_data": {"entity_id": entity_id},
                "service": service,
            },
            "context": type("Ctx", (), {"id": "manual", "parent_id": None})(),
        },
    )()


class TestManualDisableStatesEmpty:
    """Test behavior when manual_disable_states is empty (default)."""

    @pytest.mark.asyncio
    async def test_empty_list_means_no_states_disable_automation(self, mock_hass, mock_config_entry):
        """With an empty list, no manual state change should disable automation."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = []
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Manual off should NOT disable automation
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert coordinator.get_automation_paused("light.living_room") is False

    @pytest.mark.asyncio
    async def test_empty_list_allows_presence_to_turn_on_after_manual_off(self, mock_hass, mock_config_entry):
        """With empty list, presence should still work after manual off."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = []
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Manual off
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )

        # Presence detected should still trigger
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF, STATE_ON)
        )
        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")


class TestManualDisableStatesOff:
    """Test behavior when manual_disable_states contains 'off'."""

    @pytest.mark.asyncio
    async def test_manual_off_disables_when_off_in_list(self, mock_hass, mock_config_entry):
        """Manual off should disable automation when 'off' is in the list."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = [STATE_OFF]
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert coordinator.get_automation_paused("light.living_room") is True

    @pytest.mark.asyncio
    async def test_manual_on_re_enables_when_off_in_list(self, mock_hass, mock_config_entry):
        """Manual on should re-enable automation when 'off' is in the list."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = [STATE_OFF]
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Manual off disables
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert coordinator.get_automation_paused("light.living_room") is True

        # Manual on re-enables
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_OFF, STATE_ON)
        )
        assert coordinator.get_automation_paused("light.living_room") is False

    @pytest.mark.asyncio
    async def test_presence_blocked_after_manual_off(self, mock_hass, mock_config_entry):
        """Presence detection should not trigger lights when paused by manual off."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = [STATE_OFF]
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Manual off disables automation
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )

        # Presence should not turn light back on
        mock_hass.services.clear()
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF, STATE_ON)
        )
        assert mock_hass.services.calls == []


class TestManualDisableStatesOn:
    """Test behavior when manual_disable_states contains 'on'."""

    @pytest.mark.asyncio
    async def test_manual_on_disables_when_on_in_list(self, mock_hass, mock_config_entry):
        """Manual on should disable automation when 'on' is in the list."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = [STATE_ON]
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_OFF, STATE_ON)
        )
        assert coordinator.get_automation_paused("light.living_room") is True

    @pytest.mark.asyncio
    async def test_manual_off_re_enables_when_on_in_list(self, mock_hass, mock_config_entry):
        """Manual off should re-enable automation when 'on' is in the list."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = [STATE_ON]
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Manual on disables
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_OFF, STATE_ON)
        )
        assert coordinator.get_automation_paused("light.living_room") is True

        # Manual off re-enables
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert coordinator.get_automation_paused("light.living_room") is False

    @pytest.mark.asyncio
    async def test_presence_cleared_blocked_after_manual_on(self, mock_hass, mock_config_entry):
        """Presence cleared should not turn off lights when paused by manual on."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = [STATE_ON]
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Manual on disables automation
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_OFF, STATE_ON)
        )

        # Presence cleared should not turn light off
        mock_hass.services.clear()
        mock_hass.states.set("light.living_room", STATE_ON)
        await coordinator._handle_presence_change(
            _entity_event(mock_hass, "binary_sensor.living_room_motion", STATE_ON, STATE_OFF)
        )
        # Should not call turn_off
        turn_off_calls = [c for c in mock_hass.services.calls if c["service"] == "turn_off"]
        assert turn_off_calls == []


class TestManualDisableStatesBothOnOff:
    """Test behavior when manual_disable_states contains both 'on' and 'off'."""

    @pytest.mark.asyncio
    async def test_both_states_disable_automation(self, mock_hass, mock_config_entry):
        """Both on and off should disable automation when both are in the list."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = [STATE_ON, STATE_OFF]
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Manual on disables
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_OFF, STATE_ON)
        )
        assert coordinator.get_automation_paused("light.living_room") is True

        # Manual off also keeps it disabled
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert coordinator.get_automation_paused("light.living_room") is True


class TestManualDisableStatesCustomStates:
    """Test behavior with custom states like brightness levels or scenes."""

    @pytest.mark.asyncio
    async def test_custom_state_disables_when_in_list(self, mock_hass, mock_config_entry):
        """A custom state should disable automation when in the list."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = ["dim"]
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, "dim")
        )
        assert coordinator.get_automation_paused("light.living_room") is True

    @pytest.mark.asyncio
    async def test_custom_state_not_in_list_re_enables(self, mock_hass, mock_config_entry):
        """A custom state not in the list should re-enable automation."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = ["dim"]
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Dim disables
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, "dim")
        )
        assert coordinator.get_automation_paused("light.living_room") is True

        # "bright" is not in the list, so it re-enables
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", "dim", "bright")
        )
        assert coordinator.get_automation_paused("light.living_room") is False

    @pytest.mark.asyncio
    async def test_multiple_custom_states_in_list(self, mock_hass, mock_config_entry):
        """Multiple custom states should all disable automation."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = ["dim", "movie_mode", "sleep"]
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # "dim" disables
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, "dim")
        )
        assert coordinator.get_automation_paused("light.living_room") is True

        # "movie_mode" keeps it disabled
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", "dim", "movie_mode")
        )
        assert coordinator.get_automation_paused("light.living_room") is True

        # "on" is not in the list, so it re-enables
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", "movie_mode", STATE_ON)
        )
        assert coordinator.get_automation_paused("light.living_room") is False


class TestManualDisableStatesServiceCalls:
    """Test manual_disable_states with service calls (external actions)."""

    @pytest.mark.asyncio
    async def test_service_call_turn_off_disables_when_off_in_list(self, mock_hass, mock_config_entry):
        """Service call turn_off should disable when 'off' is in manual_disable_states."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = [STATE_OFF]
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Simulate service call turn_off (pass service name, not state)
        mock_hass.states.set("light.living_room", STATE_OFF)
        await coordinator._handle_external_action("light.living_room", "turn_off")
        assert coordinator.get_automation_paused("light.living_room") is True

    @pytest.mark.asyncio
    async def test_service_call_turn_on_re_enables_when_off_in_list(self, mock_hass, mock_config_entry):
        """Service call turn_on should re-enable when 'off' is in manual_disable_states."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = [STATE_OFF]
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Disable first
        mock_hass.states.set("light.living_room", STATE_OFF)
        await coordinator._handle_external_action("light.living_room", "turn_off")
        assert coordinator.get_automation_paused("light.living_room") is True

        # Re-enable with turn_on
        mock_hass.states.set("light.living_room", STATE_ON)
        await coordinator._handle_external_action("light.living_room", "turn_on")
        assert coordinator.get_automation_paused("light.living_room") is False


class TestManualDisableStatesInteractionWithOtherSettings:
    """Test interaction between manual_disable_states and other config options."""

    @pytest.mark.asyncio
    async def test_disable_on_external_false_ignores_manual_disable_states(self, mock_hass, mock_config_entry):
        """When disable_on_external_control is False, manual_disable_states has no effect."""
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_DISABLE_ON_EXTERNAL_CONTROL] = False
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = [STATE_OFF]
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Manual off should NOT disable automation
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert coordinator.get_automation_paused("light.living_room") is False

    @pytest.mark.asyncio
    async def test_missing_manual_disable_states_uses_legacy_behavior(self, mock_hass, mock_config_entry):
        """When manual_disable_states key is missing, fall back to legacy behavior."""
        # Remove the key if it exists
        if CONF_MANUAL_DISABLE_STATES in mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0]:
            del mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES]
        
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Manual off SHOULD disable automation in legacy mode (cleared state = off)
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert coordinator.get_automation_paused("light.living_room") is True


class TestManualDisableStatesMultipleEntities:
    """Test manual_disable_states with multiple controlled entities."""

    @pytest.mark.asyncio
    async def test_different_entities_have_different_disable_states(self, mock_hass, mock_config_entry):
        """Different entities can have different manual_disable_states configurations."""
        # Add a second entity with different settings
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES].append({
            "entity_id": "light.bedroom",
            "presence_detected_service": "turn_on",
            "presence_detected_state": "on",
            "presence_cleared_service": "turn_off",
            "presence_cleared_state": "off",
            "respect_presence_allowed": True,
            "disable_on_external_control": True,
            CONF_MANUAL_DISABLE_STATES: [STATE_ON],  # Opposite of living room
        })
        mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_MANUAL_DISABLE_STATES] = [STATE_OFF]
        
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        mock_hass.states.set("light.bedroom", STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        # Living room: off disables
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )
        assert coordinator.get_automation_paused("light.living_room") is True

        # Bedroom: on disables (off should NOT disable)
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.bedroom", STATE_OFF, STATE_ON)
        )
        assert coordinator.get_automation_paused("light.bedroom") is True

        # Living room: on re-enables
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.living_room", STATE_OFF, STATE_ON)
        )
        assert coordinator.get_automation_paused("light.living_room") is False

        # Bedroom: off re-enables
        await coordinator._handle_controlled_entity_change(
            _entity_event(mock_hass, "light.bedroom", STATE_ON, STATE_OFF)
        )
        assert coordinator.get_automation_paused("light.bedroom") is False
