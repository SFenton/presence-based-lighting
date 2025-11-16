"""Switch platform for Presence Based Lighting."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify

from .const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_INITIAL_PRESENCE_ALLOWED,
    CONF_RESPECTS_PRESENCE_ALLOWED,
    CONF_ROOM_NAME,
    DOMAIN,
    ICON,
)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up per-entity presence switches."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        PresenceEntitySwitch(coordinator, entry, entity_config)
        for entity_config in entry.data.get(CONF_CONTROLLED_ENTITIES, [])
    ]
    async_add_entities(entities)


class PresenceEntitySwitch(SwitchEntity, RestoreEntity):
    """Switch controlling whether a specific entity follows presence automation."""

    def __init__(self, coordinator, entry, entity_config):
        self._coordinator = coordinator
        self._entry = entry
        self._entity_config = entity_config
        self._entity_id = entity_config[CONF_ENTITY_ID]
        sanitized = slugify(self._entity_id.split(".")[1])

        self._attr_name = f"{entry.data[CONF_ROOM_NAME]} {self._entity_id} Presence Allowed"
        self._attr_unique_id = f"{entry.entry_id}_{sanitized}_presence_allowed"
        self._attr_icon = ICON
        self._remove_listener = None

    @property
    def device_info(self):
        """Return device information for grouping switches under the room."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": f"{self._entry.data[CONF_ROOM_NAME]} Presence Lighting",
            "manufacturer": "Presence Based Lighting",
            "model": "Presence Automation",
        }

    @property
    def is_on(self):
        """Whether presence automation is currently allowed for this entity."""
        return self._coordinator.get_presence_allowed(self._entity_id)

    async def async_turn_on(self, **kwargs):
        await self._coordinator.async_set_presence_allowed(self._entity_id, True)

    async def async_turn_off(self, **kwargs):
        await self._coordinator.async_set_presence_allowed(self._entity_id, False)

    @property
    def extra_state_attributes(self):
        return {
            "controlled_entity": self._entity_id,
            CONF_RESPECTS_PRESENCE_ALLOWED: self._entity_config[CONF_RESPECTS_PRESENCE_ALLOWED],
            CONF_DISABLE_ON_EXTERNAL_CONTROL: self._entity_config[CONF_DISABLE_ON_EXTERNAL_CONTROL],
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None:
            initial_state = self._entity_config.get(
                CONF_INITIAL_PRESENCE_ALLOWED, True
            )
        else:
            initial_state = last_state.state == STATE_ON

        self._remove_listener = self._coordinator.register_presence_switch(
            self._entity_id,
            initial_state,
            self._handle_coordinator_update,
        )

    async def async_will_remove_from_hass(self):
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None

    @callback
    def _handle_coordinator_update(self):
        self.async_write_ha_state()
