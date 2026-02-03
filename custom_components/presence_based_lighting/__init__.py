"""Presence Based Lighting integration entry point."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import time, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
	EVENT_CALL_SERVICE,
	EVENT_STATE_CHANGED,
	STATE_OFF,
	STATE_ON,
)
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
	async_track_state_change_event,
	async_track_time_change,
	async_track_time_interval,
)
from homeassistant.util import dt as dt_util
import homeassistant.helpers.config_validation as cv

from .const import (
	AUTOMATION_MODE_AUTOMATIC,
	AUTOMATION_MODE_PRESENCE_LOCK,
	CONF_ACTIVATION_CONDITIONS,
	CONF_AUTOMATION_MODE,
	CONF_AUTO_REENABLE_END_TIME,
	CONF_AUTO_REENABLE_PRESENCE_SENSORS,
	CONF_AUTO_REENABLE_START_TIME,
	CONF_AUTO_REENABLE_VACANCY_THRESHOLD,
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
	DEFAULT_AUTO_REENABLE_END_TIME,
	DEFAULT_AUTO_REENABLE_START_TIME,
	DEFAULT_AUTO_REENABLE_VACANCY_THRESHOLD,
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
	CONF_FILE_LOGGING_ENABLED,
	DEFAULT_FILE_LOGGING_ENABLED,
	FILE_LOG_NAME,
	FILE_LOG_MAX_LINES,
	DOMAIN,
	PLATFORMS,
	STARTUP_MESSAGE,
)
from .interceptor import PresenceLockInterceptor, is_interceptor_available
from .real_last_changed import get_effective_state, is_entity_on, is_entity_off, is_real_last_changed_entity

_LOGGER = logging.getLogger(__package__)

_FILE_LOGGING_STATE_KEY = "_file_logging"


class _AcceptAllFilter(logging.Filter):
	"""A filter that accepts all log records regardless of level."""

	def filter(self, record: logging.LogRecord) -> bool:
		return True


class _ComponentNameFilter(logging.Filter):
	"""A filter that only accepts records from our component namespace."""

	def __init__(self, component_name: str):
		super().__init__()
		self._prefix = component_name

	def filter(self, record: logging.LogRecord) -> bool:
		# Accept records from our component and all its submodules
		return record.name.startswith(self._prefix)


class _LineCappedFileHandler(logging.FileHandler):
	"""A FileHandler that periodically trims itself to the last N lines.
	
	This handler also forces the parent logger to DEBUG level on every emit
	to work around Home Assistant resetting logger levels.
	"""

	def __init__(
		self,
		*args,
		hass: HomeAssistant,
		max_lines: int,
		trim_every_writes: int = 200,
		logger_name: str = "",
		**kwargs,
	):
		super().__init__(*args, **kwargs)
		self._hass = hass
		self._max_lines = max_lines
		self._trim_every_writes = max(1, trim_every_writes)
		self._writes_since_trim = 0
		self._trim_pending = False
		self._logger_name = logger_name

	def emit(self, record) -> None:
		# Force the target logger to DEBUG level every time we emit.
		# This works around HA resetting the logger level after we configure it.
		if self._logger_name:
			target_logger = logging.getLogger(self._logger_name)
			if target_logger.level > logging.DEBUG:
				target_logger.setLevel(logging.DEBUG)
		super().emit(record)
		self._writes_since_trim += 1
		if self._trim_pending or self._writes_since_trim < self._trim_every_writes:
			return
		self._writes_since_trim = 0
		self._trim_pending = True
		loop = getattr(self._hass, "loop", None)
		if loop is None:
			self._trim_pending = False
			return

		def _schedule() -> None:
			async def _do_trim() -> None:
				try:
					await _trim_log_file(self._hass, self.baseFilename, self._max_lines)
				finally:
					self._trim_pending = False

			create_task = getattr(self._hass, "async_create_task", None)
			if callable(create_task):
				create_task(_do_trim())
			else:
				# Fallback for HA internals; safest no-op if unavailable.
				self._trim_pending = False

		try:
			loop.call_soon_threadsafe(_schedule)
		except Exception:
			self._trim_pending = False


def _get_file_logging_state(hass: HomeAssistant) -> dict:
	"""Return (and initialize) shared file-logging state stored in hass.data."""
	if DOMAIN not in hass.data:
		hass.data[DOMAIN] = {}
	state = hass.data[DOMAIN].get(_FILE_LOGGING_STATE_KEY)
	if isinstance(state, dict):
		return state
	state = {
		"handler": None,
		"unsub_trim": None,
		"enabled_entries": set(),
		"log_path": None,
		"prev_logger_level": None,
	}
	hass.data[DOMAIN][_FILE_LOGGING_STATE_KEY] = state
	return state


async def _trim_log_file(hass: HomeAssistant, log_path: str, max_lines: int) -> None:
	"""Trim the log file to the last max_lines (runs in executor)."""
	path = Path(log_path)
	if not path.exists():
		return

	def _trim() -> None:  # pragma: no cover - executor I/O
		from collections import deque
		try:
			with path.open("r", encoding="utf-8", errors="replace") as file_handle:
				lines = deque(file_handle, maxlen=max_lines)
			with path.open("w", encoding="utf-8") as file_handle:
				file_handle.writelines(lines)
		except Exception as err:
			_LOGGER.debug("Failed trimming log file %s: %s", log_path, err)

	await hass.async_add_executor_job(_trim)


async def _ensure_file_logging_enabled(hass: HomeAssistant) -> None:
	"""Ensure the shared file handler exists and periodic trimming is active."""
	state = _get_file_logging_state(hass)
	logger = logging.getLogger("custom_components.presence_based_lighting")
	# Home Assistant commonly sets component loggers to WARNING by default.
	# If we don't force DEBUG here, the logger can filter everything before the
	# handler ever sees it, resulting in an empty log file.
	if state.get("prev_logger_level") is None:
		state["prev_logger_level"] = logger.level
	logger.setLevel(logging.DEBUG)

	if state.get("handler") is not None:
		return

	log_path = hass.config.path(FILE_LOG_NAME)

	def _create_handler() -> logging.FileHandler:  # pragma: no cover - executor I/O
		return _LineCappedFileHandler(
			log_path,
			mode="a",
			encoding="utf-8",
			hass=hass,
			max_lines=FILE_LOG_MAX_LINES,
			trim_every_writes=200,
		)

	handler = await hass.async_add_executor_job(_create_handler)
	handler.setLevel(logging.DEBUG)
	handler.setFormatter(
		logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
	)

	logger.addHandler(handler)
	# Emit a direct line to the handler so the file is never empty even if HA's
	# logger configuration filters out our log records.
	try:
		handler.emit(
			logging.LogRecord(
				name="custom_components.presence_based_lighting",
				level=logging.INFO,
				pathname=__file__,
				lineno=0,
				msg="File logging initialized (capped at %d lines)",
				args=(FILE_LOG_MAX_LINES,),
				exc_info=None,
			)
		)
		handler.flush()
	except Exception:
		pass

	state["handler"] = handler
	state["log_path"] = log_path

	await _trim_log_file(hass, log_path, FILE_LOG_MAX_LINES)

	async def _periodic_trim(_now) -> None:
		await _trim_log_file(hass, log_path, FILE_LOG_MAX_LINES)

	state["unsub_trim"] = async_track_time_interval(
		hass,
		lambda now: hass.async_create_task(_periodic_trim(now)),
		timedelta(seconds=30),
	)
	_LOGGER.info("File logging enabled at: %s (capped at %d lines)", log_path, FILE_LOG_MAX_LINES)


async def _disable_file_logging_if_unused(hass: HomeAssistant) -> None:
	"""Tear down the shared handler if no config entries still need it."""
	state = _get_file_logging_state(hass)
	enabled_entries = state.get("enabled_entries")
	if enabled_entries:
		return

	logger = logging.getLogger("custom_components.presence_based_lighting")

	unsub = state.get("unsub_trim")
	if callable(unsub):
		try:
			unsub()
		except Exception:
			pass
	state["unsub_trim"] = None

	handler = state.get("handler")
	if handler is not None:
		try:
			logger.removeHandler(handler)
		except Exception:
			pass
		await hass.async_add_executor_job(handler.close)
	state["handler"] = None
	state["log_path"] = None

	prev_level = state.get("prev_logger_level")
	if isinstance(prev_level, int):
		logger.setLevel(prev_level)
	state["prev_logger_level"] = None
	_LOGGER.info("File logging disabled")


async def _update_file_logging_for_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
	"""Apply file logging enable/disable for a single entry based on entry data."""
	state = _get_file_logging_state(hass)
	enabled_entries: set[str] = state["enabled_entries"]

	enabled = entry.data.get(CONF_FILE_LOGGING_ENABLED, DEFAULT_FILE_LOGGING_ENABLED)
	if enabled:
		enabled_entries.add(entry.entry_id)
		await _ensure_file_logging_enabled(hass)
		return

	if entry.entry_id in enabled_entries:
		enabled_entries.discard(entry.entry_id)
		await _disable_file_logging_if_unused(hass)


SERVICE_RESUME_AUTOMATION = "resume_automation"
SERVICE_PAUSE_AUTOMATION = "pause_automation"

# Allow entity_id to be a string or a list of strings
SERVICE_SCHEMA = vol.Schema({
	vol.Optional("entity_id"): vol.Any(cv.entity_id, [cv.entity_id]),
})


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
	"""Set up the Presence Based Lighting component."""

	async def handle_resume_automation(call):
		"""Handle the resume_automation service call."""
		target_entity_id = call.data.get("entity_id")
		
		# Get target switches from the service call (can be in target or data)
		target_switches = []
		if hasattr(call, "target") and call.target:
			target_switches = call.target.get("entity_id", [])
			if isinstance(target_switches, str):
				target_switches = [target_switches]
		
		# If no target, check if entity_id in data is the switch
		if not target_switches and target_entity_id:
			if isinstance(target_entity_id, list):
				target_switches = target_entity_id
			else:
				target_switches = [target_entity_id]
			target_entity_id = None  # Clear since we're using it as target
		
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
		
		# Get target switches from the service call (can be in target or data)
		target_switches = []
		if hasattr(call, "target") and call.target:
			target_switches = call.target.get("entity_id", [])
			if isinstance(target_switches, str):
				target_switches = [target_switches]
		
		# If no target, check if entity_id in data is the switch
		if not target_switches and target_entity_id:
			if isinstance(target_entity_id, list):
				target_switches = target_entity_id
			else:
				target_switches = [target_entity_id]
			target_entity_id = None  # Clear since we're using it as target
		
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
	Version 5 -> 6: Add activation_conditions for optional AND gate on light activation.
	Version 6 -> 7: Add auto_reenable_presence_sensors and auto_reenable_vacancy_threshold.
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

	if config_entry.version == 5:
		# Version 5 -> 6: Add activation_conditions (empty by default = existing behavior)
		new_data = {**config_entry.data}
		if CONF_ACTIVATION_CONDITIONS not in new_data:
			new_data[CONF_ACTIVATION_CONDITIONS] = []
		
		hass.config_entries.async_update_entry(
			config_entry, data=new_data, version=6
		)
		_LOGGER.info(
			"Migration of entry %s from version 5 to 6 successful",
			config_entry.entry_id,
		)

	if config_entry.version == 6:
		# Version 6 -> 7: Add auto_reenable_presence_sensors, vacancy_threshold, start/end times
		new_data = {**config_entry.data}
		if CONF_AUTO_REENABLE_PRESENCE_SENSORS not in new_data:
			new_data[CONF_AUTO_REENABLE_PRESENCE_SENSORS] = []
		if CONF_AUTO_REENABLE_VACANCY_THRESHOLD not in new_data:
			new_data[CONF_AUTO_REENABLE_VACANCY_THRESHOLD] = DEFAULT_AUTO_REENABLE_VACANCY_THRESHOLD
		if CONF_AUTO_REENABLE_START_TIME not in new_data:
			new_data[CONF_AUTO_REENABLE_START_TIME] = DEFAULT_AUTO_REENABLE_START_TIME
		if CONF_AUTO_REENABLE_END_TIME not in new_data:
			new_data[CONF_AUTO_REENABLE_END_TIME] = DEFAULT_AUTO_REENABLE_END_TIME
		
		hass.config_entries.async_update_entry(
			config_entry, data=new_data, version=7
		)
		_LOGGER.info(
			"Migration of entry %s from version 6 to 7 successful",
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

		# Always enable file logging (no per-entry configuration).
		await _ensure_file_logging_enabled(hass)

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
		
		# Auto re-enable feature state
		self._auto_reenable_enabled: bool = False
		self._auto_reenable_tracking: Dict[str, Any] = {
			"is_tracking": False,
			"window_start": None,  # datetime when tracking started
			"occupied_seconds": 0.0,  # total seconds occupied
			"last_presence_change": None,  # datetime of last presence state change
			"was_occupied": False,  # last known occupancy state
		}
		self._auto_reenable_end_time_unsub: Callable[[], None] | None = None
		self._auto_reenable_start_time_unsub: Callable[[], None] | None = None
		
		# Parse start/end times from config entry
		self._auto_reenable_start_time = self._parse_time_string(
			entry.data.get(CONF_AUTO_REENABLE_START_TIME, DEFAULT_AUTO_REENABLE_START_TIME)
		)
		self._auto_reenable_end_time = self._parse_time_string(
			entry.data.get(CONF_AUTO_REENABLE_END_TIME, DEFAULT_AUTO_REENABLE_END_TIME)
		)

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

	def _parse_time_string(self, time_str: str) -> time:
		"""Parse a time string like 'HH:MM:SS' or 'HH:MM' to a time object."""
		if not time_str:
			return time(0, 0, 0)
		parts = time_str.split(":")
		hour = int(parts[0]) if len(parts) > 0 else 0
		minute = int(parts[1]) if len(parts) > 1 else 0
		second = int(parts[2]) if len(parts) > 2 else 0
		return time(hour=hour, minute=minute, second=second)

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
			activation_conditions = self.entry.data.get(CONF_ACTIVATION_CONDITIONS, [])
			
			# Store presence and clearing sensors directly
			# RLC sensors are handled via their previous_valid_state attribute
			self._presence_sensors = set(presence_sensors)
			self._clearing_sensors = set(clearing_sensors) if clearing_sensors else set(presence_sensors)
			
			# Store activation conditions (optional AND gate for light activation)
			self._activation_conditions = set(activation_conditions)
			
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

			# Register listener for activation conditions (AND gate for light activation)
			if activation_conditions:
				self._listeners.append(
					async_track_state_change_event(
						self.hass,
						activation_conditions,
						self._handle_activation_condition_change,
					)
				)
				_LOGGER.debug("Registered state change listener for %d activation conditions: %s",
							 len(activation_conditions), activation_conditions)

			self._listeners.append(
				self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._handle_service_call)
			)
			_LOGGER.debug("Registered service call listener")
			
			# Register listener for auto-reenable presence sensors
			auto_reenable_sensors = self.entry.data.get(CONF_AUTO_REENABLE_PRESENCE_SENSORS, [])
			if auto_reenable_sensors:
				self._listeners.append(
					async_track_state_change_event(
						self.hass,
						auto_reenable_sensors,
						self._handle_auto_reenable_presence_change,
					)
				)
				_LOGGER.debug("Registered state change listener for %d auto-reenable sensors: %s",
							 len(auto_reenable_sensors), auto_reenable_sensors)
			
			# Check if we need to continue tracking after a restart
			await self._check_auto_reenable_startup()
			
			_LOGGER.info("Coordinator started successfully with %d listeners", len(self._listeners))
		except Exception as err:
			_LOGGER.exception("Error starting PresenceBasedLightingCoordinator: %s", err)
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

			# Cancel auto-reenable schedules
			self._cancel_auto_reenable_schedules()

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

	async def _handle_activation_condition_change(self, event: Event) -> None:
		"""Handle state changes on activation condition entities.
		
		When an activation condition becomes true while the room is occupied,
		trigger the detected action (turn on lights).
		
		This allows scenarios like:
		- Motion detected while it's still light outside (lux sensor off)
		- Sun sets (lux sensor turns on) while room is still occupied
		- Lights should turn on at that moment
		"""
		try:
			entity_id = event.data.get("entity_id")
			new_state = event.data.get("new_state")
			old_state = event.data.get("old_state")
			
			if not new_state or not old_state:
				return
			
			# Only care about transitions to ON
			if new_state.state != STATE_ON or old_state.state == STATE_ON:
				return
			
			_LOGGER.debug(
				"Activation condition %s changed: %s -> %s",
				entity_id, old_state.state, new_state.state
			)
			
			# Check if room is currently occupied AND all activation conditions are now met
			if self._is_any_occupied() and self._are_activation_conditions_met():
				_LOGGER.debug(
					"Activation conditions now met while room is occupied - triggering detected action"
				)
				# Cancel any pending off timers since we're activating
				for entity_state in self._entity_states.values():
					if entity_state["off_timer"]:
						entity_state["off_timer"].cancel()
						entity_state["off_timer"] = None
				
				await self._apply_presence_action(CONF_PRESENCE_DETECTED_SERVICE)
				
				# Start off-timer in case clearing sensors are already cleared
				await self._start_off_timer()
			else:
				if not self._is_any_occupied():
					_LOGGER.debug("Activation condition met but room not occupied")
				else:
					_LOGGER.debug("Activation condition changed but not all conditions met yet")
		except Exception as err:
			_LOGGER.exception("Error handling activation condition change: %s", err)

	async def _apply_presence_action(self, service_key: str) -> None:
		"""Apply presence action to all controlled entities.
		
		For DETECTED actions: only applies if activation conditions are met.
		For CLEARED actions: always applies (clear regardless of activation conditions).
		"""
		_LOGGER.debug("Applying presence action %s to %d entities: %s", 
					 service_key, len(self._entity_states), list(self._entity_states.keys()))
		
		# For detected (turn on) actions, check activation conditions
		if service_key == CONF_PRESENCE_DETECTED_SERVICE:
			if not self._are_activation_conditions_met():
				_LOGGER.debug(
					"Skipping detected action - activation conditions not met. "
					"Lights will turn on when conditions are satisfied while occupied."
				)
				return
		
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

	def _are_activation_conditions_met(self) -> bool:
		"""Check if all activation conditions are satisfied (AND gate).
		
		If no activation conditions are configured, returns True (always allow).
		If any activation condition is off/false, returns False.
		All conditions must be on/true for lights to activate.
		"""
		conditions = getattr(self, '_activation_conditions', None)
		if not conditions:
			# No conditions configured - always allow activation
			return True
		
		for condition in conditions:
			state = self.hass.states.get(condition)
			if not state or state.state != STATE_ON:
				_LOGGER.debug(
					"Activation condition %s not met: state=%s",
					condition, state.state if state else "unavailable"
				)
				return False
		
		return True

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
				
				task = asyncio.create_task(self._execute_entity_off_timer(entity_state, delay))
				entity_state["off_timer"] = task

	async def _execute_entity_off_timer(self, entity_state: dict, delay: int) -> None:
		"""Execute the off timer for a specific entity.
		
		When timer fires, checks if all clearing sensors are in cleared state.
		This handles scenarios where:
		- Normal flow: clearing sensor transitioned off -> timer fires -> turn off
		- Primer flow: presence sensor triggered but clearing sensor was never on
		              -> timer fires -> clearing sensors already cleared -> turn off
		"""
		entity_id = entity_state["config"].get(CONF_ENTITY_ID, "unknown")
		this_task = asyncio.current_task()
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
			# Avoid clobbering a newly started timer:
			# if this task was cancelled/replaced, a newer task may already be stored.
			if entity_state.get("off_timer") is this_task:
				entity_state["off_timer"] = None

	# =========================================================================
	# Auto Re-Enable Feature Methods
	# =========================================================================

	def _get_tracking_persistence_path(self) -> Path:
		"""Get the path to the tracking persistence file."""
		return Path(self.hass.config.path(".storage")) / f"pbl_tracking_{self.entry.entry_id}.json"

	async def _save_tracking_state(self) -> None:
		"""Persist tracking state to storage for restart recovery."""
		try:
			tracking = self._auto_reenable_tracking
			data = {
				"is_tracking": tracking["is_tracking"],
				"window_start": tracking["window_start"].isoformat() if tracking["window_start"] else None,
				"occupied_seconds": tracking["occupied_seconds"],
				"last_presence_change": tracking["last_presence_change"].isoformat() if tracking["last_presence_change"] else None,
				"was_occupied": tracking["was_occupied"],
				"saved_at": dt_util.utcnow().isoformat(),
			}
			
			path = self._get_tracking_persistence_path()
			await self.hass.async_add_executor_job(
				lambda: path.write_text(json.dumps(data))
			)
			_LOGGER.debug("Saved auto-reenable tracking state: %s", data)
		except Exception as err:
			_LOGGER.exception("Failed to save tracking state: %s", err)

	async def _load_tracking_state(self) -> bool:
		"""Load tracking state from storage. Returns True if state was loaded."""
		try:
			path = self._get_tracking_persistence_path()
			if not path.exists():
				return False
			
			data = await self.hass.async_add_executor_job(
				lambda: json.loads(path.read_text())
			)
			
			tracking = self._auto_reenable_tracking
			tracking["is_tracking"] = data.get("is_tracking", False)
			tracking["occupied_seconds"] = data.get("occupied_seconds", 0.0)
			tracking["was_occupied"] = data.get("was_occupied", False)
			
			if data.get("window_start"):
				tracking["window_start"] = datetime.fromisoformat(data["window_start"])
			if data.get("last_presence_change"):
				tracking["last_presence_change"] = datetime.fromisoformat(data["last_presence_change"])
			
			_LOGGER.debug("Loaded auto-reenable tracking state: %s", data)
			return tracking["is_tracking"]
		except Exception as err:
			_LOGGER.debug("No valid tracking state to load: %s", err)
			return False

	async def _clear_tracking_state(self) -> None:
		"""Clear persisted tracking state."""
		try:
			path = self._get_tracking_persistence_path()
			if path.exists():
				await self.hass.async_add_executor_job(path.unlink)
				_LOGGER.debug("Cleared persisted tracking state")
		except Exception as err:
			_LOGGER.debug("Failed to clear tracking state: %s", err)

	def set_auto_reenable_enabled(self, enabled: bool) -> None:
		"""Set whether auto re-enable is enabled for this room."""
		self._auto_reenable_enabled = enabled
		_LOGGER.debug("Auto re-enable %s for %s", 
					 "enabled" if enabled else "disabled",
					 self.entry.data.get(CONF_ROOM_NAME))
		
		if enabled:
			self._schedule_auto_reenable_times()
		else:
			self._cancel_auto_reenable_schedules()

	def get_auto_reenable_tracking_info(self) -> Dict[str, Any]:
		"""Get current tracking information for display in entity attributes."""
		tracking = self._auto_reenable_tracking
		threshold = self.entry.data.get(
			CONF_AUTO_REENABLE_VACANCY_THRESHOLD, 
			DEFAULT_AUTO_REENABLE_VACANCY_THRESHOLD
		)
		
		info = {
			"is_tracking": tracking["is_tracking"],
			"vacancy_threshold_percent": threshold,
			"start_time": str(self._auto_reenable_start_time) if self._auto_reenable_start_time else None,
			"end_time": str(self._auto_reenable_end_time) if self._auto_reenable_end_time else None,
		}
		
		if tracking["is_tracking"] and tracking["window_start"]:
			now = dt_util.utcnow()
			total_seconds = (now - tracking["window_start"]).total_seconds()
			
			# Add time from current presence state if occupied
			current_occupied_seconds = tracking["occupied_seconds"]
			if tracking["was_occupied"] and tracking["last_presence_change"]:
				current_occupied_seconds += (now - tracking["last_presence_change"]).total_seconds()
			
			if total_seconds > 0:
				vacancy_percent = 100.0 * (1 - current_occupied_seconds / total_seconds)
			else:
				vacancy_percent = 100.0
			
			info["tracking_started"] = tracking["window_start"].isoformat()
			info["total_tracking_seconds"] = round(total_seconds, 1)
			info["occupied_seconds"] = round(current_occupied_seconds, 1)
			info["current_vacancy_percent"] = round(vacancy_percent, 1)
			info["currently_occupied"] = tracking["was_occupied"]
		
		return info

	def _schedule_auto_reenable_times(self) -> None:
		"""Schedule callbacks for start and end times."""
		self._cancel_auto_reenable_schedules()
		
		if not self._auto_reenable_start_time or not self._auto_reenable_end_time:
			_LOGGER.debug("Cannot schedule auto-reenable: start or end time not set")
			return
		
		# Schedule start time callback
		self._auto_reenable_start_time_unsub = async_track_time_change(
			self.hass,
			self._handle_auto_reenable_start_time,
			hour=self._auto_reenable_start_time.hour,
			minute=self._auto_reenable_start_time.minute,
			second=self._auto_reenable_start_time.second,
		)
		
		# Schedule end time callback
		self._auto_reenable_end_time_unsub = async_track_time_change(
			self.hass,
			self._handle_auto_reenable_end_time,
			hour=self._auto_reenable_end_time.hour,
			minute=self._auto_reenable_end_time.minute,
			second=self._auto_reenable_end_time.second,
		)
		
		_LOGGER.debug(
			"Scheduled auto-reenable for %s: start=%s, end=%s",
			self.entry.data.get(CONF_ROOM_NAME),
			self._auto_reenable_start_time,
			self._auto_reenable_end_time
		)

	def _cancel_auto_reenable_schedules(self) -> None:
		"""Cancel scheduled auto-reenable callbacks."""
		if self._auto_reenable_start_time_unsub:
			self._auto_reenable_start_time_unsub()
			self._auto_reenable_start_time_unsub = None
		
		if self._auto_reenable_end_time_unsub:
			self._auto_reenable_end_time_unsub()
			self._auto_reenable_end_time_unsub = None

	async def _handle_auto_reenable_start_time(self, now: datetime) -> None:
		"""Called when the monitoring window starts."""
		if not self._auto_reenable_enabled:
			return
		
		room_name = self.entry.data.get(CONF_ROOM_NAME)
		_LOGGER.info("Auto re-enable monitoring started for %s at %s", room_name, now)
		
		# Initialize tracking state
		tracking = self._auto_reenable_tracking
		tracking["is_tracking"] = True
		tracking["window_start"] = dt_util.utcnow()
		tracking["occupied_seconds"] = 0.0
		tracking["last_presence_change"] = dt_util.utcnow()
		tracking["was_occupied"] = self._is_auto_reenable_sensors_occupied()
		
		await self._save_tracking_state()

	async def _handle_auto_reenable_end_time(self, now: datetime) -> None:
		"""Called when the monitoring window ends - evaluate and potentially re-enable."""
		if not self._auto_reenable_enabled:
			return
		
		await self._evaluate_and_apply_auto_reenable()

	async def _evaluate_and_apply_auto_reenable(self) -> None:
		"""Evaluate vacancy percentage and re-enable presence lighting if threshold met."""
		room_name = self.entry.data.get(CONF_ROOM_NAME)
		tracking = self._auto_reenable_tracking
		
		if not tracking["is_tracking"]:
			_LOGGER.debug("Auto re-enable evaluation skipped for %s: not tracking", room_name)
			return
		
		now = dt_util.utcnow()
		total_seconds = (now - tracking["window_start"]).total_seconds()
		
		# Finalize occupied seconds calculation
		occupied_seconds = tracking["occupied_seconds"]
		if tracking["was_occupied"] and tracking["last_presence_change"]:
			occupied_seconds += (now - tracking["last_presence_change"]).total_seconds()
		
		# Calculate vacancy percentage
		if total_seconds > 0:
			vacancy_percent = 100.0 * (1 - occupied_seconds / total_seconds)
		else:
			vacancy_percent = 100.0
		
		threshold = self.entry.data.get(
			CONF_AUTO_REENABLE_VACANCY_THRESHOLD,
			DEFAULT_AUTO_REENABLE_VACANCY_THRESHOLD
		)
		
		_LOGGER.info(
			"Auto re-enable evaluation for %s: vacancy=%.1f%%, threshold=%d%%, occupied=%.1fs/%.1fs",
			room_name, vacancy_percent, threshold, occupied_seconds, total_seconds
		)
		
		# Reset tracking state
		tracking["is_tracking"] = False
		tracking["window_start"] = None
		tracking["occupied_seconds"] = 0.0
		tracking["last_presence_change"] = None
		tracking["was_occupied"] = False
		await self._clear_tracking_state()
		
		# Check if we should re-enable
		if vacancy_percent >= threshold:
			_LOGGER.info(
				"Auto re-enable triggered for %s: room was empty %.1f%% of time (>= %d%% threshold)",
				room_name, vacancy_percent, threshold
			)
			await self._reenable_presence_lighting()
		else:
			_LOGGER.info(
				"Auto re-enable NOT triggered for %s: room was empty only %.1f%% of time (< %d%% threshold)",
				room_name, vacancy_percent, threshold
			)

	async def _reenable_presence_lighting(self) -> None:
		"""Re-enable presence-based lighting for all entities in this room."""
		room_name = self.entry.data.get(CONF_ROOM_NAME)
		
		for entity_id, entity_state in self._entity_states.items():
			if not entity_state["presence_allowed"]:
				_LOGGER.info("Re-enabling presence lighting for %s in %s", entity_id, room_name)
				await self.async_set_presence_allowed(entity_id, True)
			
			# Also resume automation if paused
			if entity_state.get("automation_paused", False):
				_LOGGER.info("Resuming automation for %s in %s", entity_id, room_name)
				self.set_automation_paused(entity_id, False)

	def _is_auto_reenable_sensors_occupied(self) -> bool:
		"""Check if any auto-reenable presence sensor is occupied."""
		sensors = self.entry.data.get(CONF_AUTO_REENABLE_PRESENCE_SENSORS, [])
		if not sensors:
			# Fall back to main presence sensors if none configured
			sensors = self.entry.data.get(CONF_PRESENCE_SENSORS, [])
		
		return any(is_entity_on(self.hass, sensor) for sensor in sensors)

	async def _handle_auto_reenable_presence_change(self, event: Event) -> None:
		"""Track presence changes during the monitoring window."""
		tracking = self._auto_reenable_tracking
		if not tracking["is_tracking"]:
			return
		
		entity_id = event.data.get("entity_id")
		new_state = event.data.get("new_state")
		old_state = event.data.get("old_state")
		
		if not new_state or not old_state:
			return
		
		# Determine if currently occupied (any sensor on)
		is_now_occupied = self._is_auto_reenable_sensors_occupied()
		was_occupied = tracking["was_occupied"]
		
		if is_now_occupied != was_occupied:
			now = dt_util.utcnow()
			
			# If transitioning from occupied to vacant, add the occupied time
			if was_occupied and not is_now_occupied:
				if tracking["last_presence_change"]:
					occupied_duration = (now - tracking["last_presence_change"]).total_seconds()
					tracking["occupied_seconds"] += occupied_duration
					_LOGGER.debug(
						"Auto-reenable tracking: %s now vacant, added %.1fs occupied time (total: %.1fs)",
						self.entry.data.get(CONF_ROOM_NAME), occupied_duration, tracking["occupied_seconds"]
					)
			elif not was_occupied and is_now_occupied:
				_LOGGER.debug(
					"Auto-reenable tracking: %s now occupied",
					self.entry.data.get(CONF_ROOM_NAME)
				)
			
			tracking["last_presence_change"] = now
			tracking["was_occupied"] = is_now_occupied
			
			# Persist state periodically
			await self._save_tracking_state()

	async def _check_auto_reenable_startup(self) -> None:
		"""Check if we need to continue or evaluate tracking after a restart."""
		if not self._auto_reenable_enabled:
			return
		
		room_name = self.entry.data.get(CONF_ROOM_NAME)
		was_tracking = await self._load_tracking_state()
		
		if not was_tracking:
			_LOGGER.debug("No tracking state to restore for %s", room_name)
			return
		
		tracking = self._auto_reenable_tracking
		now = dt_util.utcnow()
		
		# Check if we're still in the monitoring window or just past it
		if not self._auto_reenable_start_time or not self._auto_reenable_end_time:
			_LOGGER.debug("Cannot check window for %s: times not set", room_name)
			return
		
		today = now.date()
		start_dt = dt_util.as_utc(datetime.combine(today, self._auto_reenable_start_time))
		end_dt = dt_util.as_utc(datetime.combine(today, self._auto_reenable_end_time))
		
		# Handle window crossing midnight
		if end_dt <= start_dt:
			# Window spans midnight - if we're before end, use yesterday's start
			if now < end_dt:
				start_dt = start_dt - timedelta(days=1)
			else:
				# We're after end, so next window starts today
				end_dt = end_dt + timedelta(days=1)
		
		# Check if window_start is valid for current window
		if tracking["window_start"]:
			window_start = tracking["window_start"]
			if isinstance(window_start, str):
				window_start = datetime.fromisoformat(window_start)
			
			# If we were tracking and are now past end time, evaluate
			if now >= end_dt and window_start < end_dt:
				_LOGGER.info(
					"HA restarted after monitoring window ended for %s, evaluating now",
					room_name
				)
				await self._evaluate_and_apply_auto_reenable()
			# If we're still in the window, continue tracking
			elif start_dt <= now < end_dt:
				_LOGGER.info(
					"HA restarted during monitoring window for %s, continuing tracking",
					room_name
				)
				tracking["is_tracking"] = True
				# Update presence state
				tracking["last_presence_change"] = now
				tracking["was_occupied"] = self._is_auto_reenable_sensors_occupied()
			else:
				_LOGGER.debug(
					"Stale tracking state for %s, clearing",
					room_name
				)
				await self._clear_tracking_state()
