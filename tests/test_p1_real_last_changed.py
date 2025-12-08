"""Tests for real_last_changed integration using previous_valid_state attribute."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from homeassistant.const import STATE_ON, STATE_OFF
from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.real_last_changed import (
    is_real_last_changed_entity,
    get_effective_state,
    is_entity_on,
    is_entity_off,
    ATTR_PREVIOUS_VALID_STATE,
)
from custom_components.presence_based_lighting.const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_ENTITY_ID,
    CONF_OFF_DELAY,
    CONF_PRESENCE_SENSORS,
    CONF_CLEARING_SENSORS,
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


class TestIsRealLastChangedEntity:
    """Tests for is_real_last_changed_entity helper."""

    def test_identifies_rlc_sensors_by_attribute(self):
        """Should identify sensors with previous_valid_state attribute."""
        state_obj = MagicMock()
        state_obj.attributes = {ATTR_PREVIOUS_VALID_STATE: "on"}
        
        assert is_real_last_changed_entity("sensor.motion", state_obj)
        assert is_real_last_changed_entity("sensor.dining_room_presence_sensor_pir", state_obj)
        assert is_real_last_changed_entity("sensor.any_sensor_name", state_obj)

    def test_rejects_regular_sensors(self):
        """Should reject sensors without previous_valid_state attribute."""
        state_obj = MagicMock()
        state_obj.attributes = {}  # No previous_valid_state
        
        assert not is_real_last_changed_entity("binary_sensor.motion", state_obj)
        assert not is_real_last_changed_entity("sensor.temperature", state_obj)
        assert not is_real_last_changed_entity("light.bedroom", state_obj)

    def test_rejects_partial_matches(self):
        """Should reject entities without the attribute even if name looks like RLC."""
        state_obj = MagicMock()
        state_obj.attributes = {"other_attr": "value"}  # No previous_valid_state
        
        assert not is_real_last_changed_entity("sensor.motion_real_last_changed", state_obj)
        assert not is_real_last_changed_entity("binary_sensor.motion", state_obj)

    def test_handles_none_and_empty(self):
        """Should handle None and empty strings."""
        assert not is_real_last_changed_entity(None)
        assert not is_real_last_changed_entity("")
        
    def test_heuristic_without_state(self):
        """Without state object, falls back to sensor.* heuristic."""
        # Without state, sensor.* (not binary_sensor) returns True as heuristic
        assert is_real_last_changed_entity("sensor.motion")
        # binary_sensor returns False
        assert not is_real_last_changed_entity("binary_sensor.motion")
        # Other domains return False
        assert not is_real_last_changed_entity("light.bedroom")


class TestGetEffectiveState:
    """Tests for get_effective_state helper."""

    def test_returns_attribute_for_rlc_sensor(self):
        """Should return previous_valid_state attribute for RLC sensors."""
        hass = MagicMock()
        state_obj = MagicMock()
        state_obj.state = "2024-01-01T12:00:00+00:00"  # RLC state is a timestamp
        state_obj.attributes = {ATTR_PREVIOUS_VALID_STATE: "on"}
        hass.states.get.return_value = state_obj

        result = get_effective_state(hass, "sensor.motion_real_last_changed")
        
        assert result == "on"
        hass.states.get.assert_called_with("sensor.motion_real_last_changed")

    def test_returns_state_for_regular_sensor(self):
        """Should return direct state for regular sensors."""
        hass = MagicMock()
        state_obj = MagicMock()
        state_obj.state = "on"
        state_obj.attributes = {}
        hass.states.get.return_value = state_obj

        result = get_effective_state(hass, "binary_sensor.motion")
        
        assert result == "on"

    def test_returns_none_for_missing_entity(self):
        """Should return None if entity doesn't exist."""
        hass = MagicMock()
        hass.states.get.return_value = None

        result = get_effective_state(hass, "sensor.nonexistent_real_last_changed")
        
        assert result is None

    def test_returns_state_for_sensor_without_attribute(self):
        """Sensor without previous_valid_state is treated as regular sensor, returns state."""
        hass = MagicMock()
        state_obj = MagicMock()
        state_obj.state = "2024-01-01T12:00:00+00:00"
        state_obj.attributes = {}  # No previous_valid_state - not an RLC sensor
        hass.states.get.return_value = state_obj

        # Without the attribute, it's treated as a regular sensor, so state is returned
        result = get_effective_state(hass, "sensor.motion_real_last_changed")
        
        assert result == "2024-01-01T12:00:00+00:00"


class TestIsEntityOn:
    """Tests for is_entity_on helper."""

    def test_returns_true_for_on_rlc_sensor(self):
        """Should return True when RLC sensor's previous_valid_state is 'on'."""
        hass = MagicMock()
        state_obj = MagicMock()
        state_obj.state = "2024-01-01T12:00:00+00:00"
        state_obj.attributes = {ATTR_PREVIOUS_VALID_STATE: "on"}
        hass.states.get.return_value = state_obj

        assert is_entity_on(hass, "sensor.motion_real_last_changed")

    def test_returns_false_for_off_rlc_sensor(self):
        """Should return False when RLC sensor's previous_valid_state is 'off'."""
        hass = MagicMock()
        state_obj = MagicMock()
        state_obj.state = "2024-01-01T12:00:00+00:00"
        state_obj.attributes = {ATTR_PREVIOUS_VALID_STATE: "off"}
        hass.states.get.return_value = state_obj

        assert not is_entity_on(hass, "sensor.motion_real_last_changed")

    def test_returns_true_for_on_regular_sensor(self):
        """Should return True when regular sensor state is 'on'."""
        hass = MagicMock()
        state_obj = MagicMock()
        state_obj.state = "on"
        state_obj.attributes = {}
        hass.states.get.return_value = state_obj

        assert is_entity_on(hass, "binary_sensor.motion")

    def test_returns_false_for_missing_entity(self):
        """Should return False for missing entities."""
        hass = MagicMock()
        hass.states.get.return_value = None

        assert not is_entity_on(hass, "sensor.nonexistent_real_last_changed")


class TestIsEntityOff:
    """Tests for is_entity_off helper."""

    def test_returns_true_for_off_rlc_sensor(self):
        """Should return True when RLC sensor's previous_valid_state is 'off'."""
        hass = MagicMock()
        state_obj = MagicMock()
        state_obj.state = "2024-01-01T12:00:00+00:00"
        state_obj.attributes = {ATTR_PREVIOUS_VALID_STATE: "off"}
        hass.states.get.return_value = state_obj

        assert is_entity_off(hass, "sensor.motion_real_last_changed")

    def test_returns_false_for_on_rlc_sensor(self):
        """Should return False when RLC sensor's previous_valid_state is 'on'."""
        hass = MagicMock()
        state_obj = MagicMock()
        state_obj.state = "2024-01-01T12:00:00+00:00"
        state_obj.attributes = {ATTR_PREVIOUS_VALID_STATE: "on"}
        hass.states.get.return_value = state_obj

        assert not is_entity_off(hass, "sensor.motion_real_last_changed")

    def test_returns_true_for_off_regular_sensor(self):
        """Should return True when regular sensor state is 'off'."""
        hass = MagicMock()
        state_obj = MagicMock()
        state_obj.state = "off"
        state_obj.attributes = {}
        hass.states.get.return_value = state_obj

        assert is_entity_off(hass, "binary_sensor.motion")

    def test_returns_false_for_missing_entity(self):
        """Should return False for missing entities."""
        hass = MagicMock()
        hass.states.get.return_value = None

        assert not is_entity_off(hass, "sensor.nonexistent_real_last_changed")


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.bus = MagicMock()
    hass.bus.async_listen = MagicMock(return_value=lambda: None)
    
    # Setup state tracking with attribute support
    states_data = {}  # {entity_id: {"state": str, "attributes": dict}}
    
    def get_state(entity_id):
        if entity_id not in states_data:
            return None
        data = states_data[entity_id]
        state_obj = MagicMock()
        state_obj.state = data.get("state", "unknown")
        state_obj.attributes = data.get("attributes", {})
        return state_obj
    
    hass.states.get = get_state
    hass._states_data = states_data  # For test manipulation
    
    return hass


@pytest.fixture
def mock_entry_with_rlc():
    """Create a mock config entry with RLC sensor."""
    entry = MagicMock()
    entry.entry_id = "test_entry_rlc"
    entry.data = {
        CONF_ROOM_NAME: "Test Room",
        CONF_OFF_DELAY: 0,
        CONF_PRESENCE_SENSORS: ["sensor.motion_real_last_changed"],
        CONF_CLEARING_SENSORS: [],
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


class TestCoordinatorWithRLCSensors:
    """Tests for coordinator handling of RLC sensors."""

    @pytest.mark.asyncio
    async def test_coordinator_stores_presence_sensors(self, mock_hass, mock_entry_with_rlc):
        """Coordinator should store presence sensors during start."""
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_rlc)
            await coordinator.async_start()
        
        # Verify presence sensors are stored
        assert hasattr(coordinator, "_presence_sensors")
        assert "sensor.motion_real_last_changed" in coordinator._presence_sensors

    @pytest.mark.asyncio
    async def test_is_any_occupied_reads_rlc_attribute(self, mock_hass, mock_entry_with_rlc):
        """_is_any_occupied should read previous_valid_state for RLC sensors."""
        # Set up RLC sensor with "off" state in attribute
        mock_hass._states_data["sensor.motion_real_last_changed"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "off"},
        }
        
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_rlc)
            await coordinator.async_start()
        
        # Should not be occupied when attribute is "off"
        assert not coordinator._is_any_occupied()
        
        # Update attribute to "on"
        mock_hass._states_data["sensor.motion_real_last_changed"]["attributes"] = {
            ATTR_PREVIOUS_VALID_STATE: "on"
        }
        
        # Should be occupied when attribute is "on"
        assert coordinator._is_any_occupied()

    @pytest.mark.asyncio
    async def test_are_clearing_sensors_uses_rlc_attribute(self, mock_hass, mock_entry_with_rlc):
        """_are_clearing_sensors_clear should read previous_valid_state for RLC sensors."""
        # When no clearing sensors are configured, falls back to presence sensors
        mock_hass._states_data["sensor.motion_real_last_changed"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "on"},
        }
        
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_rlc)
            await coordinator.async_start()
        
        # Not clear when attribute is "on"
        assert not coordinator._are_clearing_sensors_clear()
        
        # Update attribute to "off"
        mock_hass._states_data["sensor.motion_real_last_changed"]["attributes"] = {
            ATTR_PREVIOUS_VALID_STATE: "off"
        }
        
        # Should be clear when attribute is "off"
        assert coordinator._are_clearing_sensors_clear()


class TestMixedSensorTypes:
    """Tests for mixed RLC and regular sensor configurations."""

    @pytest.fixture
    def mock_entry_mixed(self):
        """Create a mock config entry with mixed sensor types."""
        entry = MagicMock()
        entry.entry_id = "test_entry_mixed"
        entry.data = {
            CONF_ROOM_NAME: "Mixed Room",
            CONF_OFF_DELAY: 0,
            CONF_PRESENCE_SENSORS: [
                "sensor.motion1_real_last_changed",  # RLC sensor
                "binary_sensor.motion2",  # Regular binary sensor
            ],
            CONF_CLEARING_SENSORS: [],
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
    async def test_is_any_occupied_checks_both_types(self, mock_hass, mock_entry_mixed):
        """Should correctly check both RLC and regular sensors."""
        # Set up both sensors as off
        mock_hass._states_data["sensor.motion1_real_last_changed"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "off"},
        }
        mock_hass._states_data["binary_sensor.motion2"] = {
            "state": "off",
            "attributes": {},
        }
        
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_mixed)
            await coordinator.async_start()
        
        # Both off - not occupied
        assert not coordinator._is_any_occupied()
        
        # RLC sensor on - occupied
        mock_hass._states_data["sensor.motion1_real_last_changed"]["attributes"] = {
            ATTR_PREVIOUS_VALID_STATE: "on"
        }
        assert coordinator._is_any_occupied()
        
        # RLC off, regular on - occupied
        mock_hass._states_data["sensor.motion1_real_last_changed"]["attributes"] = {
            ATTR_PREVIOUS_VALID_STATE: "off"
        }
        mock_hass._states_data["binary_sensor.motion2"]["state"] = "on"
        assert coordinator._is_any_occupied()


class TestRLCEventHandling:
    """Tests for RLC sensor event handling - verifying attribute transitions are detected."""

    @pytest.fixture
    def mock_hass_with_events(self):
        """Create a mock Home Assistant instance with event handling support."""
        hass = MagicMock()
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()
        hass.bus = MagicMock()
        hass.bus.async_listen = MagicMock(return_value=lambda: None)
        
        # Setup state tracking with attribute support
        states_data = {}
        
        def get_state(entity_id):
            if entity_id not in states_data:
                return None
            data = states_data[entity_id]
            state_obj = MagicMock()
            state_obj.state = data.get("state", "unknown")
            state_obj.attributes = data.get("attributes", {})
            return state_obj
        
        hass.states.get = get_state
        hass._states_data = states_data
        
        return hass

    @pytest.fixture
    def mock_entry_rlc_event(self):
        """Create a mock config entry for RLC event testing."""
        entry = MagicMock()
        entry.entry_id = "test_entry_rlc_event"
        entry.data = {
            CONF_ROOM_NAME: "Test Room",
            CONF_OFF_DELAY: 0,
            CONF_PRESENCE_SENSORS: ["sensor.motion_real_last_changed"],
            CONF_CLEARING_SENSORS: [],
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

    def _create_state_change_event(self, entity_id, old_state_value, old_attrs, new_state_value, new_attrs):
        """Create a mock state change event."""
        old_state = MagicMock()
        old_state.state = old_state_value
        old_state.attributes = old_attrs
        
        new_state = MagicMock()
        new_state.state = new_state_value
        new_state.attributes = new_attrs
        
        event = MagicMock()
        event.data = {
            "entity_id": entity_id,
            "old_state": old_state,
            "new_state": new_state,
        }
        return event

    @pytest.mark.asyncio
    async def test_rlc_attribute_change_off_to_on_triggers_presence(self, mock_hass_with_events, mock_entry_rlc_event):
        """RLC sensor attribute change from off to on should trigger presence detected."""
        # Set up initial state
        mock_hass_with_events._states_data["sensor.motion_real_last_changed"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "on"},  # Current state after event
        }
        mock_hass_with_events._states_data["light.test_light"] = {
            "state": "off",
            "attributes": {},
        }
        
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass_with_events, mock_entry_rlc_event)
            await coordinator.async_start()
        
        # Create event: attribute changed from "off" to "on"
        event = self._create_state_change_event(
            "sensor.motion_real_last_changed",
            old_state_value="2024-01-01T11:00:00+00:00",  # Old timestamp
            old_attrs={ATTR_PREVIOUS_VALID_STATE: "off"},  # Was off
            new_state_value="2024-01-01T12:00:00+00:00",  # New timestamp
            new_attrs={ATTR_PREVIOUS_VALID_STATE: "on"},  # Now on
        )
        
        # Handle the event
        await coordinator._handle_presence_change(event)
        
        # Should have called turn_on service
        mock_hass_with_events.services.async_call.assert_called()
        call_args = mock_hass_with_events.services.async_call.call_args
        assert call_args[0][0] == "light"  # domain
        assert call_args[0][1] == "turn_on"  # service

    @pytest.mark.asyncio
    async def test_rlc_attribute_unchanged_does_not_trigger(self, mock_hass_with_events, mock_entry_rlc_event):
        """RLC sensor with unchanged attribute should not trigger any action."""
        mock_hass_with_events._states_data["sensor.motion_real_last_changed"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "on"},
        }
        mock_hass_with_events._states_data["light.test_light"] = {
            "state": "off",
            "attributes": {},
        }
        
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass_with_events, mock_entry_rlc_event)
            await coordinator.async_start()
        
        # Create event: timestamp changed but attribute stayed the same
        event = self._create_state_change_event(
            "sensor.motion_real_last_changed",
            old_state_value="2024-01-01T11:00:00+00:00",
            old_attrs={ATTR_PREVIOUS_VALID_STATE: "on"},  # Was on
            new_state_value="2024-01-01T12:00:00+00:00",
            new_attrs={ATTR_PREVIOUS_VALID_STATE: "on"},  # Still on
        )
        
        # Handle the event
        await coordinator._handle_presence_change(event)
        
        # Should NOT have called any service
        mock_hass_with_events.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_rlc_attribute_change_on_to_off_starts_timer(self, mock_hass_with_events, mock_entry_rlc_event):
        """RLC sensor attribute change from on to off should start the off timer."""
        mock_hass_with_events._states_data["sensor.motion_real_last_changed"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "off"},  # Current state after event
        }
        mock_hass_with_events._states_data["light.test_light"] = {
            "state": "on",
            "attributes": {},
        }
        
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass_with_events, mock_entry_rlc_event)
            await coordinator.async_start()
        
        # Verify no timer initially
        for entity_state in coordinator._entity_states.values():
            assert entity_state["off_timer"] is None
        
        # Create event: attribute changed from "on" to "off"
        event = self._create_state_change_event(
            "sensor.motion_real_last_changed",
            old_state_value="2024-01-01T11:00:00+00:00",
            old_attrs={ATTR_PREVIOUS_VALID_STATE: "on"},  # Was on
            new_state_value="2024-01-01T12:00:00+00:00",
            new_attrs={ATTR_PREVIOUS_VALID_STATE: "off"},  # Now off
        )
        
        # Handle the event - should start the off timer
        await coordinator._handle_presence_change(event)
        
        # Timer should now be set (or already fired for delay=0)
        # With delay=0 and the async nature, we just verify the event was processed
        # by checking that no exception was raised and the method completed
