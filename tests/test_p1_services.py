"""Tests for resume_automation and pause_automation services."""

import pytest
from unittest.mock import MagicMock
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import (
    PresenceBasedLightingCoordinator,
    async_setup,
    SERVICE_RESUME_AUTOMATION,
    SERVICE_PAUSE_AUTOMATION,
)
from custom_components.presence_based_lighting.const import (
    CONF_ROOM_NAME,
    DOMAIN,
)
from tests.conftest import (
    setup_entity_states,
)


def _create_service_call(entity_id=None, target_switches=None):
    """Create a mock service call object."""
    call = MagicMock()
    call.data = {}
    if entity_id:
        call.data["entity_id"] = entity_id
    
    if target_switches:
        call.target = {"entity_id": target_switches}
    else:
        call.target = None
    
    return call


def _presence_event(mock_hass, old_state, new_state, entity_id="binary_sensor.living_room_motion"):
    """Create a presence change event."""
    mock_hass.states.set(entity_id, new_state)
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": type("State", (), {"state": old_state, "attributes": {}, "context": None})(),
                "new_state": type("State", (), {"state": new_state, "attributes": {}, "context": None})(),
            }
        },
    )()


def _external_action_event(entity_id, service, domain="light"):
    """Create an external service call event."""
    return type(
        "Event",
        (),
        {
            "data": {
                "domain": domain,
                "service": service,
                "service_data": {"entity_id": entity_id},
            },
            "context": type("Context", (), {"id": "external_context"})(),
        },
    )()


class TestServiceRegistration:
    """Test that services are registered correctly."""

    @pytest.mark.asyncio
    async def test_services_are_registered(self, mock_hass):
        """Test that resume_automation and pause_automation services are registered."""
        registered_services = []
        
        def capture_register(domain, service, handler, schema=None):
            registered_services.append((domain, service))
        
        mock_hass.services.async_register = capture_register
        
        result = await async_setup(mock_hass, {})
        
        assert result is True
        assert (DOMAIN, SERVICE_RESUME_AUTOMATION) in registered_services
        assert (DOMAIN, SERVICE_PAUSE_AUTOMATION) in registered_services


class TestResumeAutomationService:
    """Test resume_automation service behavior."""
    entity = "light.living_room"
    switch_entity = "switch.living_room_presence_lighting"

    @pytest.mark.asyncio
    async def test_resume_automation_resets_paused_flag(self, mock_hass, mock_config_entry):
        """Test that resume_automation sets automation_paused to False."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Manually pause automation
        coordinator.set_automation_paused(self.entity, True)
        assert coordinator.get_automation_paused(self.entity) is True
        
        # Resume automation
        coordinator.set_automation_paused(self.entity, False)
        assert coordinator.get_automation_paused(self.entity) is False

    @pytest.mark.asyncio
    async def test_resume_automation_allows_presence_control(self, mock_hass, mock_config_entry):
        """Test that after resuming, presence changes affect the light."""
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Pause automation
        coordinator.set_automation_paused(self.entity, True)
        mock_hass.services.clear()
        
        # Presence detected should NOT turn on light (paused)
        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_OFF, STATE_ON)
        )
        # Check that turn_on was not called
        turn_on_calls = [c for c in mock_hass.services.calls 
                         if c["domain"] == "light" and c["service"] == "turn_on"]
        assert len(turn_on_calls) == 0
        
        # Resume automation
        coordinator.set_automation_paused(self.entity, False)
        mock_hass.services.clear()
        
        # Now presence detected should turn on light
        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_OFF, STATE_ON)
        )
        turn_on_calls = [c for c in mock_hass.services.calls 
                         if c["domain"] == "light" and c["service"] == "turn_on"]
        assert len(turn_on_calls) == 1

    @pytest.mark.asyncio
    async def test_resume_specific_entity_only(self, mock_hass, mock_config_entry_multi):
        """Test that resume_automation can target a specific entity."""
        from tests.conftest import setup_multi_entity_states
        setup_multi_entity_states(mock_hass, lights_states=[STATE_ON, STATE_ON])
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_multi)
        await coordinator.async_start()
        
        # Pause both entities
        coordinator.set_automation_paused("light.living_room_1", True)
        coordinator.set_automation_paused("light.living_room_2", True)
        
        assert coordinator.get_automation_paused("light.living_room_1") is True
        assert coordinator.get_automation_paused("light.living_room_2") is True
        
        # Resume only the first entity
        coordinator.set_automation_paused("light.living_room_1", False)
        
        assert coordinator.get_automation_paused("light.living_room_1") is False
        assert coordinator.get_automation_paused("light.living_room_2") is True


class TestPauseAutomationService:
    """Test pause_automation service behavior."""
    entity = "light.living_room"
    switch_entity = "switch.living_room_presence_lighting"

    @pytest.mark.asyncio
    async def test_pause_automation_sets_paused_flag(self, mock_hass, mock_config_entry):
        """Test that pause_automation sets automation_paused to True."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Initially not paused
        assert coordinator.get_automation_paused(self.entity) is False
        
        # Pause automation
        coordinator.set_automation_paused(self.entity, True)
        assert coordinator.get_automation_paused(self.entity) is True

    @pytest.mark.asyncio
    async def test_pause_automation_prevents_presence_control(self, mock_hass, mock_config_entry):
        """Test that paused automation prevents presence changes from affecting the light."""
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        mock_hass.services.clear()
        
        # Pause automation
        coordinator.set_automation_paused(self.entity, True)
        
        # Presence detected should NOT turn on light
        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_OFF, STATE_ON)
        )
        
        turn_on_calls = [c for c in mock_hass.services.calls 
                         if c["domain"] == "light" and c["service"] == "turn_on"]
        assert len(turn_on_calls) == 0

    @pytest.mark.asyncio
    async def test_pause_specific_entity_only(self, mock_hass, mock_config_entry_multi):
        """Test that pause_automation can target a specific entity."""
        from tests.conftest import setup_multi_entity_states
        setup_multi_entity_states(mock_hass, lights_states=[STATE_OFF, STATE_OFF])
        
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry_multi)
        await coordinator.async_start()
        
        # Both should start unpaused
        assert coordinator.get_automation_paused("light.living_room_1") is False
        assert coordinator.get_automation_paused("light.living_room_2") is False
        
        # Pause only the first entity
        coordinator.set_automation_paused("light.living_room_1", True)
        
        assert coordinator.get_automation_paused("light.living_room_1") is True
        assert coordinator.get_automation_paused("light.living_room_2") is False


class TestServiceHandlerIntegration:
    """Test the full service handler integration."""
    entity = "light.living_room"
    switch_entity = "switch.living_room_presence_lighting"

    @pytest.mark.asyncio
    async def test_service_handler_finds_coordinator(self, mock_hass, mock_config_entry):
        """Test that service handlers can find the correct coordinator."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        
        # Set up hass.data with the coordinator
        mock_hass.data[DOMAIN] = {}
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator
        await coordinator.async_start()
        
        # Pause the entity
        coordinator.set_automation_paused(self.entity, True)
        assert coordinator.get_automation_paused(self.entity) is True
        
        # Simulate what the service handler would do
        room_name = mock_config_entry.data.get(CONF_ROOM_NAME, "").lower().replace(" ", "_")
        expected_switch = f"switch.{room_name}_presence_lighting"
        assert expected_switch == self.switch_entity
        
        # Find and resume via coordinator
        for entry_id, coord in mock_hass.data[DOMAIN].items():
            if isinstance(coord, PresenceBasedLightingCoordinator):
                for entity_id in coord._entity_states:
                    coord.set_automation_paused(entity_id, False)
        
        assert coordinator.get_automation_paused(self.entity) is False

    @pytest.mark.asyncio
    async def test_resume_after_manual_off_allows_presence_control(self, mock_hass, mock_config_entry):
        """Test resuming automation after manual off allows presence to control light again."""
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()
        
        # Simulate manual off which pauses automation
        coordinator.set_automation_paused(self.entity, True)
        mock_hass.states.set(self.entity, STATE_OFF)
        mock_hass.services.clear()
        
        # Presence change should not affect light while paused
        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_ON, STATE_OFF)
        )
        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_OFF, STATE_ON)
        )
        
        turn_on_calls = [c for c in mock_hass.services.calls 
                         if c["domain"] == "light" and c["service"] == "turn_on"]
        assert len(turn_on_calls) == 0
        
        # Resume automation
        coordinator.set_automation_paused(self.entity, False)
        mock_hass.services.clear()
        
        # Now presence on should turn on the light
        mock_hass.states.set("binary_sensor.living_room_motion", STATE_OFF)
        await coordinator._handle_presence_change(
            _presence_event(mock_hass, STATE_OFF, STATE_ON)
        )
        
        turn_on_calls = [c for c in mock_hass.services.calls 
                         if c["domain"] == "light" and c["service"] == "turn_on"]
        assert len(turn_on_calls) == 1
