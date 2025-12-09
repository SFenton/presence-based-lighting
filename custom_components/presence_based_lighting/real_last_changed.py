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


def is_real_last_changed_entity(entity_id: str | None, state: "State | None" = None) -> bool:
    """Check if an entity is a real_last_changed sensor.
    
    Real last changed entities are sensors that track when another entity
    last changed state, persisting through HA restarts.
    
    Detection methods (in order):
    1. If state is provided, check for previous_valid_state attribute
    2. Check if entity_id is a sensor (not binary_sensor)
    
    Args:
        entity_id: The entity ID to check
        state: Optional state object to check for attributes
        
    Returns:
        True if this is a real_last_changed sensor, False otherwise
    """
    if not entity_id:
        return False
    
    # If we have the state object, check for the previous_valid_state attribute
    if state is not None:
        return ATTR_PREVIOUS_VALID_STATE in state.attributes
    
    # Fallback: Real last changed entities are sensors (not binary_sensors)
    # This is a heuristic - sensor.* entities that report timestamps are likely RLC
    return entity_id.startswith("sensor.") and not entity_id.startswith("sensor.binary_")


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
    
    if is_real_last_changed_entity(entity_id, state):
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


def is_rlc_integration_available(hass: "HomeAssistant") -> bool:
    """Check if the real_last_changed integration is available.
    
    Checks if any entities with the previous_valid_state attribute exist,
    which indicates the RLC integration is installed and has created sensors.
    
    Args:
        hass: Home Assistant instance
        
    Returns:
        True if RLC integration appears to be available
    """
    for state in hass.states.async_all():
        if state.entity_id.startswith("sensor.") and ATTR_PREVIOUS_VALID_STATE in state.attributes:
            return True
    return False


def get_rlc_sensors_for_entity(hass: "HomeAssistant", target_entity_id: str) -> list[str]:
    """Find RLC sensors that might track a given entity.
    
    RLC sensors track when another entity last changed state. This function
    looks for sensors that have the 'entity_id' attribute matching the target,
    or whose name contains the target entity's name.
    
    Args:
        hass: Home Assistant instance
        target_entity_id: The entity ID to find RLC sensors for (e.g., "light.lamp")
        
    Returns:
        List of RLC sensor entity_ids that likely track the target entity
    """
    rlc_sensors = []
    
    # Extract the name part from the entity_id (e.g., "lamp" from "light.lamp")
    target_name = target_entity_id.split(".", 1)[-1] if "." in target_entity_id else target_entity_id
    
    for state in hass.states.async_all():
        if not state.entity_id.startswith("sensor."):
            continue
            
        if ATTR_PREVIOUS_VALID_STATE not in state.attributes:
            continue
            
        # Check if this RLC sensor tracks our target entity
        # Method 1: Check the 'entity_id' attribute (if RLC stores the source entity)
        tracked_entity = state.attributes.get("entity_id")
        if tracked_entity == target_entity_id:
            rlc_sensors.append(state.entity_id)
            continue
            
        # Method 2: Check if the sensor name contains the target entity's name
        sensor_name = state.entity_id.split(".", 1)[-1] if "." in state.entity_id else state.entity_id
        if target_name in sensor_name:
            rlc_sensors.append(state.entity_id)
            continue
    
    return rlc_sensors


def get_all_rlc_sensors(hass: "HomeAssistant") -> list[str]:
    """Get all RLC sensors in the system.
    
    Args:
        hass: Home Assistant instance
        
    Returns:
        List of all RLC sensor entity_ids
    """
    return [
        state.entity_id
        for state in hass.states.async_all()
        if state.entity_id.startswith("sensor.") and ATTR_PREVIOUS_VALID_STATE in state.attributes
    ]
