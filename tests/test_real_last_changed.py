"""Tests for real_last_changed helper functions."""

import pytest
from unittest.mock import MagicMock, patch

from custom_components.presence_based_lighting.real_last_changed import (
    is_real_last_changed_entity,
    get_source_entity_from_config_entries,
    get_source_entity_from_pattern,
    get_source_entity,
    get_all_real_last_changed_mappings,
    resolve_entity_for_state_tracking,
    get_entity_with_real_last_changed_info,
    REAL_LAST_CHANGED_DOMAIN,
    REAL_LAST_CHANGED_SUFFIX,
)


class TestIsRealLastChangedEntity:
    """Tests for is_real_last_changed_entity function."""

    def test_valid_real_last_changed_entity(self):
        """Test detection of valid real_last_changed entities."""
        assert is_real_last_changed_entity("sensor.living_room_motion_real_last_changed") is True
        assert is_real_last_changed_entity("sensor.bedroom_pir_real_last_changed") is True
        assert is_real_last_changed_entity("sensor.front_door_real_last_changed") is True

    def test_invalid_entities_wrong_domain(self):
        """Test rejection of non-sensor entities."""
        assert is_real_last_changed_entity("binary_sensor.motion_real_last_changed") is False
        assert is_real_last_changed_entity("light.living_room_real_last_changed") is False
        assert is_real_last_changed_entity("switch.motion_real_last_changed") is False

    def test_invalid_entities_wrong_suffix(self):
        """Test rejection of entities without correct suffix."""
        assert is_real_last_changed_entity("sensor.living_room_motion") is False
        assert is_real_last_changed_entity("sensor.temperature") is False
        assert is_real_last_changed_entity("sensor.motion_last_changed") is False  # Close but not exact

    def test_empty_or_none_entity(self):
        """Test handling of empty or None entity IDs."""
        assert is_real_last_changed_entity("") is False
        assert is_real_last_changed_entity(None) is False


class TestGetSourceEntityFromConfigEntries:
    """Tests for config entry based source entity lookup."""

    def test_finds_source_from_default_naming(self):
        """Test finding source entity when using default naming convention."""
        mock_hass = MagicMock()
        
        # Create mock config entry
        mock_entry = MagicMock()
        mock_entry.data = {
            "source_entity": "binary_sensor.living_room_motion",
            "name": None,
        }
        mock_hass.config_entries.async_entries.return_value = [mock_entry]
        
        result = get_source_entity_from_config_entries(
            mock_hass, 
            "sensor.living_room_motion_real_last_changed"
        )
        
        assert result == "binary_sensor.living_room_motion"
        mock_hass.config_entries.async_entries.assert_called_with(REAL_LAST_CHANGED_DOMAIN)

    def test_finds_source_from_custom_naming(self):
        """Test finding source entity when using custom name."""
        mock_hass = MagicMock()
        
        mock_entry = MagicMock()
        mock_entry.data = {
            "source_entity": "binary_sensor.pir_sensor_1",
            "name": "Living Room Motion Tracker",
        }
        mock_hass.config_entries.async_entries.return_value = [mock_entry]
        
        result = get_source_entity_from_config_entries(
            mock_hass, 
            "sensor.living_room_motion_tracker"
        )
        
        assert result == "binary_sensor.pir_sensor_1"

    def test_returns_none_when_no_matching_entry(self):
        """Test returning None when no config entry matches."""
        mock_hass = MagicMock()
        
        mock_entry = MagicMock()
        mock_entry.data = {
            "source_entity": "binary_sensor.bedroom_motion",
            "name": None,
        }
        mock_hass.config_entries.async_entries.return_value = [mock_entry]
        
        result = get_source_entity_from_config_entries(
            mock_hass, 
            "sensor.living_room_motion_real_last_changed"
        )
        
        assert result is None

    def test_handles_empty_config_entries(self):
        """Test handling when no config entries exist."""
        mock_hass = MagicMock()
        mock_hass.config_entries.async_entries.return_value = []
        
        result = get_source_entity_from_config_entries(
            mock_hass, 
            "sensor.living_room_motion_real_last_changed"
        )
        
        assert result is None

    def test_handles_exception_gracefully(self):
        """Test graceful handling of exceptions."""
        mock_hass = MagicMock()
        mock_hass.config_entries.async_entries.side_effect = Exception("Test error")
        
        result = get_source_entity_from_config_entries(
            mock_hass, 
            "sensor.living_room_motion_real_last_changed"
        )
        
        assert result is None


class TestGetSourceEntityFromPattern:
    """Tests for pattern-based source entity lookup."""

    def test_finds_binary_sensor_source(self):
        """Test finding a binary_sensor as source entity."""
        mock_hass = MagicMock()
        mock_hass.states.get.side_effect = lambda entity_id: (
            MagicMock() if entity_id == "binary_sensor.living_room_motion" else None
        )
        
        result = get_source_entity_from_pattern(
            mock_hass, 
            "sensor.living_room_motion_real_last_changed"
        )
        
        assert result == "binary_sensor.living_room_motion"

    def test_finds_switch_source(self):
        """Test finding a switch as source entity."""
        mock_hass = MagicMock()
        mock_hass.states.get.side_effect = lambda entity_id: (
            MagicMock() if entity_id == "switch.basement_fan" else None
        )
        
        result = get_source_entity_from_pattern(
            mock_hass, 
            "sensor.basement_fan_real_last_changed"
        )
        
        assert result == "switch.basement_fan"

    def test_returns_none_when_no_source_found(self):
        """Test returning None when no matching source exists."""
        mock_hass = MagicMock()
        mock_hass.states.get.return_value = None
        
        result = get_source_entity_from_pattern(
            mock_hass, 
            "sensor.unknown_entity_real_last_changed"
        )
        
        assert result is None

    def test_rejects_non_real_last_changed_entity(self):
        """Test rejection of non-real_last_changed entities."""
        mock_hass = MagicMock()
        
        result = get_source_entity_from_pattern(
            mock_hass, 
            "sensor.temperature"
        )
        
        assert result is None
        mock_hass.states.get.assert_not_called()


class TestGetSourceEntity:
    """Tests for the combined source entity lookup function."""

    def test_prefers_config_entry_over_pattern(self):
        """Test that config entry lookup is preferred."""
        mock_hass = MagicMock()
        
        # Config entry returns a source
        mock_entry = MagicMock()
        mock_entry.data = {
            "source_entity": "binary_sensor.motion_from_config",
            "name": None,
        }
        mock_hass.config_entries.async_entries.return_value = [mock_entry]
        
        # Pattern matching would also find something
        mock_hass.states.get.side_effect = lambda entity_id: (
            MagicMock() if entity_id == "binary_sensor.motion_from_config" else None
        )
        
        result = get_source_entity(
            mock_hass, 
            "sensor.motion_from_config_real_last_changed"
        )
        
        assert result == "binary_sensor.motion_from_config"

    def test_falls_back_to_pattern_when_config_fails(self):
        """Test fallback to pattern matching."""
        mock_hass = MagicMock()
        
        # No matching config entries
        mock_hass.config_entries.async_entries.return_value = []
        
        # Pattern matching finds source
        mock_hass.states.get.side_effect = lambda entity_id: (
            MagicMock() if entity_id == "binary_sensor.living_room_motion" else None
        )
        
        result = get_source_entity(
            mock_hass, 
            "sensor.living_room_motion_real_last_changed"
        )
        
        assert result == "binary_sensor.living_room_motion"

    def test_returns_none_for_non_real_last_changed(self):
        """Test returning None for regular entities."""
        mock_hass = MagicMock()
        
        result = get_source_entity(mock_hass, "binary_sensor.motion")
        
        assert result is None


class TestGetAllRealLastChangedMappings:
    """Tests for getting all real_last_changed mappings."""

    def test_returns_all_mappings(self):
        """Test getting all mappings from config entries."""
        mock_hass = MagicMock()
        
        entries = [
            MagicMock(data={"source_entity": "binary_sensor.motion1", "name": None}),
            MagicMock(data={"source_entity": "switch.fan", "name": "Fan Tracker"}),
        ]
        mock_hass.config_entries.async_entries.return_value = entries
        
        result = get_all_real_last_changed_mappings(mock_hass)
        
        assert "sensor.motion1_real_last_changed" in result
        assert result["sensor.motion1_real_last_changed"] == "binary_sensor.motion1"
        assert "sensor.fan_tracker" in result
        assert result["sensor.fan_tracker"] == "switch.fan"

    def test_handles_empty_entries(self):
        """Test handling when no entries exist."""
        mock_hass = MagicMock()
        mock_hass.config_entries.async_entries.return_value = []
        
        result = get_all_real_last_changed_mappings(mock_hass)
        
        assert result == {}


class TestResolveEntityForStateTracking:
    """Tests for the entity resolution function."""

    def test_resolves_real_last_changed_to_source(self):
        """Test resolving a real_last_changed entity to its source."""
        mock_hass = MagicMock()
        
        mock_entry = MagicMock()
        mock_entry.data = {
            "source_entity": "binary_sensor.motion",
            "name": None,
        }
        mock_hass.config_entries.async_entries.return_value = [mock_entry]
        
        result = resolve_entity_for_state_tracking(
            mock_hass, 
            "sensor.motion_real_last_changed"
        )
        
        assert result == "binary_sensor.motion"

    def test_returns_original_for_non_real_last_changed(self):
        """Test returning original entity for non-real_last_changed."""
        mock_hass = MagicMock()
        
        result = resolve_entity_for_state_tracking(
            mock_hass, 
            "binary_sensor.motion"
        )
        
        assert result == "binary_sensor.motion"
        mock_hass.config_entries.async_entries.assert_not_called()

    def test_returns_original_when_source_not_found(self):
        """Test returning original entity when source cannot be found."""
        mock_hass = MagicMock()
        mock_hass.config_entries.async_entries.return_value = []
        mock_hass.states.get.return_value = None
        
        result = resolve_entity_for_state_tracking(
            mock_hass, 
            "sensor.unknown_real_last_changed"
        )
        
        assert result == "sensor.unknown_real_last_changed"


class TestGetEntityWithRealLastChangedInfo:
    """Tests for getting entity info with real_last_changed detection."""

    def test_returns_info_for_real_last_changed_entity(self):
        """Test getting info for a real_last_changed entity."""
        mock_hass = MagicMock()
        
        mock_entry = MagicMock()
        mock_entry.data = {
            "source_entity": "binary_sensor.motion",
            "name": None,
        }
        mock_hass.config_entries.async_entries.return_value = [mock_entry]
        
        result = get_entity_with_real_last_changed_info(
            mock_hass, 
            "sensor.motion_real_last_changed"
        )
        
        assert result["entity_id"] == "sensor.motion_real_last_changed"
        assert result["is_real_last_changed"] is True
        assert result["source_entity"] == "binary_sensor.motion"
        assert result["resolved_entity"] == "binary_sensor.motion"

    def test_returns_info_for_regular_entity(self):
        """Test getting info for a regular entity."""
        mock_hass = MagicMock()
        
        result = get_entity_with_real_last_changed_info(
            mock_hass, 
            "binary_sensor.motion"
        )
        
        assert result["entity_id"] == "binary_sensor.motion"
        assert result["is_real_last_changed"] is False
        assert result["source_entity"] is None
        assert result["resolved_entity"] == "binary_sensor.motion"
