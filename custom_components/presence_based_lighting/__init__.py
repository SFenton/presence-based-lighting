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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import async_get_last_state

from .const import (
	CONF_CONTROLLED_ENTITIES,
	CONF_DISABLE_ON_EXTERNAL_CONTROL,
	CONF_ENTITY_ID,
	CONF_ENTITY_OFF_DELAY,
	CONF_INITIAL_PRESENCE_ALLOWED,
	CONF_LIGHT_ENTITIES,
	CONF_OFF_DELAY,
	CONF_PRESENCE_CLEARED_SERVICE,
	CONF_PRESENCE_CLEARED_STATE,
	CONF_PRESENCE_DETECTED_SERVICE,
	CONF_PRESENCE_DETECTED_STATE,
	CONF_PRESENCE_SENSORS,
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
	DEFAULT_RESPECTS_PRESENCE_ALLOWED,
	DOMAIN,
	PLATFORMS,
	STARTUP_MESSAGE,
)

_LOGGER = logging.getLogger(__package__)


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
	"""YAML setup is not supported."""
	return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Handle config entry migrations."""

	if entry.version >= 2:
		return True

	_LOGGER.debug("Migrating Presence Based Lighting entry %s from v%s", entry.entry_id, entry.version)

	last_state = None
	entity_registry = er.async_get(hass)
	old_unique_id = f"{entry.entry_id}_switch"
	old_entity_id = entity_registry.async_get_entity_id("switch", DOMAIN, old_unique_id)
	if old_entity_id:
		last_state = await async_get_last_state(hass, old_entity_id)
		entity_registry.async_remove(old_entity_id)

	initial_allowed = True
	if last_state is not None:
		initial_allowed = last_state.state == STATE_ON

	controlled_entities = []
	for entity_id in entry.data.get(CONF_LIGHT_ENTITIES, []):
		controlled_entities.append(
			{
				CONF_ENTITY_ID: entity_id,
				CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
				CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
				CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
				CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
				CONF_RESPECTS_PRESENCE_ALLOWED: DEFAULT_RESPECTS_PRESENCE_ALLOWED,
				CONF_DISABLE_ON_EXTERNAL_CONTROL: DEFAULT_DISABLE_ON_EXTERNAL,
				CONF_INITIAL_PRESENCE_ALLOWED: initial_allowed,
			}
		)

	new_data = {
		CONF_ROOM_NAME: entry.data[CONF_ROOM_NAME],
		CONF_PRESENCE_SENSORS: entry.data.get(CONF_PRESENCE_SENSORS, []),
		CONF_OFF_DELAY: entry.data.get(CONF_OFF_DELAY, DEFAULT_OFF_DELAY),
		CONF_CONTROLLED_ENTITIES: controlled_entities,
	}

	hass.config_entries.async_update_entry(entry, data=new_data, version=2)
	_LOGGER.info("Migration of Presence Based Lighting entry %s to v2 complete", entry.entry_id)
	return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Set up Presence Based Lighting via the UI."""

	if DOMAIN not in hass.data:
		hass.data[DOMAIN] = {}
		_LOGGER.info(STARTUP_MESSAGE)

	coordinator = PresenceBasedLightingCoordinator(hass, entry)
	hass.data[DOMAIN][entry.entry_id] = coordinator

	await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
	await coordinator.async_start()

	entry.async_on_unload(entry.add_update_listener(async_reload_entry))
	return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Unload a config entry."""

	coordinator: PresenceBasedLightingCoordinator = hass.data[DOMAIN][entry.entry_id]
	coordinator.async_stop()

	unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
	if unload_ok:
		hass.data[DOMAIN].pop(entry.entry_id)

	return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
	"""Reload an existing config entry."""

	await async_unload_entry(hass, entry)
	await async_setup_entry(hass, entry)


class PresenceBasedLightingCoordinator:
	"""Coordinator managing per-entity presence automation."""

	def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
		self.hass = hass
		self.entry = entry
		self._listeners: list[Callable[[], None]] = []
		self._pending_off_task: asyncio.Task | None = None
		self._entity_states: Dict[str, dict] = {}

		for entity in entry.data.get(CONF_CONTROLLED_ENTITIES, []):
			entity_id = entity[CONF_ENTITY_ID]
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

	async def async_start(self) -> None:
		"""Begin tracking sensors and controlled entities."""

		controlled_ids = list(self._entity_states.keys())
		presence_sensors = self.entry.data.get(CONF_PRESENCE_SENSORS, [])

		if controlled_ids:
			self._listeners.append(
				async_track_state_change_event(
					self.hass,
					controlled_ids,
					self._handle_controlled_entity_change,
				)
			)

		if presence_sensors:
			self._listeners.append(
				async_track_state_change_event(
					self.hass,
					presence_sensors,
					self._handle_presence_change,
				)
			)

		self._listeners.append(
			self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._handle_service_call)
		)

	@callback
	def async_stop(self) -> None:
		"""Stop tracking events."""

		if self._pending_off_task:
			self._pending_off_task.cancel()
			self._pending_off_task = None

		# Cancel all per-entity timers
		for entity_state in self._entity_states.values():
			if entity_state["off_timer"]:
				entity_state["off_timer"].cancel()
				entity_state["off_timer"] = None

		for remove in self._listeners:
			remove()
		self._listeners.clear()

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
		service_data = event.data.get("service_data") or {}
		target = service_data.get("entity_id")
		if not target:
			return

		target_entities = target if isinstance(target, list) else [target]
		service = event.data.get("service")

		for entity_id in target_entities:
			if entity_id not in self._entity_states:
				continue
			if self._is_context_ours(entity_id, event.context):
				continue
			await self._handle_external_action(entity_id, service)

	async def _handle_controlled_entity_change(self, event: Event) -> None:
		entity_id = event.data.get("entity_id")
		if not entity_id or entity_id not in self._entity_states:
			return

		new_state = event.data.get("new_state")
		old_state = event.data.get("old_state")
		if not new_state or not old_state or new_state.state == old_state.state:
			return

		if self._is_context_ours(entity_id, new_state.context):
			return

		cfg = self._entity_states[entity_id]["config"]
		if not cfg[CONF_DISABLE_ON_EXTERNAL_CONTROL]:
			return

		if new_state.state == cfg[CONF_PRESENCE_CLEARED_STATE]:
			await self.async_set_presence_allowed(entity_id, False)
		elif new_state.state == cfg[CONF_PRESENCE_DETECTED_STATE]:
			await self.async_set_presence_allowed(entity_id, True)
			if not self._is_any_occupied():
				await self._start_off_timer()

	async def _handle_external_action(self, entity_id: str, service: str | None) -> None:
		cfg = self._entity_states[entity_id]["config"]
		if not cfg[CONF_DISABLE_ON_EXTERNAL_CONTROL]:
			return

		if service == cfg[CONF_PRESENCE_CLEARED_SERVICE]:
			await self.async_set_presence_allowed(entity_id, False)
		elif service == cfg[CONF_PRESENCE_DETECTED_SERVICE]:
			await self.async_set_presence_allowed(entity_id, True)
			if not self._is_any_occupied():
				await self._start_off_timer()

	async def _handle_presence_change(self, event: Event) -> None:
		new_state = event.data.get("new_state")
		old_state = event.data.get("old_state")
		if not new_state or not old_state or new_state.state == old_state.state:
			return

		if new_state.state == STATE_ON:
			if self._pending_off_task:
				self._pending_off_task.cancel()
				self._pending_off_task = None
			# Cancel all per-entity timers when presence detected
			for entity_state in self._entity_states.values():
				if entity_state["off_timer"]:
					entity_state["off_timer"].cancel()
					entity_state["off_timer"] = None
			await self._apply_presence_action(CONF_PRESENCE_DETECTED_SERVICE)
		elif new_state.state == STATE_OFF and not self._is_any_occupied():
			await self._start_off_timer()

	async def _apply_presence_action(self, service_key: str) -> None:
		for entity_state in self._entity_states.values():
			if not self._should_follow_presence(entity_state):
				continue
			await self._apply_action_to_entity(entity_state, service_key)

	async def _apply_action_to_entity(self, entity_state: dict, service_key: str) -> None:
		config = entity_state["config"]
		entity_id = config[CONF_ENTITY_ID]
		service = config[service_key]
		
		# Skip if service is set to NO_ACTION
		if service == NO_ACTION:
			return
		
		target_state_key = (
			CONF_PRESENCE_DETECTED_STATE
			if service_key == CONF_PRESENCE_DETECTED_SERVICE
			else CONF_PRESENCE_CLEARED_STATE
		)
		target_state = config[target_state_key]
		current_state = self.hass.states.get(entity_id)
		if current_state and current_state.state == target_state:
			return

		context = Context()
		entity_state["contexts"].append(context.id)
		try:
			await self.hass.services.async_call(
				entity_state["domain"],
				service,
				{"entity_id": entity_id},
				blocking=True,
				context=context,
			)
		except Exception as err:  # pragma: no cover - log unexpected HA errors
			_LOGGER.error("Failed to call service %s.%s for %s: %s", entity_state["domain"], service, entity_id, err)

	def _should_follow_presence(self, entity_state: dict) -> bool:
		config = entity_state["config"]
		if not config[CONF_RESPECTS_PRESENCE_ALLOWED]:
			return True
		return entity_state["presence_allowed"]

	def _is_context_ours(self, entity_id: str, context: Context | None) -> bool:
		if not context:
			return False
		context_ids = self._entity_states[entity_id]["contexts"]
		return context.id in context_ids or (context.parent_id in context_ids if context.parent_id else False)

	def _is_any_occupied(self) -> bool:
		sensors = self.entry.data.get(CONF_PRESENCE_SENSORS, [])
		return any(self.hass.states.is_state(sensor, STATE_ON) for sensor in sensors)

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
		try:
			await asyncio.sleep(delay)
			if not self._is_any_occupied():
				await self._apply_action_to_entity(entity_state, CONF_PRESENCE_CLEARED_SERVICE)
		except asyncio.CancelledError:
			_LOGGER.debug("Presence Based Lighting off timer cancelled for %s", entity_state["config"][CONF_ENTITY_ID])
		finally:
			entity_state["off_timer"] = None
