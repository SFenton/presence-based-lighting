"""Test configuration and fixtures for Presence Based Lighting."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Mock homeassistant before importing integration
import sys
from unittest.mock import MagicMock

# Create mock modules
sys.modules['homeassistant'] = MagicMock()
sys.modules['homeassistant.core'] = MagicMock()
sys.modules['homeassistant.config_entries'] = MagicMock()
sys.modules['homeassistant.const'] = MagicMock()
sys.modules['homeassistant.helpers'] = MagicMock()
sys.modules['homeassistant.helpers.event'] = MagicMock()

# Define constants we need
STATE_ON = "on"
STATE_OFF = "off"
EVENT_STATE_CHANGED = "state_changed"

from custom_components.presence_based_lighting.const import (
    CONF_ROOM_NAME,
    CONF_LIGHT_ENTITIES,
    CONF_PRESENCE_SENSORS,
    CONF_OFF_DELAY,
    DOMAIN,
)


@pytest.fixture
def mock_config_entry():
    """Return a mock config entry."""
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.data = {
        CONF_ROOM_NAME: "Living Room",
        CONF_LIGHT_ENTITIES: ["light.living_room"],
        CONF_PRESENCE_SENSORS: ["binary_sensor.living_room_motion"],
        CONF_OFF_DELAY: 30,
    }
    entry.entry_id = "test_entry_id"
    entry.unique_id = "Living Room"
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock()
    return entry


@pytest.fixture
def mock_config_entry_multi():
    """Return a mock config entry with multiple lights and sensors."""
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.data = {
        CONF_ROOM_NAME: "Living Room",
        CONF_LIGHT_ENTITIES: ["light.living_room_1", "light.living_room_2"],
        CONF_PRESENCE_SENSORS: ["binary_sensor.motion_1", "binary_sensor.motion_2"],
        CONF_OFF_DELAY: 30,
    }
    entry.entry_id = "test_entry_id"
    entry.unique_id = "Living Room"
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock()
    return entry


@pytest.fixture
def mock_config_entry_zero_delay():
    """Return a mock config entry with zero delay."""
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.data = {
        CONF_ROOM_NAME: "Bathroom",
        CONF_LIGHT_ENTITIES: ["light.bathroom"],
        CONF_PRESENCE_SENSORS: ["binary_sensor.bathroom_motion"],
        CONF_OFF_DELAY: 0,
    }
    entry.entry_id = "test_entry_id_bathroom"
    entry.unique_id = "Bathroom"
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock()
    return entry


class MockState:
    """Mock state object."""
    
    def __init__(self, entity_id, state, context=None, attributes=None):
        """Initialize mock state."""
        self.entity_id = entity_id
        self.state = state
        self.context = context or MockContext()
        self.attributes = attributes or {}


class MockContext:
    """Mock context object."""
    
    def __init__(self, id=None, parent_id=None, user_id=None):
        """Initialize mock context."""
        self.id = id or "test_context_id"
        self.parent_id = parent_id
        self.user_id = user_id


class MockHass:
    """Mock Home Assistant instance for testing."""
    
    def __init__(self):
        """Initialize mock hass."""
        self.data = {}
        self.states = MockStates()
        self.services = MockServices()
        self.config_entries = MockConfigEntries()
        self._state_listeners = []
        
    def is_state(self, entity_id, state):
        """Check if entity is in given state."""
        entity_state = self.states.get(entity_id)
        if entity_state is None:
            return False
        return entity_state.state == state


class MockStates:
    """Mock states registry."""
    
    def __init__(self):
        """Initialize mock states."""
        self._states = {}
        
    def get(self, entity_id):
        """Get state of entity."""
        return self._states.get(entity_id)
        
    def set(self, entity_id, state, context=None, attributes=None):
        """Set state of entity."""
        self._states[entity_id] = MockState(entity_id, state, context, attributes)
        
    def is_state(self, entity_id, state):
        """Check if entity is in state."""
        entity_state = self.get(entity_id)
        if entity_state is None:
            return False
        return entity_state.state == state


class MockServices:
    """Mock services registry."""
    
    def __init__(self):
        """Initialize mock services."""
        self.calls = []
        
    async def async_call(self, domain, service, service_data=None, blocking=False):
        """Call a service."""
        self.calls.append({
            "domain": domain,
            "service": service,
            "service_data": service_data or {},
            "blocking": blocking,
        })
        
    def clear(self):
        """Clear service calls."""
        self.calls = []


class MockConfigEntries:
    """Mock config entries registry."""
    
    async def async_forward_entry_setups(self, entry, platforms):
        """Forward entry setups."""
        return True
        
    async def async_unload_platforms(self, entry, platforms):
        """Unload platforms."""
        return True


@pytest.fixture
def mock_hass():
    """Return a mock Home Assistant instance."""
    return MockHass()


def setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF):
    """Set up initial entity states."""
    # Set light states
    mock_hass.states.set("light.living_room", lights_state)
    
    # Set occupancy sensor states
    mock_hass.states.set("binary_sensor.living_room_motion", occupancy_state)


def setup_multi_entity_states(mock_hass, lights_states=None, occupancy_states=None):
    """Set up initial states for multiple entities."""
    if lights_states is None:
        lights_states = [STATE_OFF, STATE_OFF]
    if occupancy_states is None:
        occupancy_states = [STATE_OFF, STATE_OFF]
        
    mock_hass.states.set("light.living_room_1", lights_states[0])
    mock_hass.states.set("light.living_room_2", lights_states[1])
    mock_hass.states.set("binary_sensor.motion_1", occupancy_states[0])
    mock_hass.states.set("binary_sensor.motion_2", occupancy_states[1])


def assert_lights_on(mock_hass, light_entities):
    """Assert all lights are on."""
    for entity in light_entities:
        assert mock_hass.states.is_state(entity, STATE_ON), f"{entity} should be ON"


def assert_lights_off(mock_hass, light_entities):
    """Assert all lights are off."""
    for entity in light_entities:
        assert mock_hass.states.is_state(entity, STATE_OFF), f"{entity} should be OFF"


def assert_service_called(mock_hass, domain, service, entity_id=None):
    """Assert a service was called."""
    for call in mock_hass.services.calls:
        if call["domain"] == domain and call["service"] == service:
            if entity_id is None:
                return True
            if entity_id in call["service_data"].get("entity_id", []):
                return True
    raise AssertionError(f"Service {domain}.{service} was not called" + 
                        (f" for {entity_id}" if entity_id else ""))


def assert_service_not_called(mock_hass, domain, service):
    """Assert a service was not called."""
    for call in mock_hass.services.calls:
        if call["domain"] == domain and call["service"] == service:
            raise AssertionError(f"Service {domain}.{service} should not have been called")
    return True
