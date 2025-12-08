"""Tests for sensor mappings step behavior in config flow."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from homeassistant import config_entries
from homeassistant.const import STATE_ON, STATE_OFF

from custom_components.presence_based_lighting.config_flow import (
    PresenceBasedLightingFlowHandler,
    PresenceBasedLightingOptionsFlowHandler,
    STEP_USER,
    STEP_SENSOR_MAPPINGS,
    STEP_SELECT_ENTITY,
    STEP_MANAGE_ENTITIES,
)
from custom_components.presence_based_lighting.const import (
    CONF_PRESENCE_SENSORS,
    CONF_CLEARING_SENSORS,
    CONF_PRESENCE_SENSOR_MAPPINGS,
    CONF_CLEARING_SENSOR_MAPPINGS,
    CONF_ROOM_NAME,
    CONF_OFF_DELAY,
    CONF_CONTROLLED_ENTITIES,
    DOMAIN,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    
    # Setup state registry
    states = {}
    
    def get_state(entity_id):
        state_value = states.get(entity_id, STATE_OFF)
        state_obj = MagicMock()
        state_obj.state = state_value
        state_obj.name = entity_id.split(".")[-1].replace("_", " ").title()
        return state_obj
    
    hass.states.get = get_state
    hass._states = states
    
    # Mock entity registry
    entity_registry = MagicMock()
    entity_registry.async_get = MagicMock(return_value=None)
    hass.helpers = MagicMock()
    
    return hass


def create_mock_async_show_form():
    """Create a mock async_show_form that captures and returns form data."""
    captured = {}
    def mock_show_form(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return {"type": "form", **kwargs}
    return mock_show_form, captured


class TestConfigFlowSkipsSensorMappingsForRegularSensors:
    """Test that sensor mappings step is skipped when no Real Last Changed sensors."""

    @pytest.mark.asyncio
    async def test_skips_sensor_mappings_with_only_regular_binary_sensors(self, mock_hass):
        """When only regular binary sensors are selected, skip sensor mappings step."""
        flow = PresenceBasedLightingFlowHandler()
        flow.hass = mock_hass
        flow._base_data = {
            CONF_ROOM_NAME: "Test Room",
            CONF_OFF_DELAY: 60,
            CONF_PRESENCE_SENSORS: ["binary_sensor.motion"],
            CONF_CLEARING_SENSORS: ["binary_sensor.occupancy"],
            CONF_PRESENCE_SENSOR_MAPPINGS: {},
            CONF_CLEARING_SENSOR_MAPPINGS: {},
        }
        flow._controlled_entities = []
        flow._errors = {}
        
        # Call sensor_mappings step
        with patch.object(flow, "async_step_select_entity", new_callable=AsyncMock) as mock_select:
            mock_select.return_value = {"type": "form", "step_id": STEP_SELECT_ENTITY}
            result = await flow.async_step_sensor_mappings()
        
        # Should have skipped to select_entity
        mock_select.assert_called_once()
        
        # Mappings should be empty
        assert flow._base_data[CONF_PRESENCE_SENSOR_MAPPINGS] == {}
        assert flow._base_data[CONF_CLEARING_SENSOR_MAPPINGS] == {}

    @pytest.mark.asyncio
    async def test_skips_sensor_mappings_with_no_sensors(self, mock_hass):
        """When no sensors are selected, skip sensor mappings step."""
        flow = PresenceBasedLightingFlowHandler()
        flow.hass = mock_hass
        flow._base_data = {
            CONF_ROOM_NAME: "Test Room",
            CONF_OFF_DELAY: 60,
            CONF_PRESENCE_SENSORS: [],
            CONF_CLEARING_SENSORS: [],
            CONF_PRESENCE_SENSOR_MAPPINGS: {},
            CONF_CLEARING_SENSOR_MAPPINGS: {},
        }
        flow._controlled_entities = []
        flow._errors = {}
        
        with patch.object(flow, "async_step_select_entity", new_callable=AsyncMock) as mock_select:
            mock_select.return_value = {"type": "form", "step_id": STEP_SELECT_ENTITY}
            result = await flow.async_step_sensor_mappings()
        
        mock_select.assert_called_once()


class TestConfigFlowShowsSensorMappingsForRealLastChanged:
    """Test that sensor mappings step appears when Real Last Changed sensors exist."""

    @pytest.mark.asyncio
    async def test_shows_form_with_only_real_last_changed_sensor(self, mock_hass):
        """When a real_last_changed sensor is selected, show the mappings form."""
        flow = PresenceBasedLightingFlowHandler()
        flow.hass = mock_hass
        flow._base_data = {
            CONF_ROOM_NAME: "Test Room",
            CONF_OFF_DELAY: 60,
            CONF_PRESENCE_SENSORS: ["sensor.motion_real_last_changed"],
            CONF_CLEARING_SENSORS: [],
        }
        flow._errors = {}
        
        # Mock async_show_form to capture what's passed to it
        mock_show_form, captured = create_mock_async_show_form()
        flow.async_show_form = mock_show_form
        
        with patch("custom_components.presence_based_lighting.config_flow.get_source_entity", return_value=None):
            result = await flow.async_step_sensor_mappings()
        
        # Should show the form
        assert result["type"] == "form"
        assert captured["step_id"] == STEP_SENSOR_MAPPINGS
        
        # Should have a field for the real_last_changed sensor
        schema = captured["data_schema"].schema
        field_keys = [str(k) for k in schema.keys()]
        assert any("motion_real_last_changed" in k for k in field_keys)

    @pytest.mark.asyncio
    async def test_only_shows_real_last_changed_sensors_in_form(self, mock_hass):
        """Form should only include Real Last Changed sensors, not regular ones."""
        flow = PresenceBasedLightingFlowHandler()
        flow.hass = mock_hass
        flow._base_data = {
            CONF_ROOM_NAME: "Test Room",
            CONF_OFF_DELAY: 60,
            CONF_PRESENCE_SENSORS: [
                "binary_sensor.regular_motion",
                "sensor.motion_real_last_changed",
            ],
            CONF_CLEARING_SENSORS: [
                "binary_sensor.regular_occupancy",
            ],
        }
        flow._errors = {}
        
        # Mock async_show_form
        mock_show_form, captured = create_mock_async_show_form()
        flow.async_show_form = mock_show_form
        
        with patch("custom_components.presence_based_lighting.config_flow.get_source_entity", return_value=None):
            result = await flow.async_step_sensor_mappings()
        
        # Should show the form
        assert result["type"] == "form"
        assert captured["step_id"] == STEP_SENSOR_MAPPINGS
        
        # Check schema fields
        schema = captured["data_schema"].schema
        field_keys = [str(k) for k in schema.keys()]
        
        # Should have field for real_last_changed sensor
        assert any("motion_real_last_changed" in k for k in field_keys)
        
        # Should NOT have fields for regular binary sensors
        assert not any("regular_motion" in k for k in field_keys)
        assert not any("regular_occupancy" in k for k in field_keys)
        
        # Should only have one field
        assert len(schema) == 1

    @pytest.mark.asyncio
    async def test_shows_both_presence_and_clearing_real_last_changed(self, mock_hass):
        """Form should show RLC sensors from both presence and clearing lists."""
        flow = PresenceBasedLightingFlowHandler()
        flow.hass = mock_hass
        flow._base_data = {
            CONF_ROOM_NAME: "Test Room",
            CONF_OFF_DELAY: 60,
            CONF_PRESENCE_SENSORS: ["sensor.motion_real_last_changed"],
            CONF_CLEARING_SENSORS: ["sensor.occupancy_real_last_changed"],
        }
        flow._errors = {}
        
        # Mock async_show_form
        mock_show_form, captured = create_mock_async_show_form()
        flow.async_show_form = mock_show_form
        
        with patch("custom_components.presence_based_lighting.config_flow.get_source_entity", return_value=None):
            result = await flow.async_step_sensor_mappings()
        
        # Should show the form with both sensors
        assert result["type"] == "form"
        schema = captured["data_schema"].schema
        field_keys = [str(k) for k in schema.keys()]
        
        assert any("motion_real_last_changed" in k for k in field_keys)
        assert any("occupancy_real_last_changed" in k for k in field_keys)
        assert len(schema) == 2


class TestConfigFlowSensorMappingsSubmission:
    """Test that submitting sensor mappings works correctly."""

    @pytest.mark.asyncio
    async def test_submission_creates_mappings_for_rlc_sensors(self, mock_hass):
        """Submitting mappings should create correct presence/clearing mapping dicts."""
        flow = PresenceBasedLightingFlowHandler()
        flow.hass = mock_hass
        flow._base_data = {
            CONF_ROOM_NAME: "Test Room",
            CONF_OFF_DELAY: 60,
            CONF_PRESENCE_SENSORS: ["sensor.motion_real_last_changed"],
            CONF_CLEARING_SENSORS: ["sensor.motion_real_last_changed"],  # Same sensor in both
        }
        flow._controlled_entities = []
        flow._errors = {}
        
        user_input = {
            "source_sensor_motion_real_last_changed": "binary_sensor.motion",
        }
        
        with patch.object(flow, "async_step_select_entity", new_callable=AsyncMock) as mock_select:
            mock_select.return_value = {"type": "form", "step_id": STEP_SELECT_ENTITY}
            result = await flow.async_step_sensor_mappings(user_input)
        
        # Should have mappings in both dicts
        assert flow._base_data[CONF_PRESENCE_SENSOR_MAPPINGS] == {
            "sensor.motion_real_last_changed": "binary_sensor.motion",
        }
        assert flow._base_data[CONF_CLEARING_SENSOR_MAPPINGS] == {
            "sensor.motion_real_last_changed": "binary_sensor.motion",
        }


class TestOptionsFlowSkipsSensorMappingsForRegularSensors:
    """Test that options flow sensor mappings step is skipped for regular sensors."""

    @pytest.fixture
    def mock_config_entry(self):
        """Create a mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.data = {
            CONF_ROOM_NAME: "Test Room",
            CONF_OFF_DELAY: 60,
            CONF_PRESENCE_SENSORS: ["binary_sensor.motion"],
            CONF_CLEARING_SENSORS: [],
            CONF_PRESENCE_SENSOR_MAPPINGS: {},
            CONF_CLEARING_SENSOR_MAPPINGS: {},
            CONF_CONTROLLED_ENTITIES: [],
        }
        return entry

    @pytest.mark.asyncio
    async def test_options_skips_sensor_mappings_with_regular_sensors(self, mock_hass, mock_config_entry):
        """Options flow should skip sensor mappings when only regular sensors."""
        flow = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
        flow.hass = mock_hass
        flow._base_data = dict(mock_config_entry.data)
        flow._controlled_entities = []
        flow._errors = {}
        
        with patch.object(flow, "async_step_manage_entities", new_callable=AsyncMock) as mock_manage:
            mock_manage.return_value = {"type": "form", "step_id": STEP_MANAGE_ENTITIES}
            result = await flow.async_step_sensor_mappings()
        
        mock_manage.assert_called_once()
        assert flow._base_data[CONF_PRESENCE_SENSOR_MAPPINGS] == {}
        assert flow._base_data[CONF_CLEARING_SENSOR_MAPPINGS] == {}

    @pytest.mark.asyncio
    async def test_options_shows_form_with_real_last_changed(self, mock_hass, mock_config_entry):
        """Options flow should show form when RLC sensors are selected."""
        flow = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
        flow.hass = mock_hass
        flow._base_data = {
            CONF_ROOM_NAME: "Test Room",
            CONF_OFF_DELAY: 60,
            CONF_PRESENCE_SENSORS: ["sensor.motion_real_last_changed"],
            CONF_CLEARING_SENSORS: [],
            CONF_PRESENCE_SENSOR_MAPPINGS: {},
            CONF_CLEARING_SENSOR_MAPPINGS: {},
        }
        flow._controlled_entities = []
        flow._errors = {}
        
        # Mock async_show_form
        mock_show_form, captured = create_mock_async_show_form()
        flow.async_show_form = mock_show_form
        
        with patch("custom_components.presence_based_lighting.config_flow.get_source_entity", return_value=None):
            result = await flow.async_step_sensor_mappings()
        
        assert result["type"] == "form"
        assert captured["step_id"] == STEP_SENSOR_MAPPINGS


class TestMixedSensorFiltering:
    """Test filtering behavior with mixed sensor types."""

    @pytest.mark.asyncio
    async def test_filters_out_regular_sensors_from_description(self, mock_hass):
        """Sensor descriptions should only list RLC sensors, not regular ones."""
        flow = PresenceBasedLightingFlowHandler()
        flow.hass = mock_hass
        flow._base_data = {
            CONF_ROOM_NAME: "Test Room",
            CONF_OFF_DELAY: 60,
            CONF_PRESENCE_SENSORS: [
                "binary_sensor.pir",
                "sensor.pir_real_last_changed",
            ],
            CONF_CLEARING_SENSORS: [
                "binary_sensor.occupancy",
            ],
        }
        flow._errors = {}
        
        # Mock async_show_form
        mock_show_form, captured = create_mock_async_show_form()
        flow.async_show_form = mock_show_form
        
        with patch("custom_components.presence_based_lighting.config_flow.get_source_entity", return_value=None):
            result = await flow.async_step_sensor_mappings()
        
        # The form should only have one field - for the RLC sensor
        # Regular sensors (binary_sensor.pir, binary_sensor.occupancy) should be auto-resolved
        assert len(captured["data_schema"].schema) == 1
        
        # Verify the field is for the RLC sensor
        schema = captured["data_schema"].schema
        field_keys = [str(k) for k in schema.keys()]
        assert any("pir_real_last_changed" in k for k in field_keys)
        assert not any("binary_sensor" in k for k in field_keys)

    @pytest.mark.asyncio
    async def test_deduplicates_sensors_across_presence_and_clearing(self, mock_hass):
        """Same sensor in both presence and clearing should only appear once in form."""
        flow = PresenceBasedLightingFlowHandler()
        flow.hass = mock_hass
        flow._base_data = {
            CONF_ROOM_NAME: "Test Room",
            CONF_OFF_DELAY: 60,
            CONF_PRESENCE_SENSORS: ["sensor.motion_real_last_changed"],
            CONF_CLEARING_SENSORS: ["sensor.motion_real_last_changed"],  # Same sensor
        }
        flow._errors = {}
        
        # Mock async_show_form
        mock_show_form, captured = create_mock_async_show_form()
        flow.async_show_form = mock_show_form
        
        with patch("custom_components.presence_based_lighting.config_flow.get_source_entity", return_value=None):
            result = await flow.async_step_sensor_mappings()
        
        # Should only have one field (deduplicated)
        schema = captured["data_schema"].schema
        assert len(schema) == 1
