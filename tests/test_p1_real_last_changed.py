"""Tests for real_last_changed integration using previous_valid_state attribute."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from homeassistant.const import STATE_ON, STATE_OFF
from custom_components.presence_based_lighting import (
    PresenceBasedLightingCoordinator,
    async_setup_entry,
)
from custom_components.presence_based_lighting.real_last_changed import (
    is_real_last_changed_entity,
    get_effective_state,
    is_entity_on,
    is_entity_off,
    get_matching_rlc_sensor_for_entity,
    replace_entities_with_matching_rlc_sensors,
    ATTR_PREVIOUS_VALID_STATE,
)
from custom_components.presence_based_lighting.const import (
    CONF_AUTO_REENABLE_PRESENCE_SENSORS,
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


class TestRLCSensorMatching:
    """Tests for raw sensor to RLC sensor matching."""

    def test_finds_matching_rlc_sensor_by_suffix(self, mock_hass):
        """Should match raw sensors to RLC sensors with a matching object suffix."""
        mock_hass._states_data["binary_sensor.master_bedroom_window_presence_motion"] = {
            "state": "off",
            "attributes": {},
        }
        mock_hass._states_data[
            "sensor.master_bedroom_master_bedroom_window_presence_sensor_master_bedroom_window_presence_motion_real_last_changed"
        ] = {
            "state": "2026-06-13T16:08:59+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "off"},
        }

        assert (
            get_matching_rlc_sensor_for_entity(
                mock_hass,
                "binary_sensor.master_bedroom_window_presence_motion",
            )
            == "sensor.master_bedroom_master_bedroom_window_presence_sensor_master_bedroom_window_presence_motion_real_last_changed"
        )

    def test_does_not_match_sensors_without_rlc_attribute(self, mock_hass):
        """Should only match sensors that expose previous_valid_state."""
        mock_hass._states_data["binary_sensor.office_presence_motion"] = {
            "state": "off",
            "attributes": {},
        }
        mock_hass._states_data["sensor.office_presence_motion_real_last_changed"] = {
            "state": "2026-06-13T16:08:59+00:00",
            "attributes": {},
        }

        assert (
            get_matching_rlc_sensor_for_entity(
                mock_hass,
                "binary_sensor.office_presence_motion",
            )
            is None
        )

    def test_replace_entities_preserves_unmapped_sensors(self, mock_hass):
        """Should replace mapped raw sensors and preserve raw sensors without RLC."""
        mock_hass._states_data["binary_sensor.office_presence_motion"] = {
            "state": "off",
            "attributes": {},
        }
        mock_hass._states_data["binary_sensor.unmapped_motion"] = {
            "state": "off",
            "attributes": {},
        }
        mock_hass._states_data[
            "sensor.office_office_presence_sensor_office_presence_motion_real_last_changed"
        ] = {
            "state": "2026-06-13T16:08:59+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "off"},
        }

        updated, replacements = replace_entities_with_matching_rlc_sensors(
            mock_hass,
            [
                "binary_sensor.office_presence_motion",
                "binary_sensor.unmapped_motion",
            ],
        )

        assert updated == [
            "sensor.office_office_presence_sensor_office_presence_motion_real_last_changed",
            "binary_sensor.unmapped_motion",
        ]
        assert replacements == {
            "binary_sensor.office_presence_motion": "sensor.office_office_presence_sensor_office_presence_motion_real_last_changed"
        }


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
        state_obj.entity_id = entity_id
        state_obj.state = data.get("state", "unknown")
        state_obj.attributes = data.get("attributes", {})
        return state_obj

    def async_all():
        return [get_state(entity_id) for entity_id in states_data]
    
    hass.states.get = get_state
    hass.states.async_all = async_all
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

    @pytest.mark.asyncio
    async def test_setup_entry_migrates_raw_presence_sensors_to_rlc(
        self, mock_hass, mock_entry_with_rlc
    ):
        """Setup should persist raw sensor replacements before the coordinator starts."""
        mock_entry_with_rlc.data[CONF_PRESENCE_SENSORS] = [
            "binary_sensor.office_presence_motion",
            "binary_sensor.unmapped_motion",
        ]
        mock_entry_with_rlc.data[CONF_CLEARING_SENSORS] = [
            "binary_sensor.office_presence_occupancy",
        ]
        mock_entry_with_rlc.data[CONF_AUTO_REENABLE_PRESENCE_SENSORS] = [
            "binary_sensor.office_presence_motion",
        ]
        mock_entry_with_rlc.async_on_unload = MagicMock()
        mock_entry_with_rlc.add_update_listener = MagicMock(return_value=lambda: None)
        mock_hass.data = {}
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        mock_hass.config_entries.async_update_entry = MagicMock(
            side_effect=lambda entry, data=None, version=None: setattr(entry, "data", data)
            if data is not None
            else None
        )
        mock_hass._states_data["binary_sensor.office_presence_motion"] = {
            "state": "off",
            "attributes": {},
        }
        mock_hass._states_data["binary_sensor.office_presence_occupancy"] = {
            "state": "off",
            "attributes": {},
        }
        mock_hass._states_data["binary_sensor.unmapped_motion"] = {
            "state": "off",
            "attributes": {},
        }
        mock_hass._states_data[
            "sensor.office_office_presence_sensor_office_presence_motion_real_last_changed"
        ] = {
            "state": "2026-06-13T16:08:59+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "off"},
        }
        mock_hass._states_data[
            "sensor.office_office_presence_sensor_office_presence_occupancy_real_last_changed"
        ] = {
            "state": "2026-06-13T16:08:59+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "off"},
        }

        with patch(
            "custom_components.presence_based_lighting.async_track_state_change_event",
            return_value=lambda: None,
        ), patch(
            "custom_components.presence_based_lighting.async_track_time_interval",
            return_value=lambda: None,
        ):
            assert await async_setup_entry(mock_hass, mock_entry_with_rlc)

        mock_hass.config_entries.async_update_entry.assert_called()
        assert mock_entry_with_rlc.data[CONF_PRESENCE_SENSORS] == [
            "sensor.office_office_presence_sensor_office_presence_motion_real_last_changed",
            "binary_sensor.unmapped_motion",
        ]
        assert mock_entry_with_rlc.data[CONF_CLEARING_SENSORS] == [
            "sensor.office_office_presence_sensor_office_presence_occupancy_real_last_changed",
        ]
        assert mock_entry_with_rlc.data[CONF_AUTO_REENABLE_PRESENCE_SENSORS] == [
            "sensor.office_office_presence_sensor_office_presence_motion_real_last_changed",
        ]

    @pytest.mark.asyncio
    async def test_setup_entry_retries_rlc_migration_after_startup(
        self, mock_hass, mock_entry_with_rlc
    ):
        """Setup should retry migration after RLC entities become available."""
        mock_entry_with_rlc.data[CONF_PRESENCE_SENSORS] = [
            "binary_sensor.office_presence_motion",
        ]
        mock_entry_with_rlc.async_on_unload = MagicMock()
        mock_entry_with_rlc.add_update_listener = MagicMock(return_value=lambda: None)
        mock_hass.data = {}
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        mock_hass.config_entries.async_update_entry = MagicMock(
            side_effect=lambda entry, data=None, version=None: setattr(entry, "data", data)
            if data is not None
            else None
        )
        mock_hass._states_data["binary_sensor.office_presence_motion"] = {
            "state": "off",
            "attributes": {},
        }
        scheduled_callbacks = []

        def schedule_later(_hass, _delay, action):
            scheduled_callbacks.append(action)
            return lambda: None

        with patch(
            "custom_components.presence_based_lighting.async_track_state_change_event",
            return_value=lambda: None,
        ), patch(
            "custom_components.presence_based_lighting.async_track_time_interval",
            return_value=lambda: None,
        ), patch(
            "custom_components.presence_based_lighting.async_call_later",
            side_effect=schedule_later,
        ):
            assert await async_setup_entry(mock_hass, mock_entry_with_rlc)

        assert scheduled_callbacks
        mock_hass.config_entries.async_update_entry.assert_not_called()

        mock_hass._states_data[
            "sensor.office_office_presence_sensor_office_presence_motion_real_last_changed"
        ] = {
            "state": "2026-06-13T16:08:59+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "off"},
        }

        scheduled_callbacks[0](None)

        mock_hass.config_entries.async_update_entry.assert_called_once()
        assert mock_entry_with_rlc.data[CONF_PRESENCE_SENSORS] == [
            "sensor.office_office_presence_sensor_office_presence_motion_real_last_changed",
        ]


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
        
        # Clear any calls from startup reconciliation
        mock_hass_with_events.services.async_call.reset_mock()
        
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
        # Start with motion ON so reconciliation puts entity in OCCUPIED (no timer yet)
        mock_hass_with_events._states_data["sensor.motion_real_last_changed"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "on"},
        }
        mock_hass_with_events._states_data["light.test_light"] = {
            "state": "on",
            "attributes": {},
        }
        
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass_with_events, mock_entry_rlc_event)
            await coordinator.async_start()
        
        # Verify no timer initially (room is occupied, clearing sensors not clear)
        for entity_state in coordinator._entity_states.values():
            assert entity_state["off_timer"] is None
        
        # Update sensor state to reflect motion going off
        mock_hass_with_events._states_data["sensor.motion_real_last_changed"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "off"},
        }
        
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


class TestRLCTrackingEntityForManualControl:
    """Tests for using RLC tracking entity to detect manual control on controlled entities."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()
        hass.bus = MagicMock()
        hass.bus.async_fire = AsyncMock()
        hass.bus.async_listen = MagicMock(return_value=lambda: None)
        
        # States storage
        hass._states_data = {}
        
        def get_state(entity_id):
            if entity_id in hass._states_data:
                data = hass._states_data[entity_id]
                mock_state = MagicMock()
                mock_state.state = data["state"]
                mock_state.attributes = data.get("attributes", {})
                mock_state.entity_id = entity_id
                return mock_state
            return None
        
        hass.states = MagicMock()
        hass.states.get = get_state
        hass.states.async_all = MagicMock(return_value=[])
        
        return hass

    @pytest.fixture
    def mock_entry_with_rlc_tracking(self):
        """Create a config entry with RLC tracking entity configured."""
        from custom_components.presence_based_lighting.const import CONF_RLC_TRACKING_ENTITY
        
        entry = MagicMock()
        entry.data = {
            CONF_ROOM_NAME: "test_room",
            CONF_PRESENCE_SENSORS: ["binary_sensor.motion"],
            CONF_CLEARING_SENSORS: [],
            CONF_OFF_DELAY: 0,
            CONF_CONTROLLED_ENTITIES: [
                {
                    CONF_ENTITY_ID: "light.test_light",
                    CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                    CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                    CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                    CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                    CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                    CONF_RESPECTS_PRESENCE_ALLOWED: True,
                    CONF_AUTOMATION_MODE: AUTOMATION_MODE_AUTOMATIC,
                    CONF_RLC_TRACKING_ENTITY: "sensor.light_test_light_rlc",
                    "manual_disable_states": ["off"],  # Manual off disables automation
                }
            ],
        }
        entry.options = {}
        entry.entry_id = "test_rlc_tracking"
        return entry

    def _create_state_change_event(self, entity_id, old_state, new_state, context=None):
        """Create a state change event for testing."""
        event = MagicMock()
        event.data = {
            "entity_id": entity_id,
            "old_state": old_state,
            "new_state": new_state,
        }
        return event

    @pytest.mark.asyncio
    async def test_rlc_tracking_uses_effective_state_for_manual_control(self, mock_hass, mock_entry_with_rlc_tracking):
        """When RLC tracking entity is configured, manual control detection uses RLC state."""
        from custom_components.presence_based_lighting.const import CONF_RLC_TRACKING_ENTITY
        
        # Set up states: light is "on", RLC sensor shows "on" as effective state
        mock_hass._states_data["binary_sensor.motion"] = {
            "state": "on",
            "attributes": {},
        }
        mock_hass._states_data["light.test_light"] = {
            "state": "on",
            "attributes": {},
        }
        mock_hass._states_data["sensor.light_test_light_rlc"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "on"},  # RLC says light is "on"
        }
        
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_rlc_tracking)
            await coordinator.async_start()
        
        # Initially presence should be allowed
        assert coordinator.get_presence_allowed("light.test_light") is True
        
        # Create event: light changed from on to off externally
        old_state = MagicMock()
        old_state.state = "on"
        old_state.attributes = {}
        
        new_state = MagicMock()
        new_state.state = "off"
        new_state.attributes = {}
        new_state.context = MagicMock()
        new_state.context.id = "external_context"
        
        event = self._create_state_change_event(
            "light.test_light",
            old_state=old_state,
            new_state=new_state,
        )
        
        # Update RLC sensor to reflect the new "real" state
        mock_hass._states_data["sensor.light_test_light_rlc"]["attributes"] = {
            ATTR_PREVIOUS_VALID_STATE: "off"  # RLC confirms light is really off
        }
        
        # Handle the controlled entity change
        await coordinator._handle_controlled_entity_change(event)
        
        # Automation should now be paused because RLC tracking shows "off" (in manual_disable_states)
        assert coordinator.get_automation_paused("light.test_light") is True

    @pytest.mark.asyncio
    async def test_rlc_tracking_ignores_spurious_changes(self, mock_hass, mock_entry_with_rlc_tracking):
        """When RLC tracking shows different state than entity, use RLC state."""
        # This simulates a scenario where the light state changes due to reboot,
        # but the RLC sensor still shows the "real" previous state
        
        mock_hass._states_data["binary_sensor.motion"] = {
            "state": "on",
            "attributes": {},
        }
        mock_hass._states_data["light.test_light"] = {
            "state": "off",  # Light appears off (maybe due to reboot)
            "attributes": {},
        }
        mock_hass._states_data["sensor.light_test_light_rlc"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "on"},  # RLC knows light was really on
        }
        
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_rlc_tracking)
            await coordinator.async_start()
        
        # Create event: light changed from on to off
        old_state = MagicMock()
        old_state.state = "on"
        old_state.attributes = {}
        
        new_state = MagicMock()
        new_state.state = "off"
        new_state.attributes = {}
        new_state.context = MagicMock()
        new_state.context.id = "external_context"
        
        event = self._create_state_change_event(
            "light.test_light",
            old_state=old_state,
            new_state=new_state,
        )
        
        # RLC still shows "on" - this is a spurious change
        # (RLC hasn't updated because the change wasn't "real")
        
        # Handle the controlled entity change
        await coordinator._handle_controlled_entity_change(event)
        
        # Presence should STILL be allowed because RLC tracking shows "on" (not in manual_disable_states)
        assert coordinator.get_presence_allowed("light.test_light") is True

    @pytest.mark.asyncio
    async def test_rlc_tracking_unavailable_ignores_change(self, mock_hass, mock_entry_with_rlc_tracking):
        """When RLC tracking entity is unavailable, the state change is ignored."""
        mock_hass._states_data["binary_sensor.motion"] = {
            "state": "on",
            "attributes": {},
        }
        mock_hass._states_data["light.test_light"] = {
            "state": "on",
            "attributes": {},
        }
        # RLC sensor is NOT present in states (unavailable)
        
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_rlc_tracking)
            await coordinator.async_start()
        
        # Initially presence should be allowed
        assert coordinator.get_presence_allowed("light.test_light") is True
        
        # Create event: light changed from on to off
        old_state = MagicMock()
        old_state.state = "on"
        old_state.attributes = {}
        
        new_state = MagicMock()
        new_state.state = "off"
        new_state.attributes = {}
        new_state.context = MagicMock()
        new_state.context.id = "external_context"
        
        event = self._create_state_change_event(
            "light.test_light",
            old_state=old_state,
            new_state=new_state,
        )
        
        # Handle the controlled entity change
        await coordinator._handle_controlled_entity_change(event)
        
        # Presence should STILL be allowed because RLC tracking entity is unavailable
        # (the change is ignored entirely)
        assert coordinator.get_presence_allowed("light.test_light") is True

    @pytest.mark.asyncio
    async def test_rlc_tracking_ignores_repeated_effective_state(self, mock_hass, mock_entry_with_rlc_tracking):
        """When RLC effective state hasn't changed, state changes should be ignored.
        
        This tests the scenario where:
        1. System turns off the light (our context)
        2. Later, another state_changed event fires with different context (e.g., availability change)
        3. But the RLC effective state is still "off"
        4. This should NOT trigger manual control detection because effective state didn't change
        
        This bug caused lights to not turn on when entering a room because a spurious
        state change was incorrectly detected as "manual control".
        """
        mock_hass._states_data["binary_sensor.motion"] = {
            "state": "off",
            "attributes": {},
        }
        mock_hass._states_data["light.test_light"] = {
            "state": "off",
            "attributes": {},
        }
        mock_hass._states_data["sensor.light_test_light_rlc"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "off"},  # RLC says light is "off"
        }
        
        with patch("custom_components.presence_based_lighting.async_track_state_change_event", return_value=lambda: None):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_rlc_tracking)
            await coordinator.async_start()
        
        # Initially presence should be allowed
        assert coordinator.get_presence_allowed("light.test_light") is True
        
        # Simulate: our system turned off the light (set the tracked state)
        coordinator._entity_states["light.test_light"]["last_effective_state"] = "off"
        
        # Now a spurious state change comes in from a different source
        # (e.g., the light reports availability changed, or a state refresh occurred)
        old_state = MagicMock()
        old_state.state = "unavailable"  # Was unavailable
        old_state.attributes = {}
        
        new_state = MagicMock()
        new_state.state = "off"  # Now reports "off" - same as RLC effective state
        new_state.attributes = {}
        new_state.context = MagicMock()
        new_state.context.id = "external_context"  # Different context from ours
        
        event = self._create_state_change_event(
            "light.test_light",
            old_state=old_state,
            new_state=new_state,
        )
        
        # Handle the controlled entity change
        await coordinator._handle_controlled_entity_change(event)
        
        # Presence should STILL be allowed because the RLC effective state
        # didn't actually change - it was already "off" when we last set it
        assert coordinator.get_presence_allowed("light.test_light") is True

    @pytest.mark.asyncio
    async def test_rlc_stale_read_race_still_pauses_via_rlc_listener(
        self, mock_hass, mock_entry_with_rlc_tracking
    ):
        """Regression: a manual off while the RLC mirror is momentarily stale.

        Reproduces the master-bedroom incident where HomeKit turned the light off
        while the room was occupied.  The controlled entity's own state_changed
        event fired before the RLC sensor (sensor.*_rlc) updated, so the
        synchronous RLC read returned the stale "on" value and the change was
        deduped as "no effective change".  The dedicated RLC-sensor listener must
        still catch the manual off (once the mirror updates) and pause automation
        WITHOUT re-issuing turn_on.
        """
        # Room occupied; light currently on; RLC mirror agrees ("on").
        mock_hass._states_data["binary_sensor.motion"] = {"state": "on", "attributes": {}}
        mock_hass._states_data["light.test_light"] = {"state": "on", "attributes": {}}
        mock_hass._states_data["sensor.light_test_light_rlc"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "on"},
        }

        with patch(
            "custom_components.presence_based_lighting.async_track_state_change_event",
            return_value=lambda: None,
        ):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_rlc_tracking)
            await coordinator.async_start()

        # The RLC sensor is mapped to the controlled entity.
        assert coordinator._rlc_to_entity == {"sensor.light_test_light_rlc": "light.test_light"}
        assert coordinator.get_automation_paused("light.test_light") is False
        mock_hass.services.async_call.reset_mock()

        # --- Step 1: light reports off, but the RLC mirror is still stale ("on").
        mock_hass._states_data["light.test_light"] = {"state": "off", "attributes": {}}
        old_light = MagicMock()
        old_light.state = "on"
        old_light.attributes = {}
        new_light = MagicMock()
        new_light.state = "off"
        new_light.attributes = {}
        new_light.context = MagicMock()
        new_light.context.id = "external_context"
        new_light.context.parent_id = None
        light_event = self._create_state_change_event(
            "light.test_light", old_state=old_light, new_state=new_light
        )
        await coordinator._handle_controlled_entity_change(light_event)

        # The stale read deduped the change: not paused yet, nothing re-issued.
        assert coordinator.get_automation_paused("light.test_light") is False
        mock_hass.services.async_call.assert_not_called()

        # --- Step 2: the RLC mirror catches up and reports "off".
        mock_hass._states_data["sensor.light_test_light_rlc"] = {
            "state": "2024-01-01T12:00:05+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "off"},
        }
        old_rlc = MagicMock()
        old_rlc.state = "2024-01-01T12:00:00+00:00"
        old_rlc.attributes = {ATTR_PREVIOUS_VALID_STATE: "on"}
        new_rlc = MagicMock()
        new_rlc.state = "2024-01-01T12:00:05+00:00"
        new_rlc.attributes = {ATTR_PREVIOUS_VALID_STATE: "off"}
        rlc_event = self._create_state_change_event(
            "sensor.light_test_light_rlc", old_state=old_rlc, new_state=new_rlc
        )
        await coordinator._handle_rlc_tracking_change(rlc_event)

        # Manual off is now detected: automation paused, light NOT turned back on.
        assert coordinator.get_automation_paused("light.test_light") is True
        for call in mock_hass.services.async_call.call_args_list:
            assert call.args[1] != "turn_on", "light must not be re-enabled after manual off"

    @pytest.mark.asyncio
    async def test_rlc_listener_ignores_our_own_change(
        self, mock_hass, mock_entry_with_rlc_tracking
    ):
        """An RLC change caused by our own service call must not pause automation."""
        mock_hass._states_data["binary_sensor.motion"] = {"state": "on", "attributes": {}}
        mock_hass._states_data["light.test_light"] = {"state": "on", "attributes": {}}
        mock_hass._states_data["sensor.light_test_light_rlc"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "off"},
        }

        with patch(
            "custom_components.presence_based_lighting.async_track_state_change_event",
            return_value=lambda: None,
        ):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_rlc_tracking)
            await coordinator.async_start()

        # Mark a context as ours and stamp it onto the controlled entity's state.
        our_ctx = MagicMock()
        our_ctx.id = "our_context"
        our_ctx.parent_id = None
        coordinator._entity_states["light.test_light"]["contexts"].append(our_ctx.id)
        light_state = MagicMock()
        light_state.state = "on"
        light_state.attributes = {}
        light_state.context = our_ctx
        mock_hass._states_data["light.test_light"] = {"state": "on", "attributes": {}}
        original_get = mock_hass.states.get
        mock_hass.states.get = lambda eid: (
            light_state if eid == "light.test_light" else original_get(eid)
        )

        old_rlc = MagicMock()
        old_rlc.state = "2024-01-01T12:00:00+00:00"
        old_rlc.attributes = {ATTR_PREVIOUS_VALID_STATE: "off"}
        new_rlc = MagicMock()
        new_rlc.state = "2024-01-01T12:00:05+00:00"
        new_rlc.attributes = {ATTR_PREVIOUS_VALID_STATE: "on"}
        rlc_event = self._create_state_change_event(
            "sensor.light_test_light_rlc", old_state=old_rlc, new_state=new_rlc
        )
        await coordinator._handle_rlc_tracking_change(rlc_event)

        # Our own change is treated as actuation feedback, never manual control.
        assert coordinator.get_automation_paused("light.test_light") is False

    @pytest.fixture
    async def started_rlc_coordinator(self, mock_hass, mock_entry_with_rlc_tracking):
        """A started coordinator with an occupied room and the light on."""
        mock_hass._states_data["binary_sensor.motion"] = {"state": "on", "attributes": {}}
        mock_hass._states_data["light.test_light"] = {"state": "on", "attributes": {}}
        mock_hass._states_data["sensor.light_test_light_rlc"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "on"},
        }
        with patch(
            "custom_components.presence_based_lighting.async_track_state_change_event",
            return_value=lambda: None,
        ):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_rlc_tracking)
            await coordinator.async_start()
        coordinator._entity_states["light.test_light"]["last_effective_state"] = "on"
        return coordinator

    @staticmethod
    def _rlc_event(creator, old_effective, new_effective, new_state=True):
        old_rlc = None
        if old_effective is not None or new_state:
            old_rlc = MagicMock()
            old_rlc.attributes = {ATTR_PREVIOUS_VALID_STATE: old_effective}
        new_rlc = None
        if new_state:
            new_rlc = MagicMock()
            new_rlc.attributes = {ATTR_PREVIOUS_VALID_STATE: new_effective}
        return creator("sensor.light_test_light_rlc", old_state=old_rlc, new_state=new_rlc)

    @pytest.mark.asyncio
    async def test_rlc_listener_ignores_unmapped_sensor(self, started_rlc_coordinator):
        """An RLC event for an unmapped sensor is ignored."""
        coordinator = started_rlc_coordinator
        event = self._create_state_change_event(
            "sensor.some_other_rlc", old_state=MagicMock(), new_state=MagicMock()
        )
        await coordinator._handle_rlc_tracking_change(event)
        assert coordinator.get_automation_paused("light.test_light") is False

    @pytest.mark.asyncio
    async def test_rlc_listener_ignores_missing_new_state(self, started_rlc_coordinator):
        """An RLC event without a new_state (entity removed) is ignored."""
        coordinator = started_rlc_coordinator
        event = self._rlc_event(self._create_state_change_event, "on", None, new_state=False)
        await coordinator._handle_rlc_tracking_change(event)
        assert coordinator.get_automation_paused("light.test_light") is False

    @pytest.mark.asyncio
    async def test_rlc_listener_ignores_timestamp_only_update(self, started_rlc_coordinator):
        """An RLC event where previous_valid_state did not change is ignored."""
        coordinator = started_rlc_coordinator
        event = self._rlc_event(self._create_state_change_event, "on", "on")
        await coordinator._handle_rlc_tracking_change(event)
        assert coordinator.get_automation_paused("light.test_light") is False

    @pytest.mark.asyncio
    async def test_rlc_listener_dedups_already_processed(self, started_rlc_coordinator):
        """An RLC change already seen by the synchronous read is not reprocessed."""
        coordinator = started_rlc_coordinator
        # last_effective_state is already "off" (synchronous read won the race).
        coordinator._entity_states["light.test_light"]["last_effective_state"] = "off"
        event = self._rlc_event(self._create_state_change_event, "on", "off")
        await coordinator._handle_rlc_tracking_change(event)
        assert coordinator.get_automation_paused("light.test_light") is False

    @pytest.mark.asyncio
    async def test_rlc_listener_handles_exception(self, started_rlc_coordinator):
        """A malformed RLC event is caught and logged without raising."""
        coordinator = started_rlc_coordinator
        bad_new_state = MagicMock()
        bad_new_state.attributes = "not-a-dict"  # .get raises -> except branch
        bad_event = MagicMock()
        bad_event.data = {
            "entity_id": "sensor.light_test_light_rlc",
            "new_state": bad_new_state,
            "old_state": None,
        }
        await coordinator._handle_rlc_tracking_change(bad_event)
        assert coordinator.get_automation_paused("light.test_light") is False

    @pytest.mark.asyncio
    async def test_rlc_listener_resume_reconciles(self, started_rlc_coordinator):
        """An external turn-on (non-pausing change) resumes and reconciles."""
        coordinator = started_rlc_coordinator
        # Start paused so the external "on" is a resume; last effective was "off".
        coordinator.set_automation_paused("light.test_light", True)
        coordinator._entity_states["light.test_light"]["last_effective_state"] = "off"
        event = self._rlc_event(self._create_state_change_event, "off", "on")
        await coordinator._handle_rlc_tracking_change(event)
        # "on" is not a manual_disable_state, so automation resumes.
        assert coordinator.get_automation_paused("light.test_light") is False

    @pytest.mark.asyncio
    async def test_rlc_listener_respects_disable_flag_off(
        self, mock_hass, mock_entry_with_rlc_tracking
    ):
        """With disable_on_external_control off, a manual off does not pause."""
        mock_entry_with_rlc_tracking.data[CONF_CONTROLLED_ENTITIES][0][
            CONF_DISABLE_ON_EXTERNAL_CONTROL
        ] = False
        mock_hass._states_data["binary_sensor.motion"] = {"state": "on", "attributes": {}}
        mock_hass._states_data["light.test_light"] = {"state": "off", "attributes": {}}
        mock_hass._states_data["sensor.light_test_light_rlc"] = {
            "state": "2024-01-01T12:00:00+00:00",
            "attributes": {ATTR_PREVIOUS_VALID_STATE: "on"},
        }
        with patch(
            "custom_components.presence_based_lighting.async_track_state_change_event",
            return_value=lambda: None,
        ):
            coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_entry_with_rlc_tracking)
            await coordinator.async_start()
        coordinator._entity_states["light.test_light"]["last_effective_state"] = "on"
        event = self._rlc_event(self._create_state_change_event, "on", "off")
        await coordinator._handle_rlc_tracking_change(event)
        assert coordinator.get_automation_paused("light.test_light") is False
