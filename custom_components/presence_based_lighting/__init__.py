"""Presence Based Lighting integration entry point."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Callable, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
	EVENT_CALL_SERVICE,
	EVENT_STATE_CHANGED,
	STATE_OFF,
	STATE_ON,
)
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
	CONF_CLEARING_SENSORS,
	CONF_CONTROLLED_ENTITIES,
	CONF_DISABLE_ON_EXTERNAL_CONTROL,
	CONF_ENTITY_ID,
	CONF_ENTITY_OFF_DELAY,
	CONF_INITIAL_PRESENCE_ALLOWED,
	CONF_OFF_DELAY,
	CONF_PRESENCE_CLEARED_SERVICE,
	CONF_PRESENCE_CLEARED_STATE,
	CONF_PRESENCE_DETECTED_SERVICE,
	CONF_PRESENCE_DETECTED_STATE,
	CONF_PRESENCE_SENSORS,
	CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
	CONF_REQUIRE_VACANCY_FOR_CLEARED,
	CONF_RESPECTS_PRESENCE_ALLOWED,
	CONF_ROOM_NAME,
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

_LOGGER = logging.getLogger(__package__)

# Add file handler for persistent logging across crashes
_log_file_handler = None
_file_logging_setup = False

async def _setup_file_logging(hass: HomeAssistant):
	"""Set up file logging that persists across crashes. Only sets up once globally."""
	global _log_file_handler, _file_logging_setup
	
	# Use a flag to ensure we only setup once, even if multiple entries are being set up simultaneously
	if _file_logging_setup:
		return
	
	_file_logging_setup = True
	
	if _log_file_handler is None:
		try:
			log_path = hass.config.path("presence_based_lighting_debug.log")
			
			# Create FileHandler in executor to avoid blocking I/O
			_log_file_handler = await hass.async_add_executor_job(
				logging.FileHandler, log_path, 'a'
			)
			_log_file_handler.setLevel(logging.DEBUG)
			formatter = logging.Formatter(
				'%(asctime)s - %(name)s - %(levelname)s - %(message)s'
			)
			_log_file_handler.setFormatter(formatter)
			_LOGGER.addHandler(_log_file_handler)
			_LOGGER.info("File logging enabled at: %s", log_path)
		except Exception as err:
			_LOGGER.error("Failed to set up file logging: %s", err)


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
	"""YAML setup is not supported."""
	return True



async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Set up Presence Based Lighting via the UI."""
	
	try:
		_LOGGER.info("Setting up Presence Based Lighting entry: %s", entry.entry_id)
		
		# Enable persistent file logging
		await _setup_file_logging(hass)

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
		self._pending_off_task: asyncio.Task | None = None
		self._entity_states: Dict[str, dict] = {}

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
					"callbacks": set(),
					"contexts": deque(maxlen=20),
					"off_timer": None,
				}
			
			_LOGGER.info("Coordinator initialized with %d unique entities", len(self._entity_states))
		except Exception as err:
			_LOGGER.exception("Error initializing PresenceBasedLightingCoordinator: %s", err)
			raise

	async def async_start(self) -> None:
		"""Begin tracking sensors and controlled entities."""
		
		try:
			_LOGGER.debug("Starting coordinator for entry: %s", self.entry.entry_id)
			
			controlled_ids = list(self._entity_states.keys())
			presence_sensors = self.entry.data.get(CONF_PRESENCE_SENSORS, [])
			clearing_sensors = self.entry.data.get(CONF_CLEARING_SENSORS, [])
			# Combine both sensor lists for state change tracking (deduplicated)
			all_sensors = list(set(presence_sensors + clearing_sensors))
			
			_LOGGER.debug("Setting up listeners for %d controlled entities: %s", 
						 len(controlled_ids), controlled_ids)
			_LOGGER.debug("Setting up listeners for %d presence sensors: %s", 
						 len(presence_sensors), presence_sensors)
			_LOGGER.debug("Setting up listeners for %d clearing sensors: %s", 
						 len(clearing_sensors), clearing_sensors)

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

	@callback
	def async_stop(self) -> None:
		"""Stop tracking events."""
		
		try:
			_LOGGER.debug("Stopping coordinator for entry: %s", self.entry.entry_id)

			if self._pending_off_task:
				self._pending_off_task.cancel()
				self._pending_off_task = None
				_LOGGER.debug("Cancelled pending off task")

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
		entity_state = self._entity_states[entity_id]
		if entity_state["presence_allowed"] == allowed:
			return

		entity_state["presence_allowed"] = allowed
		self._notify_switch(entity_id)

		if allowed and self._is_any_occupied():
			await self._apply_action_to_entity(entity_state, CONF_PRESENCE_DETECTED_SERVICE)

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
				if entity_id in self._entity_states:
					expanded_entities.append(entity_id)
				else:
					# Check if this might be a group - expand its members
					state = self.hass.states.get(entity_id)
					if state and state.attributes.get("entity_id"):
						group_members = state.attributes.get("entity_id", [])
						for member in group_members:
							if member in self._entity_states:
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

			# Check presence lock first - this takes priority
			if await self._check_and_apply_presence_lock(entity_state, new_state.state):
				return  # Presence lock handled the state change

			if not cfg[CONF_DISABLE_ON_EXTERNAL_CONTROL]:
				return

			if new_state.state == cfg[CONF_PRESENCE_CLEARED_STATE]:
				await self.async_set_presence_allowed(entity_id, False)
			elif new_state.state == cfg[CONF_PRESENCE_DETECTED_STATE]:
				await self.async_set_presence_allowed(entity_id, True)
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

		if service == cfg[CONF_PRESENCE_CLEARED_SERVICE]:
			await self.async_set_presence_allowed(entity_id, False)
		elif service == cfg[CONF_PRESENCE_DETECTED_SERVICE]:
			await self.async_set_presence_allowed(entity_id, True)
			if not self._is_any_occupied():
				await self._start_off_timer()

	async def _check_and_apply_presence_lock(self, entity_state: dict, new_state: str) -> bool:
		"""Check presence lock conditions and revert state if needed.
		
		Returns True if a presence lock was triggered and the state was reverted.
		"""
		cfg = entity_state["config"]
		entity_id = cfg[CONF_ENTITY_ID]
		
		require_occ = cfg.get(CONF_REQUIRE_OCCUPANCY_FOR_DETECTED, DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED)
		require_vac = cfg.get(CONF_REQUIRE_VACANCY_FOR_CLEARED, DEFAULT_REQUIRE_VACANCY_FOR_CLEARED)
		
		# If entity is being turned ON (detected state) but room is empty and lock is enabled
		if new_state == cfg[CONF_PRESENCE_DETECTED_STATE] and require_occ and not self._is_any_occupied():
			_LOGGER.debug("Presence lock: reverting %s to cleared state (room is empty)", entity_id)
			# Force the reversion without checking current state (since the triggering action may still be in progress)
			await self._force_apply_action(entity_state, CONF_PRESENCE_CLEARED_SERVICE)
			return True
		
		# If entity is being turned OFF (cleared state) but room is occupied and lock is enabled
		if new_state == cfg[CONF_PRESENCE_CLEARED_STATE] and require_vac and self._is_any_occupied():
			_LOGGER.debug("Presence lock: reverting %s to detected state (room is occupied)", entity_id)
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
		try:
			entity_id = event.data.get("entity_id")
			new_state = event.data.get("new_state")
			old_state = event.data.get("old_state")
			if not new_state or not old_state or new_state.state == old_state.state:
				return

			_LOGGER.debug("Presence change detected on %s: %s -> %s", entity_id, old_state.state, new_state.state)

			presence_sensors = self.entry.data.get(CONF_PRESENCE_SENSORS, [])
			clearing_sensors = self.entry.data.get(CONF_CLEARING_SENSORS, [])
			
			# Trigger detected action if a presence sensor turns on
			if new_state.state == STATE_ON and entity_id in presence_sensors:
				_LOGGER.debug("Presence detected, cancelling timers")
				if self._pending_off_task:
					self._pending_off_task.cancel()
					self._pending_off_task = None
				# Cancel all per-entity timers when presence detected
				for entity_state in self._entity_states.values():
					if entity_state["off_timer"]:
						entity_state["off_timer"].cancel()
						entity_state["off_timer"] = None
				await self._apply_presence_action(CONF_PRESENCE_DETECTED_SERVICE)
			# Trigger cleared action if a clearing sensor turns off AND all clearing sensors are clear
			elif new_state.state == STATE_OFF and self._are_clearing_sensors_clear():
				_LOGGER.debug("Presence cleared, starting off timer")
				await self._start_off_timer()
		except Exception as err:
			_LOGGER.exception("Error handling presence change: %s", err)

	async def _apply_presence_action(self, service_key: str) -> None:
		for entity_state in self._entity_states.values():
			if not self._should_follow_presence(entity_state):
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
			if current_state and current_state.state == target_state:
				_LOGGER.debug("Entity %s already in target state %s", entity_id, target_state)
				return

			context = Context()
			entity_state["contexts"].append(context.id)
			
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
		The presence_allowed flag always controls whether automation applies,
		regardless of whether RESPECTS_PRESENCE_ALLOWED is set (which only
		controls visibility of the switch in the UI).
		"""
		return entity_state["presence_allowed"]

	def _is_context_ours(self, entity_id: str, context: Context | None) -> bool:
		if not context:
			return False
		context_ids = self._entity_states[entity_id]["contexts"]
		return context.id in context_ids or (context.parent_id in context_ids if context.parent_id else False)

	def _is_any_occupied(self) -> bool:
		sensors = self.entry.data.get(CONF_PRESENCE_SENSORS, [])
		return any(self.hass.states.is_state(sensor, STATE_ON) for sensor in sensors)

	def _are_clearing_sensors_clear(self) -> bool:
		"""Check if all clearing sensors report off (unoccupied)."""
		clearing = self.entry.data.get(CONF_CLEARING_SENSORS, [])
		if not clearing:
			# Fall back to presence sensors if no clearing sensors configured
			clearing = self.entry.data.get(CONF_PRESENCE_SENSORS, [])
		return all(self.hass.states.is_state(sensor, STATE_OFF) for sensor in clearing)

	async def _start_off_timer(self) -> None:
		"""Start per-entity off timers when presence clears."""
		if self._pending_off_task:
			self._pending_off_task.cancel()
			self._pending_off_task = None

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
		"""Execute the off timer for a specific entity."""
		entity_id = entity_state["config"].get(CONF_ENTITY_ID, "unknown")
		try:
			_LOGGER.debug("Starting off timer for %s with delay %d seconds", entity_id, delay)
			await asyncio.sleep(delay)
			if not self._is_any_occupied():
				_LOGGER.debug("Off timer expired for %s, applying cleared action", entity_id)
				await self._apply_action_to_entity(entity_state, CONF_PRESENCE_CLEARED_SERVICE)
			else:
				_LOGGER.debug("Off timer expired for %s, but room is occupied", entity_id)
		except asyncio.CancelledError:
			_LOGGER.debug("Off timer cancelled for %s", entity_id)
		except Exception as err:
			_LOGGER.exception("Error in off timer for %s: %s", entity_id, err)
		finally:
			entity_state["off_timer"] = None
