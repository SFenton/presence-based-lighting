"""Switch platform for Presence Based Lighting."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
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
    ICON_AUTO_REENABLE,
)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up per-entity presence switches and auto re-enable switch."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # Per-entity presence switches
    entities = [
        PresenceEntitySwitch(coordinator, entry, entity_config)
        for entity_config in entry.data.get(CONF_CONTROLLED_ENTITIES, [])
    ]
    
    # Auto re-enable switch for the room
    entities.append(AutoReEnableSwitch(coordinator, entry))
    
    async_add_entities(entities)


class PresenceEntitySwitch(SwitchEntity, RestoreEntity):
    """Switch controlling whether a specific entity follows presence automation."""

    def __init__(self, coordinator, entry, entity_config):
        self._coordinator = coordinator
        self._entry = entry
        self._entity_config = entity_config
        self._entity_id = entity_config[CONF_ENTITY_ID]
        sanitized = slugify(self._entity_id.split(".")[1])

        self._attr_name = self._format_switch_name(self._entity_id)
        self._attr_unique_id = f"{entry.entry_id}_{sanitized}_presence_allowed"
        self._attr_icon = ICON
        self._remove_listener = None
        self._entity_friendly_name: str | None = None

    def _format_switch_name(self, entity_label: str) -> str:
        room = self._entry.data[CONF_ROOM_NAME]
        return f"{room} Presence - {entity_label} - Presence Allowed"

    def _derive_target_friendly_name(self) -> str:
        # Try to get the friendly name from hass states first
        if self._coordinator.hass and self._coordinator.hass.states:
            state = self._coordinator.hass.states.get(self._entity_id)
            if state:
                friendly = state.attributes.get("friendly_name")
                if friendly:
                    return friendly

        # Fall back to entity registry metadata
        if self.hass:
            registry = er.async_get(self.hass)
            if (entry := registry.async_get(self._entity_id)) is not None:
                if entry.name:
                    return entry.name
                if entry.original_name:
                    return entry.original_name

        # Fallback to last part of entity_id if nothing else is available
        object_id = self._entity_id.split(".")[-1]
        return object_id.replace("_", " ").title()

    def _desired_entity_id(self, friendly_name: str) -> str:
        slug_source = f"{self._entry.data[CONF_ROOM_NAME]} Presence {friendly_name} Presence Allowed"
        return f"switch.{slugify(slug_source)}"

    def _update_display_metadata(self) -> None:
        friendly = self._derive_target_friendly_name()
        self._entity_friendly_name = friendly
        self._attr_name = self._format_switch_name(friendly)

        if not self.hass or not self.entity_id:
            return

        registry = er.async_get(self.hass)
        reg_entry = registry.async_get(self.entity_id)
        desired_entity_id = self._desired_entity_id(friendly)

        # Only rename automatically if no custom name is set and entity_id differs
        if reg_entry and not reg_entry.name and reg_entry.entity_id != desired_entity_id:
            try:
                registry.async_update_entity(reg_entry.entity_id, new_entity_id=desired_entity_id)
            except ValueError:
                # Another entity might already use the desired id; skip renaming
                return

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
            "automation_paused": self._coordinator.get_automation_paused(self._entity_id),
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._update_display_metadata()
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


class AutoReEnableSwitch(SwitchEntity, RestoreEntity):
    """Switch controlling whether auto re-enable is active for this room.
    
    When enabled, the coordinator will track presence during the configured
    time window and automatically re-enable presence-based lighting if the
    room was empty for the configured threshold percentage of time.
    """

    def __init__(self, coordinator, entry):
        """Initialize the auto re-enable switch."""
        self._coordinator = coordinator
        self._entry = entry
        self._is_on = False
        
        room_name = entry.data.get(CONF_ROOM_NAME, "Unknown")
        sanitized_room = slugify(room_name)
        
        self._attr_name = f"{room_name} Auto Re-Enable Presence Lighting"
        self._attr_unique_id = f"{entry.entry_id}_auto_reenable"
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
    def is_on(self) -> bool:
        """Return whether auto re-enable is enabled."""
        return self._is_on

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        attrs = {
            "room": self._entry.data.get(CONF_ROOM_NAME),
        }
        
        # Add tracking info from coordinator if available
        if hasattr(self._coordinator, 'get_auto_reenable_tracking_info'):
            tracking_info = self._coordinator.get_auto_reenable_tracking_info()
            attrs.update(tracking_info)
        
        return attrs

    async def async_turn_on(self, **kwargs) -> None:
        """Enable auto re-enable."""
        self._is_on = True
        if hasattr(self._coordinator, 'set_auto_reenable_enabled'):
            self._coordinator.set_auto_reenable_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable auto re-enable."""
        self._is_on = False
        if hasattr(self._coordinator, 'set_auto_reenable_enabled'):
            self._coordinator.set_auto_reenable_enabled(False)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore state on startup."""
        await super().async_added_to_hass()
        
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._is_on = last_state.state == STATE_ON
        else:
            self._is_on = False
        
        # Notify coordinator of initial state
        if hasattr(self._coordinator, 'set_auto_reenable_enabled'):
            self._coordinator.set_auto_reenable_enabled(self._is_on)
