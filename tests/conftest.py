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
sys.modules['homeassistant.helpers.entity_registry'] = MagicMock()
sys.modules['homeassistant.helpers.restore_state'] = MagicMock()

# Define constants we need
STATE_ON = "on"
STATE_OFF = "off"
EVENT_STATE_CHANGED = "state_changed"

homeassistant_const = sys.modules['homeassistant.const']
homeassistant_const.STATE_ON = STATE_ON
homeassistant_const.STATE_OFF = STATE_OFF
homeassistant_const.EVENT_STATE_CHANGED = EVENT_STATE_CHANGED

from custom_components.presence_based_lighting.const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_INITIAL_PRESENCE_ALLOWED,
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
    DEFAULT_DISABLE_ON_EXTERNAL,
    DEFAULT_INITIAL_PRESENCE_ALLOWED,
    DOMAIN,
)


@pytest.fixture
def mock_config_entry():
    """Return a mock config entry with 1 second delay for fast tests."""
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.version = 2
    entry.data = {
        CONF_ROOM_NAME: "Living Room",
        CONF_PRESENCE_SENSORS: ["binary_sensor.living_room_motion"],
        CONF_OFF_DELAY: 1,
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
            }
        ],
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
    entry.version = 2
    entry.data = {
        CONF_ROOM_NAME: "Living Room",
        CONF_PRESENCE_SENSORS: ["binary_sensor.motion_1", "binary_sensor.motion_2"],
        CONF_OFF_DELAY: 1,
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.living_room_1",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
            },
            {
                CONF_ENTITY_ID: "light.living_room_2",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
            },
        ],
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
    entry.version = 2
    entry.data = {
        CONF_ROOM_NAME: "Bathroom",
        CONF_PRESENCE_SENSORS: ["binary_sensor.bathroom_motion"],
        CONF_OFF_DELAY: 0,
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.bathroom",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
            }
        ],
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
        self.bus = MockBus()
        self._state_listeners = []
        self._context_counter = 0
        
    @property
    def context(self):
        """Get a new context for service calls."""
        self._context_counter += 1
        return MockContext(id=f"test_context_{self._context_counter}")
        
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
        
    async def async_call(self, domain, service, service_data=None, blocking=False, context=None):
        """Call a service."""
        self.calls.append({
            "domain": domain,
            "service": service,
            "service_data": service_data or {},
            "blocking": blocking,
            "context": context,
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

    def async_update_entry(self, entry, data=None, version=None):
        """Update entry data/version for migration tests."""
        if data is not None:
            entry.data = data
        if version is not None:
            entry.version = version


class MockBus:
    """Mock event bus."""
    
    def __init__(self):
        """Initialize mock bus."""
        self._listeners = {}
        
    def async_listen(self, event_type, listener):
        """Register an event listener."""
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(listener)
        
        # Return a function to remove the listener
        def remove_listener():
            if event_type in self._listeners:
                self._listeners[event_type].remove(listener)
        
        return remove_listener


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
            target = call["service_data"].get("entity_id", [])
            if isinstance(target, str):
                target_entities = [target]
            else:
                target_entities = target
            if entity_id in target_entities:
                return True
    raise AssertionError(f"Service {domain}.{service} was not called" + 
                        (f" for {entity_id}" if entity_id else ""))


def assert_service_not_called(mock_hass, domain, service):
    """Assert a service was not called."""
    for call in mock_hass.services.calls:
        if call["domain"] == domain and call["service"] == service:
            raise AssertionError(f"Service {domain}.{service} should not have been called")
    return True
