"""Tests for sensor mappings and real_last_changed integration in the coordinator."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from homeassistant.const import STATE_ON, STATE_OFF
from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_ENTITY_ID,
    CONF_OFF_DELAY,
    CONF_PRESENCE_SENSORS,
    CONF_CLEARING_SENSORS,
    CONF_PRESENCE_SENSOR_MAPPINGS,
    CONF_PRESENCE_DETECTED_SERVICE,
    CONF_PRESENCE_DETECTED_STATE,
    CONF_PRESENCE_CLEARED_SERVICE,
    CONF_PRESENCE_CLEARED_STATE,
    CONF_RESPECTS_PRESENCE_ALLOWED,
    CONF_AUTOMATION_MODE,
    CONF_ROOM_NAME,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    AUTOMATION_MODE_AUTOMATIC,
    DEFAULT_DETECTED_SERVICE,
    DEFAULT_DETECTED_STATE,
    DEFAULT_CLEARED_SERVICE,
    DEFAULT_CLEARED_STATE,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.bus = MagicMock()
    hass.bus.async_listen = MagicMock(return_value=lambda: None)
    
    # Setup state tracking
    states = {}
    
    def is_state(entity_id, state):
        return states.get(entity_id) == state
    
    def get_state(entity_id):
        state_value = states.get(entity_id)
        if state_value:
            state_obj = MagicMock()
            state_obj.state = state_value
            return state_obj
        return None
    
    hass.states.is_state = is_state
    hass.states.get = get_state
    hass._states = states
    
    return hass


@pytest.fixture
def mock_entry_with_mapping():
    """Create a mock config entry with sensor mappings."""
    entry = MagicMock()
    entry.entry_id = "test_entry_123"
    entry.data = {
        CONF_ROOM_NAME: "Test Room",
        CONF_OFF_DELAY: 0,
        # User selected the real_last_changed sensor
        CONF_PRESENCE_SENSORS: ["sensor.motion_real_last_changed"],
        CONF_CLEARING_SENSORS: [],
        # Mapping from real_last_changed to source binary_sensor
        CONF_PRESENCE_SENSOR_MAPPINGS: {
            "sensor.motion_real_last_changed": "binary_sensor.motion",
        },
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.test_light",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_AUTOMATION_MODE: AUTOMATION_MODE_AUTOMATIC,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
            }
        ],
    }
    return entry


class TestSensorMappingsIntegration:
    """Tests for sensor mappings in the coordinator."""

    @pytest.mark.asyncio
    async def test_coordinator_resolves_sensor_mappings(self, mock_hass, mock_entry_with_mapping):
        """Coordinator should resolve sensor mappings during start."""
        # Mock the state tracking
        mock_hass.helpers = MagicMock()
        mock_hass.helpers.event = MagicMock()
        mock_hass.helpers.event.async_track_state_change_event = MagicMock(return_value=lambda: None)
        
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_mapping)
            await coordinator.async_start()
        
        # Verify the resolved sensors are stored
        assert hasattr(coordinator, "_resolved_presence_sensors")
        assert "binary_sensor.motion" in coordinator._resolved_presence_sensors
        
        # Verify the mapping back to original sensor is stored
        assert hasattr(coordinator, "_sensor_to_original")
        assert coordinator._sensor_to_original["binary_sensor.motion"] == "sensor.motion_real_last_changed"

    @pytest.mark.asyncio
    async def test_is_any_occupied_uses_resolved_sensors(self, mock_hass, mock_entry_with_mapping):
        """_is_any_occupied should check resolved source sensors."""
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_mapping)
            await coordinator.async_start()
        
        # Source sensor is off - should not be occupied
        mock_hass._states["binary_sensor.motion"] = STATE_OFF
        assert not coordinator._is_any_occupied()
        
        # Source sensor is on - should be occupied
        mock_hass._states["binary_sensor.motion"] = STATE_ON
        assert coordinator._is_any_occupied()

    @pytest.mark.asyncio
    async def test_are_clearing_sensors_clear_uses_resolved_sensors(self, mock_hass, mock_entry_with_mapping):
        """_are_clearing_sensors_clear should check resolved source sensors."""
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_mapping)
            await coordinator.async_start()
        
        # Source sensor is on - not clear
        mock_hass._states["binary_sensor.motion"] = STATE_ON
        assert not coordinator._are_clearing_sensors_clear()
        
        # Source sensor is off - clear
        mock_hass._states["binary_sensor.motion"] = STATE_OFF
        assert coordinator._are_clearing_sensors_clear()


class TestNoMappingFallback:
    """Tests for fallback behavior when no mappings are configured."""

    @pytest.fixture
    def mock_entry_no_mapping(self):
        """Create a mock config entry without sensor mappings."""
        entry = MagicMock()
        entry.entry_id = "test_entry_456"
        entry.data = {
            CONF_ROOM_NAME: "Test Room",
            CONF_OFF_DELAY: 0,
            CONF_PRESENCE_SENSORS: ["binary_sensor.pir"],
            CONF_CLEARING_SENSORS: [],
            CONF_PRESENCE_SENSOR_MAPPINGS: {},  # Empty mappings
            CONF_CONTROLLED_ENTITIES: [
                {
                    CONF_ENTITY_ID: "light.test_light",
                    CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                    CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                    CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                    CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                    CONF_RESPECTS_PRESENCE_ALLOWED: True,
                    CONF_AUTOMATION_MODE: AUTOMATION_MODE_AUTOMATIC,
                    CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                }
            ],
        }
        return entry

    @pytest.mark.asyncio
    async def test_coordinator_uses_original_sensors_when_no_mapping(self, mock_hass, mock_entry_no_mapping):
        """Coordinator should use original sensors when no mappings exist."""
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_no_mapping)
            await coordinator.async_start()
        
        # The original sensor should be in resolved sensors
        assert "binary_sensor.pir" in coordinator._resolved_presence_sensors

    @pytest.mark.asyncio
    async def test_is_any_occupied_works_without_mapping(self, mock_hass, mock_entry_no_mapping):
        """_is_any_occupied should work with original sensors when no mapping."""
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_no_mapping)
            await coordinator.async_start()
        
        mock_hass._states["binary_sensor.pir"] = STATE_OFF
        assert not coordinator._is_any_occupied()
        
        mock_hass._states["binary_sensor.pir"] = STATE_ON
        assert coordinator._is_any_occupied()


class TestMixedSensors:
    """Tests for mixed sensor configurations (some mapped, some not)."""

    @pytest.fixture
    def mock_entry_mixed(self):
        """Create a mock config entry with mixed sensor types."""
        entry = MagicMock()
        entry.entry_id = "test_entry_789"
        entry.data = {
            CONF_ROOM_NAME: "Mixed Room",
            CONF_OFF_DELAY: 0,
            CONF_PRESENCE_SENSORS: [
                "sensor.motion1_real_last_changed",  # Will be mapped
                "binary_sensor.motion2",  # Direct binary sensor
            ],
            CONF_CLEARING_SENSORS: [],
            CONF_PRESENCE_SENSOR_MAPPINGS: {
                "sensor.motion1_real_last_changed": "binary_sensor.motion1",
            },
            CONF_CONTROLLED_ENTITIES: [
                {
                    CONF_ENTITY_ID: "light.test_light",
                    CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                    CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                    CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                    CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                    CONF_RESPECTS_PRESENCE_ALLOWED: True,
                    CONF_AUTOMATION_MODE: AUTOMATION_MODE_AUTOMATIC,
                    CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                }
            ],
        }
        return entry

    @pytest.mark.asyncio
    async def test_coordinator_handles_mixed_sensors(self, mock_hass, mock_entry_mixed):
        """Coordinator should handle mix of mapped and unmapped sensors."""
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_mixed)
            await coordinator.async_start()
        
        # Both resolved sensors should be present
        assert "binary_sensor.motion1" in coordinator._resolved_presence_sensors
        assert "binary_sensor.motion2" in coordinator._resolved_presence_sensors

    @pytest.mark.asyncio
    async def test_is_any_occupied_checks_all_resolved_sensors(self, mock_hass, mock_entry_mixed):
        """_is_any_occupied should check all resolved sensors."""
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_mixed)
            await coordinator.async_start()
        
        # Both off - not occupied
        mock_hass._states["binary_sensor.motion1"] = STATE_OFF
        mock_hass._states["binary_sensor.motion2"] = STATE_OFF
        assert not coordinator._is_any_occupied()
        
        # One on - occupied
        mock_hass._states["binary_sensor.motion1"] = STATE_ON
        mock_hass._states["binary_sensor.motion2"] = STATE_OFF
        assert coordinator._is_any_occupied()
        
        # Other one on - occupied
        mock_hass._states["binary_sensor.motion1"] = STATE_OFF
        mock_hass._states["binary_sensor.motion2"] = STATE_ON
        assert coordinator._is_any_occupied()
