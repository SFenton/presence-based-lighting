"""
Custom integration to integrate Presence Based Lighting with Home Assistant.

For more details about this integration, please refer to
https://github.com/sfenton/presence_based_lighting
"""
import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_STATE_CHANGED,
    STATE_ON,
    STATE_OFF,
)
from homeassistant.core import HomeAssistant, Event, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_LIGHT_ENTITIES,
    CONF_PRESENCE_SENSORS,
    CONF_OFF_DELAY,
    DOMAIN,
    PLATFORMS,
    STARTUP_MESSAGE,
)

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up this integration using YAML is not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up this integration using UI."""
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})
        _LOGGER.info(STARTUP_MESSAGE)

    # Create coordinator for this entry
    coordinator = PresenceBasedLightingCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Setup platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start the automation logic
    await coordinator.async_start()

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # Stop the coordinator
    coordinator.async_stop()
    
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


class PresenceBasedLightingCoordinator:
    """Coordinator to manage presence-based lighting automation."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self._is_enabled = True  # Start enabled by default
        self._listeners = []
        self._pending_off_task = None
        self._update_callbacks = []

    @property
    def is_enabled(self) -> bool:
        """Return if automation is enabled."""
        return self._is_enabled

    async def async_enable(self) -> None:
        """Enable the presence automation."""
        if self._is_enabled:
            return
            
        self._is_enabled = True
        self._notify_listeners()
        
        # Check current state and act accordingly
        await self._handle_re_enable()

    async def async_disable(self) -> None:
        """Disable the presence automation."""
        if not self._is_enabled:
            return
            
        self._is_enabled = False
        self._notify_listeners()
        
        # Cancel any pending off task
        if self._pending_off_task:
            self._pending_off_task.cancel()
            self._pending_off_task = None

    async def async_start(self) -> None:
        """Start listening to state changes."""
        light_entities = self.entry.data[CONF_LIGHT_ENTITIES]
        presence_entities = self.entry.data[CONF_PRESENCE_SENSORS]
        
        # Listen to light state changes
        self._listeners.append(
            async_track_state_change_event(
                self.hass,
                light_entities,
                self._handle_light_change,
            )
        )
        
        # Listen to presence sensor changes
        self._listeners.append(
            async_track_state_change_event(
                self.hass,
                presence_entities,
                self._handle_presence_change,
            )
        )

    @callback
    def async_stop(self) -> None:
        """Stop the coordinator."""
        # Cancel any pending tasks
        if self._pending_off_task:
            self._pending_off_task.cancel()
            self._pending_off_task = None
            
        # Remove all listeners
        for remove_listener in self._listeners:
            remove_listener()
        self._listeners.clear()

    @callback
    def async_add_listener(self, update_callback) -> callable:
        """Add a listener for updates."""
        self._update_callbacks.append(update_callback)
        
        def remove_listener():
            self._update_callbacks.remove(update_callback)
        
        return remove_listener

    @callback
    def _notify_listeners(self) -> None:
        """Notify all listeners of an update."""
        for update_callback in self._update_callbacks:
            update_callback()

    async def _handle_light_change(self, event: Event) -> None:
        """Handle light state changes."""
        if not event.data.get("new_state"):
            return
            
        new_state = event.data["new_state"].state
        old_state = event.data.get("old_state")
        
        if not old_state or old_state.state == new_state:
            return
        
        # Check if this was triggered by this integration
        context = event.data["new_state"].context
        
        # If the change has no parent_id or user_id, it's likely internal/automation
        # If it has a user_id, it's a manual change
        # We identify our own changes by checking if we're currently turning lights on/off
        automation_triggered = getattr(self, "_turning_lights", False)
        
        if new_state == STATE_OFF and not automation_triggered:
            # Manual override: lights turned off -> disable automation
            _LOGGER.debug("Manual lights off detected, disabling automation")
            await self.async_disable()
            
        elif new_state == STATE_ON:
            # Manual override: lights turned on -> re-enable automation
            _LOGGER.debug("Lights turned on, enabling automation")
            self._is_enabled = True
            self._notify_listeners()
            
            # Check if room is unoccupied
            if not self._is_any_occupied():
                await self._start_off_timer()

    async def _handle_presence_change(self, event: Event) -> None:
        """Handle presence sensor state changes."""
        if not self._is_enabled:
            return
            
        if not event.data.get("new_state"):
            return
            
        new_state = event.data["new_state"].state
        old_state = event.data.get("old_state")
        
        if not old_state or old_state.state == new_state:
            return
        
        if new_state == STATE_ON:
            # Occupancy detected
            _LOGGER.debug("Occupancy detected, turning on lights")
            
            # Cancel any pending off task
            if self._pending_off_task:
                self._pending_off_task.cancel()
                self._pending_off_task = None
            
            # Turn on lights
            await self._turn_on_lights()
            
        elif new_state == STATE_OFF:
            # Occupancy cleared
            if not self._is_any_occupied():
                _LOGGER.debug("No occupancy detected, starting off timer")
                await self._start_off_timer()

    async def _handle_re_enable(self) -> None:
        """Handle re-enabling the automation."""
        is_occupied = self._is_any_occupied()
        any_light_on = self._is_any_light_on()
        
        if is_occupied and not any_light_on:
            # Occupied + lights off -> turn on
            _LOGGER.debug("Re-enabled: Room occupied, turning on lights")
            await self._turn_on_lights()
            
        elif not is_occupied and any_light_on:
            # Not occupied + lights on -> start timer
            _LOGGER.debug("Re-enabled: Room unoccupied, starting off timer")
            await self._start_off_timer()

    async def _start_off_timer(self) -> None:
        """Start the timer to turn off lights."""
        # Cancel existing timer if any
        if self._pending_off_task:
            self._pending_off_task.cancel()
            self._pending_off_task = None
        
        off_delay = self.entry.data[CONF_OFF_DELAY]
        
        # Create a background task for the timer
        self._pending_off_task = asyncio.create_task(
            self._execute_off_timer(off_delay)
        )
    
    async def _execute_off_timer(self, off_delay: int) -> None:
        """Execute the off timer logic."""
        try:
            # Wait for the delay
            await asyncio.sleep(off_delay)
            
            # Check conditions again
            if (
                self._is_enabled
                and not self._is_any_occupied()
                and self._is_any_light_on()
            ):
                _LOGGER.debug("Off timer expired, turning off lights")
                await self._turn_off_lights()
                
        except asyncio.CancelledError:
            _LOGGER.debug("Off timer cancelled")
        finally:
            self._pending_off_task = None

    def _is_any_occupied(self) -> bool:
        """Check if any presence sensor is on."""
        presence_entities = self.entry.data[CONF_PRESENCE_SENSORS]
        return any(
            self.hass.states.is_state(entity, STATE_ON)
            for entity in presence_entities
        )

    def _is_any_light_on(self) -> bool:
        """Check if any light is on."""
        light_entities = self.entry.data[CONF_LIGHT_ENTITIES]
        return any(
            self.hass.states.is_state(entity, STATE_ON)
            for entity in light_entities
        )

    async def _turn_on_lights(self) -> None:
        """Turn on all configured lights."""
        light_entities = self.entry.data[CONF_LIGHT_ENTITIES]
        self._turning_lights = True
        try:
            await self.hass.services.async_call(
                "light",
                "turn_on",
                {"entity_id": light_entities},
                blocking=True,
            )
        finally:
            self._turning_lights = False

    async def _turn_off_lights(self) -> None:
        """Turn off all configured lights."""
        light_entities = self.entry.data[CONF_LIGHT_ENTITIES]
        self._turning_lights = True
        try:
            await self.hass.services.async_call(
                "light",
                "turn_off",
                {"entity_id": light_entities},
                blocking=True,
            )
        finally:
            self._turning_lights = False
