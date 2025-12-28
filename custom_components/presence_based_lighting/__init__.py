"""Presence Based Lighting integration entry point."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Callable, Dict

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
	EVENT_CALL_SERVICE,
	EVENT_STATE_CHANGED,
	STATE_OFF,
	STATE_ON,
)
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
import homeassistant.helpers.config_validation as cv

from .const import (
	AUTOMATION_MODE_AUTOMATIC,
	AUTOMATION_MODE_PRESENCE_LOCK,
	CONF_AUTOMATION_MODE,
	CONF_CLEARING_SENSORS,
	CONF_CONTROLLED_ENTITIES,
	CONF_DISABLE_ON_EXTERNAL_CONTROL,
	CONF_ENTITY_ID,
	CONF_ENTITY_OFF_DELAY,
	CONF_INITIAL_PRESENCE_ALLOWED,
	CONF_MANUAL_DISABLE_STATES,
	CONF_OFF_DELAY,
	CONF_PRESENCE_CLEARED_SERVICE,
	CONF_PRESENCE_CLEARED_STATE,
	CONF_PRESENCE_DETECTED_SERVICE,
	CONF_PRESENCE_DETECTED_STATE,
	CONF_PRESENCE_SENSORS,
	CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
	CONF_REQUIRE_VACANCY_FOR_CLEARED,
	CONF_RESPECTS_PRESENCE_ALLOWED,
	CONF_RLC_TRACKING_ENTITY,
	CONF_ROOM_NAME,
	DEFAULT_AUTOMATION_MODE,
	NO_ACTION,
	DEFAULT_CLEARED_SERVICE,
	DEFAULT_CLEARED_STATE,
	DEFAULT_DETECTED_SERVICE,
	DEFAULT_DETECTED_STATE,
	DEFAULT_DISABLE_ON_EXTERNAL,
	DEFAULT_INITIAL_PRESENCE_ALLOWED,
	DEFAULT_OFF_DELAY,
	DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED,
	DEFAULT_REQUIRE_VACANCY_FOR_CLEARED,
	DEFAULT_RESPECTS_PRESENCE_ALLOWED,
	DOMAIN,
	PLATFORMS,
	STARTUP_MESSAGE,
)
from .interceptor import PresenceLockInterceptor, is_interceptor_available
from .real_last_changed import get_effective_state, is_entity_on, is_entity_off, is_real_last_changed_entity

_LOGGER = logging.getLogger(__package__)


SERVICE_RESUME_AUTOMATION = "resume_automation"
SERVICE_PAUSE_AUTOMATION = "pause_automation"

SERVICE_SCHEMA = vol.Schema({
	vol.Optional("entity_id"): cv.entity_id,
})


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
	"""Set up the Presence Based Lighting component."""

	async def handle_resume_automation(call):
		"""Handle the resume_automation service call."""
		target_entity_id = call.data.get("entity_id")
		
		# Get target switches from the service call
		target_switches = []
		if hasattr(call, "target") and call.target:
			target_switches = call.target.get("entity_id", [])
			if isinstance(target_switches, str):
				target_switches = [target_switches]
		
		if not target_switches:
			_LOGGER.warning("resume_automation called without target switch")
			return
		
		# Find coordinators for the target switches
		for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
			if not isinstance(coordinator, PresenceBasedLightingCoordinator):
				continue
			
			# Check if this coordinator's switch matches any target
			switch_entity_id = f"switch.{coordinator.entry.data.get(CONF_ROOM_NAME, '').lower().replace(' ', '_')}_presence_lighting"
			if switch_entity_id not in target_switches:
				continue
			
			# Resume automation for entities
			for entity_id in coordinator._entity_states:
				if target_entity_id is None or entity_id == target_entity_id:
					_LOGGER.debug("Resuming automation for %s", entity_id)
					coordinator.set_automation_paused(entity_id, False)

	async def handle_pause_automation(call):
		"""Handle the pause_automation service call."""
		target_entity_id = call.data.get("entity_id")
		
		# Get target switches from the service call
		target_switches = []
		if hasattr(call, "target") and call.target:
			target_switches = call.target.get("entity_id", [])
			if isinstance(target_switches, str):
				target_switches = [target_switches]
		
		if not target_switches:
			_LOGGER.warning("pause_automation called without target switch")
			return
		
		# Find coordinators for the target switches
		for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
			if not isinstance(coordinator, PresenceBasedLightingCoordinator):
				continue
			
			# Check if this coordinator's switch matches any target
			switch_entity_id = f"switch.{coordinator.entry.data.get(CONF_ROOM_NAME, '').lower().replace(' ', '_')}_presence_lighting"
			if switch_entity_id not in target_switches:
				continue
			
			# Pause automation for entities
			for entity_id in coordinator._entity_states:
				if target_entity_id is None or entity_id == target_entity_id:
					_LOGGER.debug("Pausing automation for %s", entity_id)
					coordinator.set_automation_paused(entity_id, True)

	# Register services
	hass.services.async_register(
		DOMAIN, SERVICE_RESUME_AUTOMATION, handle_resume_automation, schema=SERVICE_SCHEMA
	)
	hass.services.async_register(
		DOMAIN, SERVICE_PAUSE_AUTOMATION, handle_pause_automation, schema=SERVICE_SCHEMA
	)
	_LOGGER.debug("Registered %s and %s services", SERVICE_RESUME_AUTOMATION, SERVICE_PAUSE_AUTOMATION)

	return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
	"""Migrate old entry to new version.
	
	Version 2 -> 3: Add automation_mode derived from legacy boolean toggles.
	Version 3 -> 4: Add manual_disable_states for automatic mode.
	Version 4 -> 5: Add presence_sensor_mappings for real_last_changed support.
	"""
	_LOGGER.debug(
		"Migrating config entry %s from version %s",
		config_entry.entry_id,
		config_entry.version,
	)

	if config_entry.version == 2:
		# Version 2 -> 3: Add automation_mode to each controlled entity
		new_data = {**config_entry.data}
		controlled_entities = new_data.get(CONF_CONTROLLED_ENTITIES, [])
		updated_entities = []

		for entity_config in controlled_entities:
			updated_config = {**entity_config}
			
			# Only add automation_mode if not already present
			if CONF_AUTOMATION_MODE not in updated_config:
				# Derive automation_mode from legacy boolean fields
				require_occupancy = updated_config.get(
					CONF_REQUIRE_OCCUPANCY_FOR_DETECTED, False
				)
				require_vacancy = updated_config.get(
					CONF_REQUIRE_VACANCY_FOR_CLEARED, False
				)
				
				# If either presence lock toggle was enabled, use presence_lock mode
				if require_occupancy or require_vacancy:
					updated_config[CONF_AUTOMATION_MODE] = AUTOMATION_MODE_PRESENCE_LOCK
				else:
					updated_config[CONF_AUTOMATION_MODE] = AUTOMATION_MODE_AUTOMATIC
				
				# Normalize the legacy booleans based on the mode
				is_automatic = updated_config[CONF_AUTOMATION_MODE] == AUTOMATION_MODE_AUTOMATIC
				is_presence_lock = updated_config[CONF_AUTOMATION_MODE] == AUTOMATION_MODE_PRESENCE_LOCK
				updated_config[CONF_DISABLE_ON_EXTERNAL_CONTROL] = is_automatic
				updated_config[CONF_REQUIRE_OCCUPANCY_FOR_DETECTED] = is_presence_lock
				updated_config[CONF_REQUIRE_VACANCY_FOR_CLEARED] = is_presence_lock

			updated_entities.append(updated_config)

		new_data[CONF_CONTROLLED_ENTITIES] = updated_entities

		hass.config_entries.async_update_entry(
			config_entry, data=new_data, version=3
		)
		_LOGGER.info(
			"Migration of entry %s from version 2 to 3 successful",
			config_entry.entry_id,
		)

	if config_entry.version == 3:
		# Version 3 -> 4: Add manual_disable_states to each controlled entity
		new_data = {**config_entry.data}
		controlled_entities = new_data.get(CONF_CONTROLLED_ENTITIES, [])
		updated_entities = []

		for entity_config in controlled_entities:
			updated_config = {**entity_config}
			
			# Add empty manual_disable_states if not present
			if CONF_MANUAL_DISABLE_STATES not in updated_config:
				updated_config[CONF_MANUAL_DISABLE_STATES] = []

			updated_entities.append(updated_config)

		new_data[CONF_CONTROLLED_ENTITIES] = updated_entities

		hass.config_entries.async_update_entry(
			config_entry, data=new_data, version=4
		)
		_LOGGER.info(
			"Migration of entry %s from version 3 to 4 successful",
			config_entry.entry_id,
		)

	if config_entry.version == 4:
		# Version 4 -> 5: No changes needed - just bump version
		# (sensor mappings were removed in favor of reading real_last_changed attributes directly)
		hass.config_entries.async_update_entry(
			config_entry, data={**config_entry.data}, version=5
		)
		_LOGGER.info(
			"Migration of entry %s from version 4 to 5 successful",
			config_entry.entry_id,
		)

	return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Set up Presence Based Lighting via the UI."""
	
	try:
		_LOGGER.info("Setting up Presence Based Lighting entry: %s", entry.entry_id)

		if DOMAIN not in hass.data:
			hass.data[DOMAIN] = {}
			_LOGGER.info(STARTUP_MESSAGE)

		_LOGGER.debug("Creating coordinator for entry: %s with data: %s", entry.entry_id, entry.data)
		coordinator = PresenceBasedLightingCoordinator(hass, entry)
		hass.data[DOMAIN][entry.entry_id] = coordinator

		_LOGGER.debug("Setting up platforms: %s", PLATFORMS)
		await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
		
		_LOGGER.debug("Starting coordinator for entry: %s", entry.entry_id)
		await coordinator.async_start()

		entry.async_on_unload(entry.add_update_listener(async_reload_entry))
		_LOGGER.info("Successfully set up Presence Based Lighting entry: %s", entry.entry_id)
		return True
	except Exception as err:
		_LOGGER.exception("Failed to set up Presence Based Lighting entry %s: %s", entry.entry_id, err)
		return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Unload a config entry."""
	
	try:
		_LOGGER.info("Unloading Presence Based Lighting entry: %s", entry.entry_id)
		
		coordinator: PresenceBasedLightingCoordinator = hass.data[DOMAIN][entry.entry_id]
		coordinator.async_stop()

		_LOGGER.debug("Unloading platforms for entry: %s", entry.entry_id)
		unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
		if unload_ok:
			hass.data[DOMAIN].pop(entry.entry_id)
			_LOGGER.info("Successfully unloaded Presence Based Lighting entry: %s", entry.entry_id)
		else:
			_LOGGER.error("Failed to unload platforms for entry: %s", entry.entry_id)

		return unload_ok
	except Exception as err:
		_LOGGER.exception("Error unloading Presence Based Lighting entry %s: %s", entry.entry_id, err)
		return False


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
	"""Reload an existing config entry via Home Assistant helpers.

	Using Home Assistant's built-in reload ensures update listeners are
	registered only once and prevents runaway reload loops when options
	changes trigger async_update_entry.
	"""

	try:
		_LOGGER.info("Reloading Presence Based Lighting entry via HA: %s", entry.entry_id)
		await hass.config_entries.async_reload(entry.entry_id)
		_LOGGER.info("Successfully reloaded Presence Based Lighting entry: %s", entry.entry_id)
	except Exception as err:
		_LOGGER.exception("Error reloading Presence Based Lighting entry %s: %s", entry.entry_id, err)


class PresenceBasedLightingCoordinator:
	"""Coordinator managing per-entity presence automation."""

	def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
		self.hass = hass
		self.entry = entry
		self._listeners: list[Callable[[], None]] = []
		self._entity_states: Dict[str, dict] = {}
		self._interceptor: PresenceLockInterceptor | None = None
		self._using_interceptor: bool = False

		try:
			_LOGGER.debug("Initializing coordinator for entry: %s", entry.entry_id)
			controlled_entities = entry.data.get(CONF_CONTROLLED_ENTITIES, [])
			_LOGGER.debug("Processing %d controlled entities", len(controlled_entities))
			
			# Track seen entity IDs to detect duplicates
			seen_entities = set()
			
			for idx, entity in enumerate(controlled_entities):
				entity_id = entity.get(CONF_ENTITY_ID)
				if not entity_id:
					_LOGGER.error("Entity at index %d is missing entity_id: %s", idx, entity)
					continue
				
				if entity_id in seen_entities:
					_LOGGER.warning("Duplicate entity_id detected: %s (skipping duplicate)", entity_id)
					continue
				
				seen_entities.add(entity_id)
				_LOGGER.debug("Configuring entity %d: %s with config: %s", idx, entity_id, entity)
				
				self._entity_states[entity_id] = {
					"config": entity,
					"domain": entity_id.split(".")[0],
					"presence_allowed": entity.get(
						CONF_INITIAL_PRESENCE_ALLOWED, DEFAULT_INITIAL_PRESENCE_ALLOWED
					),
					"automation_paused": False,  # Transient pause due to manual control
					"callbacks": set(),
					"contexts": deque(maxlen=20),
					"off_timer": None,
					"last_effective_state": None,  # Track RLC effective state for change detection
				}
			
			_LOGGER.info("Coordinator initialized with %d unique entities", len(self._entity_states))
		except Exception as err:
			_LOGGER.exception("Error initializing PresenceBasedLightingCoordinator: %s", err)
			raise

	async def async_start(self) -> None:
		"""Begin tracking sensors and controlled entities."""
		
		try:
			_LOGGER.debug("Starting coordinator for entry: %s", self.entry.entry_id)
			
			# Set up hass-interceptor for Presence Lock mode (if available)
			self._interceptor = PresenceLockInterceptor(
				self.hass,
				self.entry,
				self._is_any_occupied,
			)
			self._using_interceptor = self._interceptor.setup()
			
			if self._using_interceptor:
				_LOGGER.info(
					"Using hass-interceptor for Presence Lock mode (proactive blocking)"
				)
			elif is_interceptor_available():
				_LOGGER.debug(
					"hass-interceptor available but no presence-lock entities configured"
				)
			else:
				_LOGGER.debug(
					"hass-interceptor not installed, using fallback (reactive reversion)"
				)
			
			controlled_ids = list(self._entity_states.keys())
			presence_sensors = self.entry.data.get(CONF_PRESENCE_SENSORS, [])
			clearing_sensors = self.entry.data.get(CONF_CLEARING_SENSORS, [])
			
			# Store presence and clearing sensors directly
			# RLC sensors are handled via their previous_valid_state attribute
			self._presence_sensors = set(presence_sensors)
			self._clearing_sensors = set(clearing_sensors) if clearing_sensors else set(presence_sensors)
			
			# Combine all sensors for state change tracking
			all_sensors = list(set(presence_sensors + clearing_sensors))
			
			# Initialize last_effective_state for RLC-tracked entities
			# This prevents the first state change event from being treated as a "change"
			# which would incorrectly trigger manual control logic on startup
			for entity_id, entity_state in self._entity_states.items():
				cfg = entity_state["config"]
				rlc_tracking_entity = cfg.get(CONF_RLC_TRACKING_ENTITY)
				_LOGGER.debug("Checking RLC init for %s: tracking_entity=%s", entity_id, rlc_tracking_entity)
				if rlc_tracking_entity:
					rlc_state = get_effective_state(self.hass, rlc_tracking_entity)
					_LOGGER.debug("RLC state for %s from %s: %s", entity_id, rlc_tracking_entity, rlc_state)
					if rlc_state is not None:
						entity_state["last_effective_state"] = rlc_state
						_LOGGER.debug(
							"Initialized last_effective_state for %s from RLC %s: %s",
							entity_id, rlc_tracking_entity, rlc_state
						)
					else:
						_LOGGER.debug(
							"RLC sensor %s not available yet for %s, last_effective_state remains None",
							rlc_tracking_entity, entity_id
						)
			
			_LOGGER.debug("Setting up listeners for %d controlled entities: %s", 
						 len(controlled_ids), controlled_ids)
			_LOGGER.debug("Setting up listeners for %d presence sensors: %s", 
						 len(presence_sensors), presence_sensors)
			_LOGGER.debug("Setting up listeners for %d clearing sensors: %s", 
						 len(clearing_sensors) if clearing_sensors else len(presence_sensors), 
						 clearing_sensors if clearing_sensors else presence_sensors)

			if controlled_ids:
				self._listeners.append(
					async_track_state_change_event(
						self.hass,
						controlled_ids,
						self._handle_controlled_entity_change,
					)
				)
				_LOGGER.debug("Registered state change listener for controlled entities")

			if all_sensors:
				self._listeners.append(
					async_track_state_change_event(
						self.hass,
						all_sensors,
						self._handle_presence_change,
					)
				)
				_LOGGER.debug("Registered state change listener for presence/clearing sensors")

			self._listeners.append(
				self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._handle_service_call)
			)
			_LOGGER.debug("Registered service call listener")
			
			_LOGGER.info("Coordinator started successfully with %d listeners", len(self._listeners))
		except Exception as err:
			_LOGGER.exception("Error starting PresenceBasedLightingCoordinator: %s", err)
			raise
			raise

	@callback
	def async_stop(self) -> None:
		"""Stop tracking events."""
		
		try:
			_LOGGER.debug("Stopping coordinator for entry: %s", self.entry.entry_id)

			# Clean up hass-interceptor registrations
			if self._interceptor:
				self._interceptor.teardown()
				self._interceptor = None
				self._using_interceptor = False

			# Cancel all per-entity timers
			cancelled_count = 0
			for entity_id, entity_state in self._entity_states.items():
				if entity_state["off_timer"]:
					entity_state["off_timer"].cancel()
					entity_state["off_timer"] = None
					cancelled_count += 1
			
			if cancelled_count > 0:
				_LOGGER.debug("Cancelled %d per-entity off timers", cancelled_count)

			listener_count = len(self._listeners)
			for remove in self._listeners:
				try:
					remove()
				except Exception as err:
					_LOGGER.error("Error removing listener: %s", err)
			self._listeners.clear()
			
			_LOGGER.info("Coordinator stopped successfully, removed %d listeners", listener_count)
		except Exception as err:
			_LOGGER.exception("Error stopping PresenceBasedLightingCoordinator: %s", err)

	def register_presence_switch(
		self, entity_id: str, initial_state: bool, update_callback: Callable[[], None]
	) -> Callable[[], None]:
		"""Register a per-entity presence switch callback."""

		entity_state = self._entity_states[entity_id]
		entity_state["presence_allowed"] = initial_state
		entity_state["callbacks"].add(update_callback)
		update_callback()

		def _remove() -> None:
			entity_state["callbacks"].discard(update_callback)

		return _remove

	def get_presence_allowed(self, entity_id: str) -> bool:
		return self._entity_states[entity_id]["presence_allowed"]

	async def async_set_presence_allowed(self, entity_id: str, allowed: bool) -> None:
		"""Set user-controlled presence_allowed state (persisted by switch)."""
		entity_state = self._entity_states[entity_id]
		if entity_state["presence_allowed"] == allowed:
			return

		entity_state["presence_allowed"] = allowed
		self._notify_switch(entity_id)

		# If enabling and not paused, check room state and act accordingly
		if allowed and not entity_state.get("automation_paused", False):
			if self._is_any_occupied():
				# Room is occupied, apply detected action
				await self._apply_action_to_entity(entity_state, CONF_PRESENCE_DETECTED_SERVICE)
			# Always start off timer when re-enabling - this handles the case where
			# the room is empty and lights should be turned off after the delay
			await self._start_off_timer()

	def get_automation_paused(self, entity_id: str) -> bool:
		"""Get whether automation is temporarily paused for this entity."""
		return self._entity_states[entity_id].get("automation_paused", False)

	def set_automation_paused(self, entity_id: str, paused: bool) -> None:
		"""Set transient automation pause state (not persisted, based on manual control).
		
		This is separate from presence_allowed:
		- presence_allowed: User-controlled, persisted across reboots
		- automation_paused: Automatic, transient, based on manual_disable_states
		"""
		entity_state = self._entity_states[entity_id]
		old_paused = entity_state.get("automation_paused", False)
		if old_paused == paused:
			return
		
		entity_state["automation_paused"] = paused
		_LOGGER.debug(
			"Automation %s for %s",
			"paused" if paused else "resumed",
			entity_id
		)
		# Notify switch so it can update attributes
		self._notify_switch(entity_id)

	def _notify_switch(self, entity_id: str) -> None:
		for callback_fn in list(self._entity_states[entity_id]["callbacks"]):
			callback_fn()

	async def _handle_service_call(self, event: Event) -> None:
		try:
			service_data = event.data.get("service_data") or {}
			target = service_data.get("entity_id")
			if not target:
				return

			target_entities = target if isinstance(target, list) else [target]
			service = event.data.get("service")

			# Expand groups and collect all target entity IDs
			expanded_entities = []
			for entity_id in target_entities:
				# Skip if entity_id is not a string (could be a set or other type)
				if not isinstance(entity_id, str):
					_LOGGER.debug("Skipping non-string entity_id: %s (type: %s)", entity_id, type(entity_id))
					continue
				if entity_id in self._entity_states:
					expanded_entities.append(entity_id)
				else:
					# Check if this might be a group - expand its members
					state = self.hass.states.get(entity_id)
					if state and state.attributes.get("entity_id"):
						group_members = state.attributes.get("entity_id", [])
						# group_members could be a list, set, or tuple - iterate safely
						for member in group_members:
							if isinstance(member, str) and member in self._entity_states:
								expanded_entities.append(member)

			for entity_id in expanded_entities:
				if self._is_context_ours(entity_id, event.context):
					continue
				await self._handle_external_action(entity_id, service)
		except Exception as err:
			_LOGGER.exception("Error handling service call event: %s", err)

	async def _handle_controlled_entity_change(self, event: Event) -> None:
		try:
			entity_id = event.data.get("entity_id")
			if not entity_id or entity_id not in self._entity_states:
				return

			new_state = event.data.get("new_state")
			old_state = event.data.get("old_state")
			if not new_state or not old_state or new_state.state == old_state.state:
				return

			if self._is_context_ours(entity_id, new_state.context):
				return

			entity_state = self._entity_states[entity_id]
			cfg = entity_state["config"]

			# Check if an RLC tracking entity is configured for this entity
			# If so, use the RLC sensor's state to determine if this is a "real" change
			rlc_tracking_entity = cfg.get(CONF_RLC_TRACKING_ENTITY)
			if rlc_tracking_entity:
				# Get the "real" state from the RLC sensor
				rlc_state = get_effective_state(self.hass, rlc_tracking_entity)
				if rlc_state is None:
					_LOGGER.debug(
						"RLC tracking entity %s unavailable for %s, ignoring state change",
						rlc_tracking_entity, entity_id
					)
					return
				
				# Use the RLC sensor's previous_valid_state as the effective state
				# This filters out spurious changes from reboots/power outages
				effective_new_state = rlc_state
				
				# Check if this is the first event for this entity (startup initialization)
				# If so, just record the state and don't trigger manual control logic
				last_effective = entity_state.get("last_effective_state")
				if last_effective is None:
					entity_state["last_effective_state"] = effective_new_state
					_LOGGER.debug(
						"RLC tracking entity %s for %s: first event, initializing last_effective_state to %s (skipping manual control)",
						rlc_tracking_entity, entity_id, effective_new_state
					)
					return
				
				# Check if the effective state actually changed
				# This prevents spurious raw state changes (e.g., unavailable -> off) 
				# from incorrectly triggering manual control logic when the RLC-tracked
				# effective state hasn't changed
				if effective_new_state == last_effective:
					_LOGGER.debug(
						"RLC tracking entity %s for %s: effective state unchanged (%s), ignoring",
						rlc_tracking_entity, entity_id, effective_new_state
					)
					return
				
				# Update tracked effective state
				entity_state["last_effective_state"] = effective_new_state
				
				_LOGGER.debug(
					"Using RLC tracking entity %s for %s: effective state = %s (raw state = %s)",
					rlc_tracking_entity, entity_id, effective_new_state, new_state.state
				)
			else:
				# No RLC tracking - use the entity's direct state
				effective_new_state = new_state.state

			# Check presence lock first - this takes priority
			if await self._check_and_apply_presence_lock(entity_state, effective_new_state):
				return  # Presence lock handled the state change

			if not cfg[CONF_DISABLE_ON_EXTERNAL_CONTROL]:
				return

			# Use manual_disable_states for automatic mode behavior
			# If the new state is in manual_disable_states, pause automation
			# If the new state is NOT in manual_disable_states, resume automation
			# If the key exists (even empty), use new behavior. If missing, use legacy.
			if CONF_MANUAL_DISABLE_STATES in cfg:
				manual_disable_states = cfg[CONF_MANUAL_DISABLE_STATES]
				# New behavior: use configured manual_disable_states list
				# Empty list = no states pause automation
				if effective_new_state in manual_disable_states:
					_LOGGER.debug(
						"Manual control: %s set to %s (in disable list), pausing automation",
						entity_id, effective_new_state
					)
					self.set_automation_paused(entity_id, True)
				else:
					_LOGGER.debug(
						"Manual control: %s set to %s (not in disable list), resuming automation",
						entity_id, effective_new_state
					)
					self.set_automation_paused(entity_id, False)
					if not self._is_any_occupied():
						await self._start_off_timer()
			else:
				# Legacy behavior: no manual_disable_states configured
				# Any manual change pauses/resumes automation (backward compatible)
				if effective_new_state == cfg[CONF_PRESENCE_CLEARED_STATE]:
					self.set_automation_paused(entity_id, True)
				elif effective_new_state == cfg[CONF_PRESENCE_DETECTED_STATE]:
					self.set_automation_paused(entity_id, False)
					if not self._is_any_occupied():
						await self._start_off_timer()
		except Exception as err:
			_LOGGER.exception("Error handling controlled entity change for %s: %s", event.data.get("entity_id"), err)

	async def _handle_external_action(self, entity_id: str, service: str | None) -> None:
		entity_state = self._entity_states[entity_id]
		cfg = entity_state["config"]

		# Check presence lock first - this takes priority
		# Determine what state the service would result in
		target_state = None
		if service == cfg[CONF_PRESENCE_DETECTED_SERVICE]:
			target_state = cfg[CONF_PRESENCE_DETECTED_STATE]
		elif service == cfg[CONF_PRESENCE_CLEARED_SERVICE]:
			target_state = cfg[CONF_PRESENCE_CLEARED_STATE]
		
		if target_state and await self._check_and_apply_presence_lock(entity_state, target_state):
			return  # Presence lock handled the state change

		if not cfg[CONF_DISABLE_ON_EXTERNAL_CONTROL]:
			return

		# Use manual_disable_states for automatic mode behavior
		# If the target state is in manual_disable_states, pause automation
		# If the target state is NOT in manual_disable_states, resume automation
		# If the key exists (even empty), use new behavior. If missing, use legacy.
		if CONF_MANUAL_DISABLE_STATES in cfg:
			manual_disable_states = cfg[CONF_MANUAL_DISABLE_STATES]
			# New behavior: use configured manual_disable_states list
			# Empty list = no states pause automation
			if target_state and target_state in manual_disable_states:
				_LOGGER.debug(
					"Manual action: %s targeting %s (in disable list), pausing automation",
					entity_id, target_state
				)
				self.set_automation_paused(entity_id, True)
			elif target_state:
				_LOGGER.debug(
					"Manual action: %s targeting %s (not in disable list), resuming automation",
					entity_id, target_state
				)
				self.set_automation_paused(entity_id, False)
				if not self._is_any_occupied():
					await self._start_off_timer()
		else:
			# Legacy behavior: based on service type
			if service == cfg[CONF_PRESENCE_CLEARED_SERVICE]:
				self.set_automation_paused(entity_id, True)
			elif service == cfg[CONF_PRESENCE_DETECTED_SERVICE]:
				self.set_automation_paused(entity_id, False)
				if not self._is_any_occupied():
					await self._start_off_timer()

	async def _check_and_apply_presence_lock(self, entity_state: dict, new_state: str) -> bool:
		"""Check presence lock conditions and revert state if needed.
		
		Returns True if a presence lock was triggered and the state was reverted.
		
		When hass-interceptor is active, this is a fallback that should rarely
		trigger (interceptor blocks proactively). When not active, this is the
		primary mechanism that reverts state reactively after it changes.
		"""
		# Skip if using interceptor - it handles blocking proactively
		if self._using_interceptor:
			return False
		
		cfg = entity_state["config"]
		entity_id = cfg[CONF_ENTITY_ID]
		
		require_occ = cfg.get(CONF_REQUIRE_OCCUPANCY_FOR_DETECTED, DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED)
		require_vac = cfg.get(CONF_REQUIRE_VACANCY_FOR_CLEARED, DEFAULT_REQUIRE_VACANCY_FOR_CLEARED)
		
		# If entity is being turned ON (detected state) but room is empty and lock is enabled
		if new_state == cfg[CONF_PRESENCE_DETECTED_STATE] and require_occ and not self._is_any_occupied():
			_LOGGER.debug("Presence lock (fallback): reverting %s to cleared state (room is empty)", entity_id)
			# Force the reversion without checking current state (since the triggering action may still be in progress)
			await self._force_apply_action(entity_state, CONF_PRESENCE_CLEARED_SERVICE)
			return True
		
		# If entity is being turned OFF (cleared state) but room is occupied and lock is enabled
		if new_state == cfg[CONF_PRESENCE_CLEARED_STATE] and require_vac and self._is_any_occupied():
			_LOGGER.debug("Presence lock (fallback): reverting %s to detected state (room is occupied)", entity_id)
			# Force the reversion without checking current state (since the triggering action may still be in progress)
			await self._force_apply_action(entity_state, CONF_PRESENCE_DETECTED_SERVICE)
			return True
		
		return False

	async def _force_apply_action(self, entity_state: dict, service_key: str) -> None:
		"""Apply an action without checking current state - used for presence lock reversions."""
		try:
			config = entity_state["config"]
			entity_id = config[CONF_ENTITY_ID]
			service = config[service_key]
			
			# Skip if service is set to NO_ACTION
			if service == NO_ACTION:
				_LOGGER.debug("Skipping forced action for %s, service is NO_ACTION", entity_id)
				return

			context = Context()
			entity_state["contexts"].append(context.id)
			
			_LOGGER.debug("Force calling service %s.%s for entity %s", entity_state["domain"], service, entity_id)
			await self.hass.services.async_call(
				entity_state["domain"],
				service,
				{"entity_id": entity_id},
				blocking=True,
				context=context,
			)
			_LOGGER.debug("Force service call completed for %s", entity_id)
		except Exception as err:  # pragma: no cover - log unexpected HA errors
			_LOGGER.exception("Failed to force call service %s.%s for %s: %s", 
							 entity_state.get("domain"), config.get(service_key), 
							 config.get(CONF_ENTITY_ID), err)

	async def _handle_presence_change(self, event: Event) -> None:
		"""Handle state changes on presence/clearing sensors.
		
		For real_last_changed sensors, the state is a timestamp that changes when
		the source entity changes. We read the previous_valid_state attribute to
		determine if the source is now on or off.
		
		For regular binary sensors, we use the state directly.
		"""
		try:
			entity_id = event.data.get("entity_id")
			new_state = event.data.get("new_state")
			old_state = event.data.get("old_state")
			if not new_state or not old_state:
				return

			# For real_last_changed sensors, the "state" is a timestamp
			# We need to check previous_valid_state attribute for actual on/off
			# Pass the state object so we can detect RLC by attribute presence
			is_rlc = is_real_last_changed_entity(entity_id, new_state)
			
			if is_rlc:
				# For RLC sensors, compare the previous_valid_state attribute
				# The state itself is a timestamp, so we look at the attribute
				from .real_last_changed import ATTR_PREVIOUS_VALID_STATE
				old_effective = old_state.attributes.get(ATTR_PREVIOUS_VALID_STATE)
				new_effective = new_state.attributes.get(ATTR_PREVIOUS_VALID_STATE)
				
				# Skip if the effective state didn't actually change
				if old_effective == new_effective:
					_LOGGER.debug("RLC sensor %s timestamp changed but previous_valid_state unchanged (%s)", 
								 entity_id, new_effective)
					return
				
				_LOGGER.debug("RLC sensor %s previous_valid_state changed: %s -> %s", 
							 entity_id, old_effective, new_effective)
				currently_on = new_effective == "on"
				currently_off = new_effective == "off"
			else:
				# For regular sensors, check if state actually changed
				if new_state.state == old_state.state:
					return
				currently_on = new_state.state == STATE_ON
				currently_off = new_state.state == STATE_OFF
				_LOGGER.debug("Presence change detected on %s: %s -> %s", 
							 entity_id, old_state.state, new_state.state)

			presence_sensors = getattr(self, '_presence_sensors', set())
			clearing_sensors = getattr(self, '_clearing_sensors', set())
			
			# Trigger detected action if a presence sensor turns on
			if currently_on and entity_id in presence_sensors:
				_LOGGER.debug("Presence detected, cancelling timers")
				# Cancel all per-entity timers when presence detected
				for entity_state in self._entity_states.values():
					if entity_state["off_timer"]:
						entity_state["off_timer"].cancel()
						entity_state["off_timer"] = None
				await self._apply_presence_action(CONF_PRESENCE_DETECTED_SERVICE)
				# Start off-timer immediately after presence detected
				# This handles the case where clearing sensors may already be cleared
				# (e.g., primer sensor triggers but person never enters main room)
				# Timer will check clearing sensor state when it fires
				_LOGGER.debug("Starting off timer after presence detected")
				await self._start_off_timer()
			# Trigger cleared action if a clearing sensor turns off AND all clearing sensors are clear
			elif currently_off:
				# For clearing, use clearing sensors if specified, else fall back to presence sensors
				effective_clearing = clearing_sensors if clearing_sensors else presence_sensors
				if entity_id in effective_clearing and self._are_clearing_sensors_clear():
					_LOGGER.debug("Presence cleared, starting off timer")
					await self._start_off_timer()
		except Exception as err:
			_LOGGER.exception("Error handling presence change: %s", err)

	async def _apply_presence_action(self, service_key: str) -> None:
		_LOGGER.debug("Applying presence action %s to %d entities: %s", 
					 service_key, len(self._entity_states), list(self._entity_states.keys()))
		for entity_state in self._entity_states.values():
			entity_id = entity_state["config"].get(CONF_ENTITY_ID, "unknown")
			if not self._should_follow_presence(entity_state):
				_LOGGER.debug("Skipping %s - presence_allowed is False", entity_id)
				continue
			await self._apply_action_to_entity(entity_state, service_key)

	async def _apply_action_to_entity(self, entity_state: dict, service_key: str) -> None:
		try:
			config = entity_state["config"]
			entity_id = config[CONF_ENTITY_ID]
			service = config[service_key]
			
			# Skip if service is set to NO_ACTION
			if service == NO_ACTION:
				_LOGGER.debug("Skipping action for %s, service is NO_ACTION", entity_id)
				return
			
			target_state_key = (
				CONF_PRESENCE_DETECTED_STATE
				if service_key == CONF_PRESENCE_DETECTED_SERVICE
				else CONF_PRESENCE_CLEARED_STATE
			)
			target_state = config[target_state_key]
			current_state = self.hass.states.get(entity_id)
			
			# For turn_on (detected), always send the command to interrupt any transition
			# This fixes the bug where re-entering during light fade-off wouldn't turn lights back on
			# For turn_off (cleared), we can skip if already off since there's no transition to interrupt
			if service_key == CONF_PRESENCE_CLEARED_SERVICE:
				if current_state and current_state.state == target_state:
					_LOGGER.debug("Entity %s already in target state %s", entity_id, target_state)
					return

			context = Context()
			entity_state["contexts"].append(context.id)
			
			# Update tracked effective state so our own changes don't trigger manual control logic
			# This is especially important for RLC-tracked entities where raw state changes
			# might be delayed or arrive separately from the RLC sensor update
			entity_state["last_effective_state"] = target_state
			
			_LOGGER.debug("Calling service %s.%s for entity %s", entity_state["domain"], service, entity_id)
			await self.hass.services.async_call(
				entity_state["domain"],
				service,
				{"entity_id": entity_id},
				blocking=True,
				context=context,
			)
			_LOGGER.debug("Service call completed for %s", entity_id)
		except Exception as err:  # pragma: no cover - log unexpected HA errors
			_LOGGER.exception("Failed to call service %s.%s for %s: %s", 
							 entity_state.get("domain"), config.get(service_key), 
							 config.get(CONF_ENTITY_ID), err)

	def _should_follow_presence(self, entity_state: dict) -> bool:
		"""Check if automation should apply to this entity.
		
		Returns True if presence automation should affect this entity.
		Requires both:
		- presence_allowed: User-controlled toggle (persisted across reboots)
		- NOT automation_paused: Transient pause due to manual control
		
		The presence_allowed flag is controlled by the user via the switch.
		The automation_paused flag is controlled automatically based on
		manual_disable_states configuration.
		"""
		return entity_state["presence_allowed"] and not entity_state.get("automation_paused", False)

	def _is_context_ours(self, entity_id: str, context: Context | None) -> bool:
		if not context:
			return False
		context_ids = self._entity_states[entity_id]["contexts"]
		return context.id in context_ids or (context.parent_id in context_ids if context.parent_id else False)

	def _is_any_occupied(self) -> bool:
		"""Check if any presence sensor is occupied (on).
		
		Uses is_entity_on() helper which handles real_last_changed sensors
		by reading their previous_valid_state attribute.
		"""
		sensors = getattr(self, '_presence_sensors', None)
		if sensors:
			return any(is_entity_on(self.hass, sensor) for sensor in sensors)
		# Fallback if not yet initialized
		sensors = self.entry.data.get(CONF_PRESENCE_SENSORS, [])
		return any(is_entity_on(self.hass, sensor) for sensor in sensors)

	def _are_clearing_sensors_clear(self) -> bool:
		"""Check if all clearing sensors report off (unoccupied).
		
		Uses is_entity_off() helper which handles real_last_changed sensors
		by reading their previous_valid_state attribute.
		"""
		clearing = getattr(self, '_clearing_sensors', None)
		if clearing:
			result = all(is_entity_off(self.hass, sensor) for sensor in clearing)
			if not result:
				# Log which sensors are not clear for debugging
				for sensor in clearing:
					effective = get_effective_state(self.hass, sensor)
					if effective != "off":
						_LOGGER.debug("Clearing sensor %s not clear: effective_state=%s", sensor, effective)
			return result
		
		# Fallback: check if clearing sensors are configured
		clearing = self.entry.data.get(CONF_CLEARING_SENSORS, [])
		if clearing:
			result = all(is_entity_off(self.hass, sensor) for sensor in clearing)
			if not result:
				for sensor in clearing:
					effective = get_effective_state(self.hass, sensor)
					if effective != "off":
						_LOGGER.debug("Clearing sensor %s not clear: effective_state=%s", sensor, effective)
			return result
		
		# No clearing sensors configured - fall back to presence sensors
		presence = getattr(self, '_presence_sensors', None)
		if presence:
			result = all(is_entity_off(self.hass, sensor) for sensor in presence)
			if not result:
				for sensor in presence:
					effective = get_effective_state(self.hass, sensor)
					if effective != "off":
						_LOGGER.debug("Presence sensor (as clearing) %s not clear: effective_state=%s", sensor, effective)
			return result
		
		# Last fallback to original presence sensors
		presence = self.entry.data.get(CONF_PRESENCE_SENSORS, [])
		return all(is_entity_off(self.hass, sensor) for sensor in presence)

	async def _start_off_timer(self) -> None:
		"""Start per-entity off timers when presence clears."""
		# Start individual timers for each entity
		for entity_state in self._entity_states.values():
			if entity_state["off_timer"]:
				entity_state["off_timer"].cancel()
			
			if self._should_follow_presence(entity_state):
				config = entity_state["config"]
				delay = config.get(CONF_ENTITY_OFF_DELAY)
				if delay is None:
					delay = self.entry.data.get(CONF_OFF_DELAY, DEFAULT_OFF_DELAY)
				
				entity_state["off_timer"] = asyncio.create_task(
					self._execute_entity_off_timer(entity_state, delay)
				)

	async def _execute_entity_off_timer(self, entity_state: dict, delay: int) -> None:
		"""Execute the off timer for a specific entity.
		
		When timer fires, checks if all clearing sensors are in cleared state.
		This handles scenarios where:
		- Normal flow: clearing sensor transitioned off -> timer fires -> turn off
		- Primer flow: presence sensor triggered but clearing sensor was never on
		              -> timer fires -> clearing sensors already cleared -> turn off
		"""
		entity_id = entity_state["config"].get(CONF_ENTITY_ID, "unknown")
		try:
			_LOGGER.debug("Starting off timer for %s with delay %d seconds", entity_id, delay)
			await asyncio.sleep(delay)
			# Check if all clearing sensors are cleared (not just presence sensors)
			# This allows the timer to work even if clearing sensors were never triggered
			if self._are_clearing_sensors_clear():
				_LOGGER.debug("Off timer expired for %s, clearing sensors clear, applying cleared action", entity_id)
				await self._apply_action_to_entity(entity_state, CONF_PRESENCE_CLEARED_SERVICE)
			else:
				_LOGGER.debug("Off timer expired for %s, but clearing sensors not all clear", entity_id)
		except asyncio.CancelledError:
			_LOGGER.debug("Off timer cancelled for %s", entity_id)
		except Exception as err:
			_LOGGER.exception("Error in off timer for %s: %s", entity_id, err)
		finally:
			entity_state["off_timer"] = None
