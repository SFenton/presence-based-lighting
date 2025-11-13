"""Switch platform for Presence Based Lighting."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback

from .const import (
    ATTR_LIGHTS,
    ATTR_SENSORS,
    ATTR_OFF_DELAY,
    ATTR_ANY_OCCUPIED,
    ATTR_ANY_LIGHT_ON,
    CONF_ROOM_NAME,
    CONF_LIGHT_ENTITIES,
    CONF_PRESENCE_SENSORS,
    CONF_OFF_DELAY,
    DOMAIN,
    ICON,
)


async def async_setup_entry(hass, entry, async_add_entities):
    """Setup switch platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PresenceBasedLightingSwitch(coordinator, entry)])


class PresenceBasedLightingSwitch(SwitchEntity):
    """Presence-based lighting control switch."""

    def __init__(self, coordinator, entry):
        """Initialize the switch."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_name = f"{entry.data[CONF_ROOM_NAME]} Presence Automation"
        self._attr_unique_id = f"{entry.entry_id}_switch"
        self._attr_icon = ICON
        
    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": f"{self._entry.data[CONF_ROOM_NAME]} Presence Lighting",
            "manufacturer": "Presence Based Lighting",
            "model": "Presence Automation",
            "sw_version": "1.0.0",
        }

    @property
    def is_on(self):
        """Return true if presence automation is enabled."""
        return self._coordinator.is_enabled

    async def async_turn_on(self, **kwargs):
        """Enable presence automation."""
        await self._coordinator.async_enable()

    async def async_turn_off(self, **kwargs):
        """Disable presence automation."""
        await self._coordinator.async_disable()

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        light_entities = self._entry.data[CONF_LIGHT_ENTITIES]
        presence_entities = self._entry.data[CONF_PRESENCE_SENSORS]
        
        # Get current states
        any_occupied = any(
            self.hass.states.is_state(entity, "on") 
            for entity in presence_entities
        )
        any_light_on = any(
            self.hass.states.is_state(entity, "on") 
            for entity in light_entities
        )
        
        return {
            ATTR_LIGHTS: light_entities,
            ATTR_SENSORS: presence_entities,
            ATTR_OFF_DELAY: self._entry.data[CONF_OFF_DELAY],
            ATTR_ANY_OCCUPIED: any_occupied,
            ATTR_ANY_LIGHT_ON: any_light_on,
        }

    async def async_added_to_hass(self):
        """Register callbacks when entity is added."""
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
