"""Helper functions for working with real_last_changed entities.

Real Last Changed sensors track when another entity last changed state,
persisting through HA restarts. The key attribute is `previous_valid_state`
which contains the actual state value ("on", "off", etc.) of the source entity.

We can use RLC sensors directly without mapping to source entities by reading
the `previous_valid_state` attribute.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, State

_LOGGER = logging.getLogger(__name__)

# Constants for real_last_changed integration
REAL_LAST_CHANGED_SUFFIX = "_real_last_changed"
ATTR_PREVIOUS_VALID_STATE = "previous_valid_state"


def is_real_last_changed_entity(entity_id: str) -> bool:
    """Check if an entity is a real_last_changed sensor.
    
    Real last changed entities are sensors that track when another entity
    last changed state, persisting through HA restarts.
    
    Pattern: sensor.{name}_real_last_changed
    
    Args:
        entity_id: The entity ID to check
        
    Returns:
        True if this is a real_last_changed sensor, False otherwise
    """
    if not entity_id:
        return False
    
    # Real last changed entities are always sensors with _real_last_changed suffix
    return (
        entity_id.startswith("sensor.") and 
        entity_id.endswith(REAL_LAST_CHANGED_SUFFIX)
    )


def get_effective_state(hass: "HomeAssistant", entity_id: str) -> str | None:
    """Get the effective state value for any sensor, handling RLC sensors specially.
    
    For regular sensors/binary_sensors: returns the entity's state directly
    For RLC sensors: returns the `previous_valid_state` attribute
    
    Args:
        hass: Home Assistant instance
        entity_id: The entity ID to get state for
        
    Returns:
        The effective state value ("on", "off", etc.) or None if unavailable
    """
    state = hass.states.get(entity_id)
    if state is None:
        return None
    
    if is_real_last_changed_entity(entity_id):
        # For RLC sensors, use the previous_valid_state attribute
        return state.attributes.get(ATTR_PREVIOUS_VALID_STATE)
    else:
        # For regular sensors, use the state directly
        return state.state


def is_entity_on(hass: "HomeAssistant", entity_id: str) -> bool:
    """Check if an entity is effectively "on", handling RLC sensors.
    
    For regular sensors: checks if state == "on"
    For RLC sensors: checks if previous_valid_state == "on"
    
    Args:
        hass: Home Assistant instance
        entity_id: The entity ID to check
        
    Returns:
        True if the entity is effectively "on", False otherwise
    """
    effective_state = get_effective_state(hass, entity_id)
    return effective_state == "on"


def is_entity_off(hass: "HomeAssistant", entity_id: str) -> bool:
    """Check if an entity is effectively "off", handling RLC sensors.
    
    For regular sensors: checks if state == "off"
    For RLC sensors: checks if previous_valid_state == "off"
    
    Args:
        hass: Home Assistant instance
        entity_id: The entity ID to check
        
    Returns:
        True if the entity is effectively "off", False otherwise
    """
    effective_state = get_effective_state(hass, entity_id)
    return effective_state == "off"
