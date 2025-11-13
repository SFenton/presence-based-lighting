"""P1 High Priority Tests - Switch toggle scenarios.

Tests for section 3: Automation Switch Toggle Tests
"""
import asyncio
import pytest
from homeassistant.const import STATE_ON, STATE_OFF

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from tests.conftest import setup_entity_states, assert_service_called, assert_service_not_called


class TestSwitchToggle:
    """Tests for automation switch enable/disable behavior."""

    @pytest.mark.asyncio
    async def test_3_1_1_disable_switch_lights_stay_on(self, mock_hass, mock_config_entry):
        """Test 3.1.1: Lights ON, occupied, enabled -> disable switch -> lights stay ON, automation stops."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Disable switch
        await coordinator.async_disable()
        
        # Assert: Automation disabled, lights stay as-is
        assert coordinator.is_enabled == False
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_3_1_2_disable_switch_cancels_timer(self, mock_hass, mock_config_entry):
        """Test 3.1.2: Timer active -> disable switch -> timer cancelled, lights stay ON."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Start timer
        event_data = {
            "entity_id": "binary_sensor.living_room_motion",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),
            "new_state": type('obj', (object,), {'state': STATE_OFF})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_presence_change(event)
        
        # Wait a bit
        await asyncio.sleep(10)
        
        # Disable switch
        await coordinator.async_disable()
        
        # Wait past timer expiration
        await asyncio.sleep(25)
        
        # Assert: Lights should NOT turn off (timer was cancelled)
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_3_1_3_disable_switch_lights_off_stays_off(self, mock_hass, mock_config_entry):
        """Test 3.1.3: Lights OFF, occupied, enabled -> disable switch -> lights stay OFF."""
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Disable switch
        await coordinator.async_disable()
        
        # Assert: Lights stay off
        assert coordinator.is_enabled == False
        assert_service_not_called(mock_hass, "light", "turn_on")

    @pytest.mark.asyncio
    async def test_3_2_2_enable_switch_occupied_lights_on_stays_on(self, mock_hass, mock_config_entry):
        """Test 3.2.2: Lights ON, occupied, disabled -> enable switch -> stays ON, normal automation."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_disable()
        await coordinator.async_start()
        
        mock_hass.services.clear()
        
        # Enable switch
        await coordinator.async_enable()
        
        # Assert: Lights stay on, automation enabled
        assert coordinator.is_enabled == True
        assert_service_not_called(mock_hass, "light", "turn_on")  # Already on
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_3_2_4_enable_switch_empty_room_timer_expires(self, mock_hass, mock_config_entry):
        """Test 3.2.4: Lights ON, no occupancy, disabled -> enable switch -> timer -> lights OFF."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_disable()
        await coordinator.async_start()
        
        # Enable switch
        await coordinator.async_enable()
        
        # Wait for timer
        await asyncio.sleep(30.1)
        
        # Assert: Lights should turn off
        assert_service_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_3_2_5_enable_switch_empty_room_lights_off_no_action(self, mock_hass, mock_config_entry):
        """Test 3.2.5: Lights OFF, no occupancy, disabled -> enable switch -> no action."""
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_disable()
        await coordinator.async_start()
        
        # Enable switch
        await coordinator.async_enable()
        
        # Assert: No action taken
        assert coordinator.is_enabled == True
        assert_service_not_called(mock_hass, "light", "turn_on")
        assert_service_not_called(mock_hass, "light", "turn_off")
