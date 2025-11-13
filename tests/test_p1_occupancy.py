"""P1 High Priority Tests - Basic occupancy detection scenarios.

Tests for section 1: Basic Occupancy Detection Tests
"""
import asyncio
import pytest
from homeassistant.const import STATE_ON, STATE_OFF

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from tests.conftest import setup_entity_states, assert_service_called, assert_service_not_called


class TestBasicOccupancyDetection:
    """Tests for basic occupancy detection."""

    @pytest.mark.asyncio
    async def test_1_1_2_no_occupancy_lights_stay_off(self, mock_hass, mock_config_entry):
        """Test 1.1.2: Lights OFF, no occupancy, automation ON -> occupancy stays clear -> no change."""
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # No event occurs, just wait
        await asyncio.sleep(0.1)
        
        # Assert: No service calls should have been made
        assert_service_not_called(mock_hass, "light", "turn_on")
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_1_2_1_occupied_lights_stay_on(self, mock_hass, mock_config_entry):
        """Test 1.2.1: Lights ON, occupied, automation ON -> stays occupied -> lights stay ON."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # No state changes
        await asyncio.sleep(0.1)
        
        # Assert: No service calls (lights already on, should stay on)
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_1_3_1_occupancy_clears_timer_starts(self, mock_hass, mock_config_entry):
        """Test 1.3.1: Lights ON, occupied -> occupancy clears -> timer starts, lights stay ON."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Clear services to check for new calls
        mock_hass.services.clear()
        
        # Occupancy clears
        mock_hass.states.set("binary_sensor.living_room_motion", STATE_OFF)
        event_data = {
            "entity_id": "binary_sensor.living_room_motion",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),
            "new_state": type('obj', (object,), {'state': STATE_OFF})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_presence_change(event)
        
        # Wait a short time (not full delay)
        await asyncio.sleep(1)
        
        # Assert: Lights should still be ON (timer hasn't expired)
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_1_3_3_reoccupancy_cancels_timer(self, mock_hass, mock_config_entry):
        """Test 1.3.3: Timer active -> occupancy detected -> timer cancelled, lights stay ON."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Start timer by having occupancy clear (already clear in setup)
        # Trigger to start the timer explicitly
        event_data = {
            "entity_id": "binary_sensor.living_room_motion",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),
            "new_state": type('obj', (object,), {'state': STATE_OFF})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_presence_change(event)
        
        # Wait 15 seconds (half the timer)
        await asyncio.sleep(15)
        
        # Occupancy detected again
        mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
        event_data = {
            "entity_id": "binary_sensor.living_room_motion",
            "old_state": type('obj', (object,), {'state': STATE_OFF})(),
            "new_state": type('obj', (object,), {'state': STATE_ON})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        
        mock_hass.services.clear()
        await coordinator._handle_presence_change(event)
        
        # Wait past original timer expiration
        await asyncio.sleep(20)
        
        # Assert: Lights should NOT turn off (timer was cancelled)
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_1_4_1_disabled_occupancy_no_action(self, mock_hass, mock_config_entry):
        """Test 1.4.1: Automation disabled -> occupancy detected -> no action."""
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_disable()  # Disable automation
        await coordinator.async_start()
        
        # Occupancy detected
        mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
        event_data = {
            "entity_id": "binary_sensor.living_room_motion",
            "old_state": type('obj', (object,), {'state': STATE_OFF})(),
            "new_state": type('obj', (object,), {'state': STATE_ON})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_presence_change(event)
        
        # Assert: No lights should turn on
        assert_service_not_called(mock_hass, "light", "turn_on")

    @pytest.mark.asyncio
    async def test_1_4_2_disabled_occupancy_clears_no_timer(self, mock_hass, mock_config_entry):
        """Test 1.4.2: Automation disabled, lights ON -> occupancy clears -> no timer starts."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_disable()
        await coordinator.async_start()
        
        # Occupancy clears
        mock_hass.states.set("binary_sensor.living_room_motion", STATE_OFF)
        event_data = {
            "entity_id": "binary_sensor.living_room_motion",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),
            "new_state": type('obj', (object,), {'state': STATE_OFF})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_presence_change(event)
        
        # Wait for would-be timer expiration
        await asyncio.sleep(31)
        
        # Assert: Lights should NOT turn off
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_1_4_3_disabled_no_automation_indefinitely(self, mock_hass, mock_config_entry):
        """Test 1.4.3: Automation disabled -> wait indefinitely -> no automation action."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_disable()
        await coordinator.async_start()
        
        # Wait a while
        await asyncio.sleep(1)
        
        # Assert: No automation actions
        assert_service_not_called(mock_hass, "light", "turn_on")
        assert_service_not_called(mock_hass, "light", "turn_off")
