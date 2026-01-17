"""Time platform for Presence Based Lighting auto re-enable time configuration."""
from __future__ import annotations

from datetime import time as dt_time
import logging

from homeassistant.components.time import TimeEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify

from .const import (
    CONF_ROOM_NAME,
    DEFAULT_AUTO_REENABLE_END_TIME,
    DEFAULT_AUTO_REENABLE_START_TIME,
    DOMAIN,
    ICON_AUTO_REENABLE,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up auto re-enable time entities for the config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = [
        AutoReEnableStartTime(coordinator, entry),
        AutoReEnableEndTime(coordinator, entry),
    ]
    
    async_add_entities(entities)


class AutoReEnableTimeEntity(TimeEntity, RestoreEntity):
    """Base class for auto re-enable time entities."""

    _attr_entity_category = EntityCategory.CONFIG
    
    def __init__(self, coordinator, entry, suffix: str, default_time: str):
        """Initialize the time entity."""
        self._coordinator = coordinator
        self._entry = entry
        self._default_time_str = default_time
        self._time_value: dt_time | None = None
        
        room_name = entry.data.get(CONF_ROOM_NAME, "Unknown")
        
        self._attr_name = f"{room_name} Auto Re-Enable {suffix}"
        self._attr_unique_id = f"{entry.entry_id}_auto_reenable_{suffix.lower().replace(' ', '_')}"
        self._attr_icon = ICON_AUTO_REENABLE

    @property
    def device_info(self):
        """Return device information for grouping under the room."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": f"{self._entry.data[CONF_ROOM_NAME]} Presence Lighting",
            "manufacturer": "Presence Based Lighting",
            "model": "Presence Automation",
        }

    @property
    def native_value(self) -> dt_time | None:
        """Return the current time value."""
        return self._time_value

    def _parse_time_string(self, time_str: str) -> dt_time:
        """Parse a time string like '00:00:00' to a time object."""
        parts = time_str.split(":")
        hour = int(parts[0]) if len(parts) > 0 else 0
        minute = int(parts[1]) if len(parts) > 1 else 0
        second = int(parts[2]) if len(parts) > 2 else 0
        return dt_time(hour=hour, minute=minute, second=second)

    async def async_set_value(self, value: dt_time) -> None:
        """Set the time value."""
        self._time_value = value
        self._notify_coordinator()
        self.async_write_ha_state()

    def _notify_coordinator(self) -> None:
        """Notify the coordinator of the time change."""
        # Subclasses implement this to update the coordinator
        pass

    async def async_added_to_hass(self) -> None:
        """Restore state on startup."""
        await super().async_added_to_hass()
        
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (None, "unknown", "unavailable"):
            try:
                # Parse the time string (format: HH:MM:SS)
                self._time_value = self._parse_time_string(last_state.state)
            except (ValueError, AttributeError):
                self._time_value = self._parse_time_string(self._default_time_str)
        else:
            self._time_value = self._parse_time_string(self._default_time_str)
        
        self._notify_coordinator()


class AutoReEnableStartTime(AutoReEnableTimeEntity):
    """Time entity for auto re-enable start time."""

    def __init__(self, coordinator, entry):
        """Initialize start time entity."""
        super().__init__(
            coordinator, 
            entry, 
            "Start Time", 
            DEFAULT_AUTO_REENABLE_START_TIME
        )

    def _notify_coordinator(self) -> None:
        """Notify coordinator of start time change."""
        if self._time_value and hasattr(self._coordinator, 'set_auto_reenable_start_time'):
            self._coordinator.set_auto_reenable_start_time(self._time_value)


class AutoReEnableEndTime(AutoReEnableTimeEntity):
    """Time entity for auto re-enable end time."""

    def __init__(self, coordinator, entry):
        """Initialize end time entity."""
        super().__init__(
            coordinator, 
            entry, 
            "End Time", 
            DEFAULT_AUTO_REENABLE_END_TIME
        )

    def _notify_coordinator(self) -> None:
        """Notify coordinator of end time change."""
        if self._time_value and hasattr(self._coordinator, 'set_auto_reenable_end_time'):
            self._coordinator.set_auto_reenable_end_time(self._time_value)
