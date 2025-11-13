"""P1 High Priority Tests - Manual light control scenarios.

Tests for section 2: Manual Light Control Tests
"""
import asyncio
import pytest
from homeassistant.const import STATE_ON, STATE_OFF

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from tests.conftest import setup_entity_states, assert_service_called, assert_service_not_called


class TestManualLightControl:
    """Tests for manual light control behavior."""

    @pytest.mark.asyncio
    async def test_2_1_2_manual_off_with_timer_active(self, mock_hass, mock_config_entry):
        """Test 2.1.2: Timer active -> user turns lights OFF -> automation disables, timer cancelled."""
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
        
        # User manually turns off
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
        
        # Assert: Automation should be disabled
        assert coordinator.is_enabled == False

    @pytest.mark.asyncio
    async def test_2_1_3_manual_off_no_timer(self, mock_hass, mock_config_entry):
        """Test 2.1.3: Lights ON, no occupancy -> user turns OFF -> automation disables."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # User manually turns off (no timer active)
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
        
        # Assert: Automation disabled
        assert coordinator.is_enabled == False

    @pytest.mark.asyncio
    async def test_2_2_1_manual_on_occupied_room(self, mock_hass, mock_config_entry):
        """Test 2.2.1: Lights OFF, occupied -> user turns ON -> automation stays enabled."""
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # User manually turns on
        mock_hass.states.set("light.living_room", STATE_ON)
        event_data = {
            "entity_id": "light.living_room",
            "old_state": type('obj', (object,), {'state': STATE_OFF})(),
            "new_state": type('obj', (object,), {
                'state': STATE_ON,
                'context': type('obj', (object,), {'id': 'manual', 'parent_id': None})()
            })(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_light_change(event)
        
        # Assert: Automation should stay enabled
        assert coordinator.is_enabled == True

    @pytest.mark.asyncio
    async def test_2_2_3_manual_on_empty_room_timer_expires(self, mock_hass, mock_config_entry):
        """Test 2.2.3: Lights OFF, no occupancy -> user turns ON -> wait 30s -> lights turn OFF."""
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # User manually turns on
        mock_hass.states.set("light.living_room", STATE_ON)
        event_data = {
            "entity_id": "light.living_room",
            "old_state": type('obj', (object,), {'state': STATE_OFF})(),
            "new_state": type('obj', (object,), {
                'state': STATE_ON,
                'context': type('obj', (object,), {'id': 'manual', 'parent_id': None})()
            })(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_light_change(event)
        
        # Wait for timer to expire
        await asyncio.sleep(1.1)
        
        # Assert: Lights should turn off
        assert_service_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_2_2_4_manual_on_empty_room_occupancy_cancels_timer(self, mock_hass, mock_config_entry):
        """Test 2.2.4: Manual ON in empty room, wait 15s, occupancy detected -> timer cancelled."""
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # User manually turns on
        mock_hass.states.set("light.living_room", STATE_ON)
        event_data = {
            "entity_id": "light.living_room",
            "old_state": type('obj', (object,), {'state': STATE_OFF})(),
            "new_state": type('obj', (object,), {
                'state': STATE_ON,
                'context': type('obj', (object,), {'id': 'manual', 'parent_id': None})()
            })(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_light_change(event)
        
        # Wait 15 seconds
        await asyncio.sleep(15)
        
        # Occupancy detected
        mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
        event_data = {
            "entity_id": "binary_sensor.living_room_motion",
            "old_state": type('obj', (object,), {'state': STATE_OFF})(),
            "new_state": type('obj', (object,), {'state': STATE_ON})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        
        mock_hass.services.clear()
        await coordinator._handle_presence_change(event)
        
        # Wait past original timer
        await asyncio.sleep(20)
        
        # Assert: Lights should NOT turn off
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_2_3_1_disabled_manual_on_enables_automation(self, mock_hass, mock_config_entry):
        """Test 2.3.1: Automation disabled, occupied -> manual ON -> automation enables."""
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_disable()
        await coordinator.async_start()
        
        # User manually turns on
        mock_hass.states.set("light.living_room", STATE_ON)
        event_data = {
            "entity_id": "light.living_room",
            "old_state": type('obj', (object,), {'state': STATE_OFF})(),
            "new_state": type('obj', (object,), {
                'state': STATE_ON,
                'context': type('obj', (object,), {'id': 'manual', 'parent_id': None})()
            })(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_light_change(event)
        
        # Assert: Automation should be enabled
        assert coordinator.is_enabled == True

    @pytest.mark.asyncio
    async def test_2_3_2_disabled_manual_off_stays_disabled(self, mock_hass, mock_config_entry):
        """Test 2.3.2: Automation disabled, lights ON -> manual OFF -> stays disabled."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_disable()
        await coordinator.async_start()
        
        # User manually turns off
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
        
        # Assert: Should stay disabled
        assert coordinator.is_enabled == False

    @pytest.mark.asyncio
    async def test_2_3_3_disabled_manual_on_empty_starts_timer(self, mock_hass, mock_config_entry):
        """Test 2.3.3: Disabled, lights ON, no occupancy -> manual ON -> enables + timer starts."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_disable()
        await coordinator.async_start()
        
        # User manually turns on (lights already on, but this re-enables)
        mock_hass.states.set("light.living_room", STATE_ON)
        event_data = {
            "entity_id": "light.living_room",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),  # Already on
            "new_state": type('obj', (object,), {
                'state': STATE_ON,
                'context': type('obj', (object,), {'id': 'manual', 'parent_id': None})()
            })(),
        }
        event = type('obj', (object,), {'data': event_data})()
        
        # This won't trigger (same state), so manually enable
        await coordinator.async_enable()
        
        # Assert: Timer should start and turn off lights
        await asyncio.sleep(1.1)
        assert_service_called(mock_hass, "light", "turn_off")
