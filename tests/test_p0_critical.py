"""P0 Critical Tests - Must Pass scenarios for Presence Based Lighting.

These tests cover the most critical functionality:
- Basic occupancy detection
- Timer expiration
- Manual override behavior
"""
import asyncio
import pytest
from homeassistant.const import STATE_ON, STATE_OFF
from unittest.mock import AsyncMock, patch

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.const import (
    CONF_LIGHT_ENTITIES,
    CONF_PRESENCE_SENSORS,
    CONF_OFF_DELAY,
)
from tests.conftest import (
    setup_entity_states,
    assert_service_called,
)


class TestP0Critical:
    """P0 Critical test cases."""

    @pytest.mark.asyncio
    async def test_1_1_1_occupancy_detected_lights_turn_on(self, mock_hass, mock_config_entry):
        """Test 1.1.1: Lights OFF, no occupancy, automation ON -> occupancy detected -> lights turn ON."""
        # Setup: Lights OFF, Occupancy CLEAR, Switch ON (default)
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        # Create coordinator
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        assert coordinator.is_enabled == True
        
        await coordinator.async_start()
        
        # Simulate occupancy detected
        mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
        
        # Trigger the presence change handler
        from homeassistant.core import Event
        event_data = {
            "entity_id": "binary_sensor.living_room_motion",
            "old_state": mock_hass.states.get("binary_sensor.living_room_motion"),
            "new_state": mock_hass.states.get("binary_sensor.living_room_motion"),
        }
        event_data["old_state"] = type('obj', (object,), {'state': STATE_OFF})()
        event_data["new_state"].state = STATE_ON
        
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_presence_change(event)
        
        # Assert: Lights should be turned ON
        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")
    
    @pytest.mark.asyncio
    async def test_1_3_2_timer_expires_lights_turn_off(self, mock_hass, mock_config_entry):
        """Test 1.3.2: Lights ON, occupancy clears, wait 30s -> lights turn OFF."""
        # Setup: Lights ON, Occupancy initially OCCUPIED, Switch ON
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Simulate occupancy clears
        mock_hass.states.set("binary_sensor.living_room_motion", STATE_OFF)
        
        # Trigger presence change
        event_data = {
            "entity_id": "binary_sensor.living_room_motion",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),
            "new_state": type('obj', (object,), {'state': STATE_OFF})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_presence_change(event)
        
        # Wait for timer (30 seconds)
        await asyncio.sleep(1.1)  # Slightly over 30s
        
        # Assert: Lights should be turned OFF
        assert_service_called(mock_hass, "light", "turn_off", "light.living_room")

    @pytest.mark.asyncio
    async def test_2_1_1_manual_off_disables_automation(self, mock_hass, mock_config_entry):
        """Test 2.1.1: Lights ON, occupied, automation ON -> user turns lights OFF -> automation disables."""
        # Setup: Lights ON, Occupancy OCCUPIED, Switch ON
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Simulate manual light OFF (context.parent_id is None for manual)
        mock_hass.states.set(
            "light.living_room", 
            STATE_OFF,
            context=type('obj', (object,), {'id': 'manual_id', 'parent_id': None})()
        )
        
        # Trigger light change
        event_data = {
            "entity_id": "light.living_room",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),
            "new_state": mock_hass.states.get("light.living_room"),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_light_change(event)
        
        # Assert: Automation should be disabled
        assert coordinator.is_enabled == False

    @pytest.mark.asyncio
    async def test_2_2_2_manual_on_empty_room_starts_timer(self, mock_hass, mock_config_entry):
        """Test 2.2.2: Lights OFF, no occupancy, automation ON -> user turns ON -> timer starts."""
        # Setup: Lights OFF, Occupancy CLEAR, Switch ON
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # User manually turns lights ON
        mock_hass.states.set("light.living_room", STATE_ON)
        
        # Trigger light change
        event_data = {
            "entity_id": "light.living_room",
            "old_state": type('obj', (object,), {'state': STATE_OFF})(),
            "new_state": type('obj', (object,), {
                'state': STATE_ON,
                'context': type('obj', (object,), {'id': 'manual_id', 'parent_id': None})()
            })(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_light_change(event)
        
        # Assert: Timer should start and lights should turn off after delay
        await asyncio.sleep(1.1)
        assert_service_called(mock_hass, "light", "turn_off", "light.living_room")

    @pytest.mark.asyncio
    async def test_3_2_1_enable_switch_occupied_room_lights_on(self, mock_hass, mock_config_entry):
        """Test 3.2.1: Lights OFF, occupied, switch OFF -> toggle switch ON -> lights turn ON."""
        # Setup: Lights OFF, Occupancy OCCUPIED, Switch OFF
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        coordinator._is_enabled = False  # Start disabled
        await coordinator.async_start()
        
        # User enables the switch
        await coordinator.async_enable()
        
        # Assert: Lights should turn ON (room is occupied)
        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")

    @pytest.mark.asyncio
    async def test_3_2_3_enable_switch_empty_room_lights_on_timer_starts(self, mock_hass, mock_config_entry):
        """Test 3.2.3: Lights ON, no occupancy, switch OFF -> toggle ON -> timer starts."""
        # Setup: Lights ON, Occupancy CLEAR, Switch OFF
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        coordinator._is_enabled = False
        await coordinator.async_start()
        
        # User enables the switch
        await coordinator.async_enable()
        
        # Assert: Timer should start (lights on in empty room)
        await asyncio.sleep(1.1)
        assert_service_called(mock_hass, "light", "turn_off", "light.living_room")
