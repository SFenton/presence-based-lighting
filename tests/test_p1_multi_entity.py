"""P1 High Priority Tests - Multi-sensor and multi-light scenarios.

Tests for section 5: Multi-Sensor Scenarios
"""
import asyncio
import pytest
from homeassistant.const import STATE_ON, STATE_OFF

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from tests.conftest import setup_multi_entity_states, assert_service_called, assert_service_not_called


class TestMultiSensor:
    """Tests for multiple sensors and lights."""

    @pytest.mark.asyncio
    async def test_5_1_1_any_sensor_triggers_lights(self, mock_hass, mock_config_entry_multi):
        """Test 5.1.1: Multiple sensors, all clear -> one triggers -> lights turn ON."""
        setup_multi_entity_states(
            mock_hass,
            lights_states=[STATE_OFF, STATE_OFF],
            occupancy_states=[STATE_OFF, STATE_OFF]
        )
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_multi)
        await coordinator.async_start()
        
        # Sensor 1 detects occupancy
        mock_hass.states.set("binary_sensor.motion_1", STATE_ON)
        event_data = {
            "entity_id": "binary_sensor.motion_1",
            "old_state": type('obj', (object,), {'state': STATE_OFF})(),
            "new_state": type('obj', (object,), {'state': STATE_ON})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_presence_change(event)
        
        # Assert: Lights should turn on
        assert_service_called(mock_hass, "light", "turn_on")

    @pytest.mark.asyncio
    async def test_5_1_2_one_sensor_stays_occupied_lights_stay_on(self, mock_hass, mock_config_entry_multi):
        """Test 5.1.2: Both sensors occupied, one clears but other stays -> lights stay ON."""
        setup_multi_entity_states(
            mock_hass,
            lights_states=[STATE_ON, STATE_ON],
            occupancy_states=[STATE_ON, STATE_ON]
        )
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_multi)
        await coordinator.async_start()
        
        # Sensor 1 clears
        mock_hass.states.set("binary_sensor.motion_1", STATE_OFF)
        event_data = {
            "entity_id": "binary_sensor.motion_1",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),
            "new_state": type('obj', (object,), {'state': STATE_OFF})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        
        mock_hass.services.clear()
        await coordinator._handle_presence_change(event)
        
        # Wait to ensure no timer expires
        await asyncio.sleep(1)
        
        # Assert: Lights should stay on (sensor 2 still occupied)
        assert_service_not_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_5_1_3_all_sensors_clear_timer_starts(self, mock_hass, mock_config_entry_multi):
        """Test 5.1.3: One sensor occupied -> clears -> timer starts."""
        setup_multi_entity_states(
            mock_hass,
            lights_states=[STATE_ON, STATE_ON],
            occupancy_states=[STATE_ON, STATE_OFF]
        )
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_multi)
        await coordinator.async_start()
        
        # Last occupied sensor clears
        mock_hass.states.set("binary_sensor.motion_1", STATE_OFF)
        event_data = {
            "entity_id": "binary_sensor.motion_1",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),
            "new_state": type('obj', (object,), {'state': STATE_OFF})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_presence_change(event)
        
        # Wait for timer
        await asyncio.sleep(30.1)
        
        # Assert: Lights should turn off
        assert_service_called(mock_hass, "light", "turn_off")

    @pytest.mark.asyncio
    async def test_5_1_4_any_sensor_reoccupies_cancels_timer(self, mock_hass, mock_config_entry_multi):
        """Test 5.1.4: Timer active -> any sensor detects -> timer cancelled."""
        setup_multi_entity_states(
            mock_hass,
            lights_states=[STATE_ON, STATE_ON],
            occupancy_states=[STATE_OFF, STATE_OFF]
        )
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_multi)
        await coordinator.async_start()
        
        # Start timer (simulate clearance event)
        event_data = {
            "entity_id": "binary_sensor.motion_1",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),
            "new_state": type('obj', (object,), {'state': STATE_OFF})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_presence_change(event)
        
        # Wait 15 seconds
        await asyncio.sleep(15)
        
        # Sensor 2 detects occupancy
        mock_hass.states.set("binary_sensor.motion_2", STATE_ON)
        event_data = {
            "entity_id": "binary_sensor.motion_2",
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
    async def test_5_2_1_all_lights_turn_on_together(self, mock_hass, mock_config_entry_multi):
        """Test 5.2.1: Multiple lights, all off -> occupancy -> all turn ON."""
        setup_multi_entity_states(
            mock_hass,
            lights_states=[STATE_OFF, STATE_OFF],
            occupancy_states=[STATE_OFF, STATE_OFF]
        )
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_multi)
        await coordinator.async_start()
        
        # Occupancy detected
        mock_hass.states.set("binary_sensor.motion_1", STATE_ON)
        event_data = {
            "entity_id": "binary_sensor.motion_1",
            "old_state": type('obj', (object,), {'state': STATE_OFF})(),
            "new_state": type('obj', (object,), {'state': STATE_ON})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_presence_change(event)
        
        # Assert: Service should be called with both lights
        service_call = None
        for call in mock_hass.services.calls:
            if call["domain"] == "light" and call["service"] == "turn_on":
                service_call = call
                break
        
        assert service_call is not None
        # Both lights should be in the entity_id list
        entity_ids = service_call["service_data"]["entity_id"]
        assert "light.living_room_1" in entity_ids
        assert "light.living_room_2" in entity_ids

    @pytest.mark.asyncio
    async def test_5_2_2_all_lights_turn_off_together(self, mock_hass, mock_config_entry_multi):
        """Test 5.2.2: Multiple lights on -> occupancy clears -> timer -> all turn OFF."""
        setup_multi_entity_states(
            mock_hass,
            lights_states=[STATE_ON, STATE_ON],
            occupancy_states=[STATE_ON, STATE_ON]
        )
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_multi)
        await coordinator.async_start()
        
        # All occupancy clears
        mock_hass.states.set("binary_sensor.motion_1", STATE_OFF)
        mock_hass.states.set("binary_sensor.motion_2", STATE_OFF)
        
        event_data = {
            "entity_id": "binary_sensor.motion_2",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),
            "new_state": type('obj', (object,), {'state': STATE_OFF})(),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_presence_change(event)
        
        # Wait for timer
        await asyncio.sleep(30.1)
        
        # Assert: Both lights should turn off
        service_call = None
        for call in mock_hass.services.calls:
            if call["domain"] == "light" and call["service"] == "turn_off":
                service_call = call
                break
        
        assert service_call is not None
        entity_ids = service_call["service_data"]["entity_id"]
        assert "light.living_room_1" in entity_ids
        assert "light.living_room_2" in entity_ids

    @pytest.mark.asyncio
    async def test_5_2_3_partial_manual_off_disables_automation(self, mock_hass, mock_config_entry_multi):
        """Test 5.2.3: Both lights ON -> user turns off one -> automation disables."""
        setup_multi_entity_states(
            mock_hass,
            lights_states=[STATE_ON, STATE_ON],
            occupancy_states=[STATE_ON, STATE_ON]
        )
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_multi)
        await coordinator.async_start()
        
        # User manually turns off one light
        mock_hass.states.set(
            "light.living_room_1",
            STATE_OFF,
            context=type('obj', (object,), {'id': 'manual', 'parent_id': None})()
        )
        event_data = {
            "entity_id": "light.living_room_1",
            "old_state": type('obj', (object,), {'state': STATE_ON})(),
            "new_state": mock_hass.states.get("light.living_room_1"),
        }
        event = type('obj', (object,), {'data': event_data})()
        await coordinator._handle_light_change(event)
        
        # Assert: Automation should disable
        assert coordinator.is_enabled == False
