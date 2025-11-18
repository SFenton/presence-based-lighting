"""Test configuration and fixtures for Presence Based Lighting."""
import asyncio
import sys
from unittest.mock import MagicMock, patch
import pytest
import pytest_asyncio
from aiohttp.resolver import ThreadedResolver

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest_asyncio.fixture
def event_loop():
    """Provide a real event loop for pytest-asyncio/HA fixtures."""
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()

# Mock homeassistant before importing integration
import sys
import types

# Create base homeassistant module as a real module, not a MagicMock
homeassistant_module = types.ModuleType('homeassistant')
sys.modules['homeassistant'] = homeassistant_module

# Create core and const modules
core_module = types.ModuleType('homeassistant.core')
sys.modules['homeassistant.core'] = core_module
homeassistant_module.core = core_module

const_module = types.ModuleType('homeassistant.const')
sys.modules['homeassistant.const'] = const_module
homeassistant_module.const = const_module

components_module = types.ModuleType('homeassistant.components')
sys.modules['homeassistant.components'] = components_module
homeassistant_module.components = components_module

recorder_module = types.ModuleType('homeassistant.components.recorder')
sys.modules['homeassistant.components.recorder'] = recorder_module
components_module.recorder = recorder_module

recorder_history_module = types.ModuleType('homeassistant.components.recorder.history')

def _recorder_get_significant_states(*_args, **_kwargs):
    return {}

recorder_history_module.get_significant_states = _recorder_get_significant_states
sys.modules['homeassistant.components.recorder.history'] = recorder_history_module
recorder_module.history = recorder_history_module

config_entries_module = types.ModuleType('homeassistant.config_entries')

# Create base flow classes with async methods
class _BaseFlow:
    """Base flow class."""
    async def async_show_form(self, *args, **kwargs):
        """Mock show form."""
        return {"type": "form"}
    
    def async_create_entry(self, *args, **kwargs):
        """Mock create entry - NOT async despite the name."""
        return {"type": "create_entry"}

class ConfigFlow(_BaseFlow):
    """ConfigFlow that accepts domain parameter."""
    def __init_subclass__(cls, domain=None, **kwargs):
        """Handle domain parameter in subclass definition."""
        super().__init_subclass__(**kwargs)
        if domain:
            cls.DOMAIN = domain

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._flow_unique_id = None

    async def async_set_unique_id(self, unique_id):
        """Store unique_id for the flow."""
        self._flow_unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        """Stub no-op for tests."""
        return None

class OptionsFlow(_BaseFlow):
    """OptionsFlow base class."""
    def __init__(self, config_entry):
        """Initialize options flow."""
        self._config_entry = config_entry
    
    @property
    def config_entry(self):
        """Return the config entry."""
        return self._config_entry

config_entries_module.ConfigFlow = ConfigFlow
config_entries_module.OptionsFlow = OptionsFlow
config_entries_module.ConfigEntry = type("ConfigEntry", (), {})
sys.modules['homeassistant.config_entries'] = config_entries_module
sys.modules['homeassistant.helpers.event'] = MagicMock()
sys.modules['homeassistant.helpers.entity_registry'] = MagicMock()
sys.modules['homeassistant.helpers.restore_state'] = MagicMock()

# Provide a concrete homeassistant.util module with logging helpers
util_module = types.ModuleType('homeassistant.util')
homeassistant_module.util = util_module
sys.modules['homeassistant.util'] = util_module

logging_module = types.ModuleType('homeassistant.util.logging')

def _log_exception(*args, **kwargs):
    """Stub log_exception used by HA test fixtures."""
    return None

logging_module.log_exception = _log_exception
util_module.logging = logging_module
sys.modules['homeassistant.util.logging'] = logging_module


def _slugify(value: str) -> str:
    """Simplified slugify implementation for tests."""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip().lower()
    slug = []
    for char in value:
        if char.isalnum() or char in {"_", "-"}:
            slug.append(char)
        else:
            slug.append("_")
    result = "".join(slug)
    return result.strip("_") or "entity"


util_module.slugify = _slugify

dt_module = types.ModuleType('homeassistant.util.dt')

from datetime import datetime, timezone

def _utcnow():
    return datetime.now(timezone.utc)

dt_module.utcnow = _utcnow
sys.modules['homeassistant.util.dt'] = dt_module
util_module.dt = dt_module


@pytest_asyncio.fixture(autouse=True, scope="session")
async def mock_zeroconf_resolver():
    """Override HA's zeroconf resolver with platform-friendly default."""
    with patch(
        "homeassistant.helpers.aiohttp_client._async_make_resolver",
        return_value=ThreadedResolver(),
    ):
        yield

# Set up config_validation as a real module with entity_id function
import voluptuous as vol

# Create helpers as a real module so submodules work properly
helpers_module = types.ModuleType('homeassistant.helpers')
sys.modules['homeassistant.helpers'] = helpers_module

# Create selector module with lightweight placeholder classes used in config flow
selector_module = types.ModuleType('homeassistant.helpers.selector')

class _SelectorConfig(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def _make_selector(selector_key: str):
    def _factory(config=None):
        if isinstance(config, _SelectorConfig):
            payload = dict(config)
        elif isinstance(config, dict):
            payload = dict(config)
        else:
            payload = {}
        return {selector_key: payload}
    return _factory


class _SelectOptionDict(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class _SelectSelectorMode:
    DROPDOWN = "dropdown"
    LIST = "list"


class _TextSelectorType:
    TEXT = "text"


selector_module.SelectSelector = _make_selector("select")
selector_module.SelectSelectorConfig = _SelectorConfig
selector_module.SelectSelectorMode = _SelectSelectorMode
selector_module.SelectOptionDict = _SelectOptionDict
selector_module.EntitySelector = _make_selector("entity")
selector_module.EntitySelectorConfig = _SelectorConfig
selector_module.NumberSelector = _make_selector("number")
selector_module.BooleanSelector = _make_selector("boolean")
selector_module.TextSelector = _make_selector("text")
selector_module.TextSelectorConfig = _SelectorConfig
selector_module.TextSelectorType = _TextSelectorType
sys.modules['homeassistant.helpers.selector'] = selector_module
helpers_module.selector = selector_module

cv_module = types.ModuleType('homeassistant.helpers.config_validation')
def _validate_entity_id(value: str) -> str:
    """Validate entity ID format."""
    if not isinstance(value, str) or "." not in value:
        raise vol.Invalid("invalid_entity")
    return value
cv_module.entity_id = _validate_entity_id
sys.modules['homeassistant.helpers.config_validation'] = cv_module
helpers_module.config_validation = cv_module

# Provide aiohttp_client submodule stub for fixtures expecting resolver patching
aiohttp_client_module = types.ModuleType('homeassistant.helpers.aiohttp_client')

async def _async_make_resolver(*_args, **_kwargs):
    return ThreadedResolver()

aiohttp_client_module._async_make_resolver = _async_make_resolver
sys.modules['homeassistant.helpers.aiohttp_client'] = aiohttp_client_module
helpers_module.aiohttp_client = aiohttp_client_module

# Add types to core module
import uuid
# core_module already created above
core_module.HomeAssistant = type("HomeAssistant", (), {})

# Context with id attribute
class MockContext:
    def __init__(self):
        self.id = str(uuid.uuid4())

core_module.Context = MockContext
core_module.Event = type("Event", (), {})
core_module.callback = lambda func: func  # Simple decorator that returns function as-is

# Define constants we need
STATE_ON = "on"
STATE_OFF = "off"
EVENT_STATE_CHANGED = "state_changed"
EVENT_CALL_SERVICE = "call_service"

# const_module already created above
const_module.STATE_ON = STATE_ON
const_module.STATE_OFF = STATE_OFF
const_module.EVENT_STATE_CHANGED = EVENT_STATE_CHANGED
const_module.EVENT_CALL_SERVICE = EVENT_CALL_SERVICE

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
        self._descriptions = {
            "light": {
                "turn_on": {
                    "name": "Turn On",
                    "description": "Turn the entity on",
                    "icon": "mdi:lightbulb-on",
                },
                "turn_off": {
                    "name": "Turn Off",
                    "description": "Turn the entity off",
                    "icon": "mdi:lightbulb-off",
                },
            },
        }
        
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
        self._descriptions = {
            "light": {
                "turn_on": {
                    "name": "Turn On",
                    "description": "Turn the entity on",
                    "icon": "mdi:lightbulb-on",
                },
                "turn_off": {
                    "name": "Turn Off",
                    "description": "Turn the entity off",
                    "icon": "mdi:lightbulb-off",
                },
            },
        }
        self._services = {
            "light": {
                "turn_on": {},
                "turn_off": {},
            },
        }
        
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
        self.calls.clear()

    async def async_get_all_descriptions(self):
        """Return mocked service descriptions."""
        return self._descriptions

    def async_services(self):
        """Return registered services map."""
        return self._services


class MockConfigEntries:
    """Mock config entries registry."""
    
    async def async_forward_entry_setups(self, entry, platforms):
        """Forward entry setups."""
        return True
        
    async def async_unload_platforms(self, entry, platforms):
        """Unload platforms."""
        return True

    def async_update_entry(self, entry, data=None, version=None):
        """Update entry data or version for options flow tests."""
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
