"""P1 High Priority Tests - No-op state change scenarios.

Tests for scenarios where service calls don't result in state changes
(e.g., turning off a light that is already off).
"""
import asyncio
import pytest
from homeassistant.const import STATE_ON, STATE_OFF

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from tests.conftest import setup_entity_states, assert_service_called, assert_service_not_called


class TestNoOpStateChanges:
    """Tests for scenarios where commands don't change state."""

    @pytest.mark.asyncio
    async def test_external_turn_off_already_off_light(self, mock_hass, mock_config_entry):
        """Test external automation turns off already-off light -> automation should disable.
        
        Scenario:
        - Room is unoccupied, lights are already off
        - External automation (e.g., "bedtime") sends turn_off command to lights
        - No state change event occurs (light is already off)
        - Expected: presence automation should recognize manual intervention and disable
        """
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Verify initial state
        assert coordinator.is_enabled == True
        
        # External automation calls turn_off on already-off light
        # Simulate EVENT_CALL_SERVICE event
        from tests.conftest import MockContext
        event_data = {
            "domain": "light",
            "service": "turn_off",
            "service_data": {"entity_id": ["light.living_room"]},
        }
        event = type('obj', (object,), {
            'data': event_data,
            'context': MockContext(id='external_context')
        })()
        await coordinator._handle_service_call(event)
        
        # Should disable
        assert coordinator.is_enabled == False

    @pytest.mark.asyncio
    async def test_external_turn_on_already_on_light(self, mock_hass, mock_config_entry):
        """Test external automation turns on already-on light -> automation should stay enabled.
        
        Scenario:
        - Room is occupied, lights are already on
        - External automation (e.g., "movie mode") sends turn_on command to lights
        - No state change event occurs (light is already on)
        - Expected: automation stays enabled (this is fine)
        """
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Verify initial state
        assert coordinator.is_enabled == True
        
        # External automation calls turn_on on already-on light
        from tests.conftest import MockContext
        event_data = {
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": ["light.living_room"]},
        }
        event = type('obj', (object,), {
            'data': event_data,
            'context': MockContext(id='external_context')
        })()
        await coordinator._handle_service_call(event)
        
        # Automation should stay enabled
        assert coordinator.is_enabled == True

    @pytest.mark.asyncio
    async def test_bedtime_routine_with_multiple_rooms(self, mock_hass, mock_config_entry):
        """Test bedtime routine turning off lights in unoccupied room.
        
        Scenario:
        - Room is unoccupied, lights are off
        - User triggers "bedtime" automation that turns off all house lights
        - The room's lights are already off, so no state change
        - Expected: presence automation should detect this and disable
        """
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        assert coordinator.is_enabled == True
        
        # Bedtime routine sends turn_off to all lights
        from tests.conftest import MockContext
        event_data = {
            "domain": "light",
            "service": "turn_off",
            "service_data": {"entity_id": ["light.living_room", "light.bedroom", "light.kitchen"]},
        }
        event = type('obj', (object,), {
            'data': event_data,
            'context': MockContext(id='bedtime_automation')
        })()
        await coordinator._handle_service_call(event)
        
        # Should disable
        assert coordinator.is_enabled == False

    @pytest.mark.asyncio
    async def test_away_mode_disables_all_rooms(self, mock_hass, mock_config_entry):
        """Test away mode automation disabling lights across the house.
        
        Scenario:
        - User activates "away mode" which sends turn_off to all lights
        - This room's lights are already off (room unoccupied)
        - Expected: presence automation should detect manual intervention
        """
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        assert coordinator.is_enabled == True
        
        # Away mode sends turn_off commands
        from tests.conftest import MockContext
        event_data = {
            "domain": "light",
            "service": "turn_off",
            "service_data": {"entity_id": "light.living_room"},
        }
        event = type('obj', (object,), {
            'data': event_data,
            'context': MockContext(id='away_mode')
        })()
        await coordinator._handle_service_call(event)
        
        # Should disable
        assert coordinator.is_enabled == False

    @pytest.mark.asyncio
    async def test_occupied_room_lights_already_on_external_turn_on(self, mock_hass, mock_config_entry):
        """Test occupied room with lights on receives external turn_on command.
        
        Scenario:
        - Room is occupied, automation already turned lights on
        - External scene activation sends turn_on to same lights
        - No state change (lights already on)
        - Expected: automation stays enabled (correct behavior)
        """
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        assert coordinator.is_enabled == True
        
        # External scene sends turn_on to already-on lights
        from tests.conftest import MockContext
        event_data = {
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": "light.living_room"},
        }
        event = type('obj', (object,), {
            'data': event_data,
            'context': MockContext(id='scene_activation')
        })()
        await coordinator._handle_service_call(event)
        
        # Automation should remain enabled
        assert coordinator.is_enabled == True

    @pytest.mark.asyncio
    async def test_cleanup_routine_partial_overlap(self, mock_hass, mock_config_entry):
        """Test cleanup routine that affects both occupied and unoccupied rooms.
        
        Scenario:
        - Room is unoccupied, lights off
        - User runs "cleanup" routine that turns off specific lights
        - This room's lights included in the command
        - Expected: automation should disable
        """
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        assert coordinator.is_enabled == True
        
        # Cleanup routine targets this light (among others)
        from tests.conftest import MockContext
        event_data = {
            "domain": "light",
            "service": "turn_off",
            "service_data": {"entity_id": ["light.living_room", "light.hallway"]},
        }
        event = type('obj', (object,), {
            'data': event_data,
            'context': MockContext(id='cleanup_routine')
        })()
        await coordinator._handle_service_call(event)
        
        # Should disable
        assert coordinator.is_enabled == False

    @pytest.mark.asyncio
    async def test_state_change_still_works_normally(self, mock_hass, mock_config_entry):
        """Verify that normal state changes still work correctly.
        
        This is a control test to ensure normal operation isn't affected.
        """
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Manual turn off with actual state change
        mock_hass.states.set(
            "light.living_room",
            STATE_OFF,
            context=type('obj', (object,), {'id': 'manual', 'parent_id': None})()
        )
        event_data = {
            "entity_id": "light.living_room",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),
            "new_state": mock_hass.states.get("light.living_room"),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_light_change(event)
        
        # Should disable (this is the normal case that works)
        assert coordinator.is_enabled == False

    @pytest.mark.asyncio
    async def test_integration_own_calls_ignored(self, mock_hass, mock_config_entry):
        """Test that integration's own service calls are properly ignored.
        
        Scenario:
        - Integration turns off lights due to timer expiration
        - Should not disable itself
        """
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Wait for timer to potentially start
        await asyncio.sleep(0.1)
        
        # Simulate the integration's own turn_off call
        from tests.conftest import MockContext
        our_context = MockContext(id='our_context')
        coordinator._our_context_ids.add(our_context.id)
        
        event_data = {
            "domain": "light",
            "service": "turn_off",
            "service_data": {"entity_id": "light.living_room"},
        }
        event = type('obj', (object,), {
            'data': event_data,
            'context': our_context
        })()
        await coordinator._handle_service_call(event)
        
        # Should still be enabled (ignored our own call)
        assert coordinator.is_enabled == True

    @pytest.mark.asyncio
    async def test_service_call_ignores_other_domains(self, mock_hass, mock_config_entry):
        """Test that service calls to other domains are ignored.
        
        Scenario:
        - Service call to switch.turn_off
        - Should be ignored (not light domain)
        """
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        assert coordinator.is_enabled == True
        
        # Service call to different domain
        from tests.conftest import MockContext
        event_data = {
            "domain": "switch",
            "service": "turn_off",
            "service_data": {"entity_id": "switch.living_room"},
        }
        event = type('obj', (object,), {
            'data': event_data,
            'context': MockContext(id='external')
        })()
        await coordinator._handle_service_call(event)
        
        # Should still be enabled (different domain)
        assert coordinator.is_enabled == True

    @pytest.mark.asyncio
    async def test_service_call_ignores_other_entities(self, mock_hass, mock_config_entry):
        """Test that service calls to other light entities are ignored.
        
        Scenario:
        - Service call to light.turn_off for a different room
        - Should be ignored (different entity)
        """
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        assert coordinator.is_enabled == True
        
        # Service call to different light entity
        from tests.conftest import MockContext
        event_data = {
            "domain": "light",
            "service": "turn_off",
            "service_data": {"entity_id": "light.bedroom"},
        }
        event = type('obj', (object,), {
            'data': event_data,
            'context': MockContext(id='external')
        })()
        await coordinator._handle_service_call(event)
        
        # Should still be enabled (different entity)
        assert coordinator.is_enabled == True

