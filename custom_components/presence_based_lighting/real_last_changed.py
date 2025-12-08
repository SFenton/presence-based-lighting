"""Helper functions for detecting and mapping real_last_changed entities."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Constants for real_last_changed integration
REAL_LAST_CHANGED_DOMAIN = "real_last_changed"
REAL_LAST_CHANGED_SUFFIX = "_real_last_changed"
CONF_SOURCE_ENTITY = "source_entity"


def is_real_last_changed_entity(entity_id: str) -> bool:
    """Check if an entity is a real_last_changed sensor.
    
    Real last changed entities are sensors that track when another entity
    last changed state, persisting through HA restarts.
    
    Pattern: sensor.{name}_real_last_changed
    """
    if not entity_id:
        return False
    
    # Real last changed entities are always sensors with _real_last_changed suffix
    return (
        entity_id.startswith("sensor.") and 
        entity_id.endswith(REAL_LAST_CHANGED_SUFFIX)
    )


def get_source_entity_from_config_entries(hass: "HomeAssistant", entity_id: str) -> str | None:
    """Look up the source entity from real_last_changed config entries.
    
    This is the most reliable method as it uses the actual stored configuration.
    
    Args:
        hass: Home Assistant instance
        entity_id: The real_last_changed sensor entity ID
        
    Returns:
        The source entity ID if found, None otherwise
    """
    try:
        # Get all real_last_changed config entries
        entries = hass.config_entries.async_entries(REAL_LAST_CHANGED_DOMAIN)
        
        for entry in entries:
            source = entry.data.get(CONF_SOURCE_ENTITY)
            if not source:
                continue
            
            # Check if this entry's sensor matches our entity_id
            # The entity_id is derived from the source entity or custom name
            custom_name = entry.data.get("name")
            
            if custom_name:
                # Custom name: sensor.{slugify(custom_name)}
                from homeassistant.util import slugify
                expected_entity_id = f"sensor.{slugify(custom_name)}"
            else:
                # Default: sensor.{source_entity_name}_real_last_changed
                # e.g., binary_sensor.living_room_motion → sensor.living_room_motion_real_last_changed
                source_name = source.split(".")[-1]
                expected_entity_id = f"sensor.{source_name}{REAL_LAST_CHANGED_SUFFIX}"
            
            if entity_id == expected_entity_id:
                _LOGGER.debug(
                    "Found source entity %s for real_last_changed entity %s via config entry",
                    source, entity_id
                )
                return source
        
        _LOGGER.debug(
            "No config entry found for real_last_changed entity %s",
            entity_id
        )
        return None
        
    except Exception as err:
        _LOGGER.warning(
            "Error looking up source entity for %s: %s",
            entity_id, err
        )
        return None


def get_source_entity_from_pattern(hass: "HomeAssistant", entity_id: str) -> str | None:
    """Attempt to find the source entity by pattern matching.
    
    This is a fallback method when config entry lookup fails.
    It tries to find entities that match the base name.
    
    Args:
        hass: Home Assistant instance
        entity_id: The real_last_changed sensor entity ID
        
    Returns:
        The source entity ID if found, None otherwise
    """
    if not is_real_last_changed_entity(entity_id):
        return None
    
    # Extract base name: sensor.living_room_motion_real_last_changed → living_room_motion
    base_name = entity_id.replace("sensor.", "").replace(REAL_LAST_CHANGED_SUFFIX, "")
    
    # Common domains to check, in order of likelihood for presence sensors
    domains_to_check = [
        "binary_sensor",  # Most common for motion/occupancy sensors
        "sensor",
        "switch",
        "light",
        "input_boolean",
    ]
    
    # Check each domain for a matching entity
    for domain in domains_to_check:
        candidate = f"{domain}.{base_name}"
        state = hass.states.get(candidate)
        if state is not None:
            _LOGGER.debug(
                "Found source entity %s for real_last_changed entity %s via pattern matching",
                candidate, entity_id
            )
            return candidate
    
    _LOGGER.debug(
        "No source entity found for real_last_changed entity %s via pattern matching",
        entity_id
    )
    return None


def get_source_entity(hass: "HomeAssistant", entity_id: str) -> str | None:
    """Get the source entity for a real_last_changed sensor.
    
    Uses multiple strategies:
    1. Config entry lookup (most reliable)
    2. Pattern matching fallback
    
    Args:
        hass: Home Assistant instance
        entity_id: The real_last_changed sensor entity ID
        
    Returns:
        The source entity ID if found, None otherwise
    """
    if not is_real_last_changed_entity(entity_id):
        return None
    
    # Try config entry lookup first
    source = get_source_entity_from_config_entries(hass, entity_id)
    if source:
        return source
    
    # Fall back to pattern matching
    return get_source_entity_from_pattern(hass, entity_id)


def get_all_real_last_changed_mappings(hass: "HomeAssistant") -> dict[str, str]:
    """Get all real_last_changed entity to source entity mappings.
    
    Returns:
        Dict mapping real_last_changed entity IDs to their source entity IDs
    """
    mappings = {}
    
    try:
        entries = hass.config_entries.async_entries(REAL_LAST_CHANGED_DOMAIN)
        
        for entry in entries:
            source = entry.data.get(CONF_SOURCE_ENTITY)
            if not source:
                continue
            
            custom_name = entry.data.get("name")
            
            if custom_name:
                from homeassistant.util import slugify
                rlc_entity_id = f"sensor.{slugify(custom_name)}"
            else:
                source_name = source.split(".")[-1]
                rlc_entity_id = f"sensor.{source_name}{REAL_LAST_CHANGED_SUFFIX}"
            
            mappings[rlc_entity_id] = source
        
    except Exception as err:
        _LOGGER.warning("Error getting real_last_changed mappings: %s", err)
    
    return mappings


def resolve_entity_for_state_tracking(hass: "HomeAssistant", entity_id: str) -> str:
    """Resolve an entity ID to the appropriate entity for state tracking.
    
    If the entity is a real_last_changed sensor, return its source entity.
    Otherwise, return the entity as-is.
    
    This allows users to select real_last_changed entities in the UI,
    but we use the source entity for actual state tracking.
    
    Args:
        hass: Home Assistant instance
        entity_id: The entity ID to resolve
        
    Returns:
        The entity ID to use for state tracking
    """
    if is_real_last_changed_entity(entity_id):
        source = get_source_entity(hass, entity_id)
        if source:
            _LOGGER.info(
                "Resolved real_last_changed entity %s to source entity %s",
                entity_id, source
            )
            return source
        else:
            _LOGGER.warning(
                "Could not resolve source entity for %s, using as-is",
                entity_id
            )
    
    return entity_id


def get_entity_with_real_last_changed_info(
    hass: "HomeAssistant", 
    entity_id: str
) -> dict:
    """Get entity information including real_last_changed detection.
    
    Returns:
        Dict with:
        - entity_id: The original entity ID
        - is_real_last_changed: Whether this is a real_last_changed sensor
        - source_entity: The source entity if applicable
        - resolved_entity: The entity to use for state tracking
    """
    is_rlc = is_real_last_changed_entity(entity_id)
    source = get_source_entity(hass, entity_id) if is_rlc else None
    
    return {
        "entity_id": entity_id,
        "is_real_last_changed": is_rlc,
        "source_entity": source,
        "resolved_entity": source if source else entity_id,
    }
