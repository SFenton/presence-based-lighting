"""Presence Based Lighting integration entry point."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import time, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, Any

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
	DEFAULT_MANUAL_DISABLE_STATES,
	DEFAULT_OFF_DELAY,
	DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED,
	DEFAULT_REQUIRE_VACANCY_FOR_CLEARED,
	DEFAULT_RESPECTS_PRESENCE_ALLOWED,
	DOMAIN,
	ENABLE_FILE_LOGGING,
	PLATFORMS,
	STARTUP_MESSAGE,
)
from .entity_targeting import as_entity_list, legacy_room_switch_entity_id, slugify_entity_id
from .interceptor import PresenceLockInterceptor, is_interceptor_available
from .ownership import get_ownership_manager
from .real_last_changed import (
	ATTR_PREVIOUS_VALID_STATE,
	get_effective_state,
	is_entity_on,
	is_entity_off,
	is_real_last_changed_entity,
)
from .service_handlers import (
	SERVICE_PAUSE_AUTOMATION,
	SERVICE_RESUME_AUTOMATION,
	async_register_services,
)

_LOGGER = logging.getLogger(__package__)


class EntityAutomationState(Enum):
	"""Explicit state machine for per-entity presence automation.

	Every controlled entity is always in exactly one of these states.
	Each state has well-defined entry/exit actions and transitions,
	eliminating dead-end states where lights can get stuck.

	State diagram::

	  IDLE ──presence detected──▶ OCCUPIED (or PENDING_ACTIVATION)
	  OCCUPIED ──clearing sensors clear──▶ CLEARING (timer started)
	  CLEARING ──timer fires + sensors clear──▶ IDLE
	  CLEARING ──timer fires + sensors NOT clear──▶ WAITING_FOR_CLEAR
	  CLEARING ──presence detected──▶ OCCUPIED
	  WAITING_FOR_CLEAR ──sensors clear──▶ IDLE
	  WAITING_FOR_CLEAR ──presence detected──▶ OCCUPIED
	  WAITING_FOR_CLEAR ──safety timeout + room empty──▶ IDLE (forced)
	  WAITING_FOR_CLEAR ──safety timeout + room occupied──▶ OCCUPIED
	  PENDING_ACTIVATION ──conditions met──▶ OCCUPIED
	  PENDING_ACTIVATION ──room empties──▶ IDLE
	  PAUSED ──resume / state leaves disable list──▶ (reconciled)
	  Any state ──external manual control──▶ PAUSED
	"""

	IDLE = "idle"
	OCCUPIED = "occupied"
	PENDING_ACTIVATION = "pending_activation"
	CLEARING = "clearing"
	WAITING_FOR_CLEAR = "waiting_for_clear"
	SETTLING_OFF = "settling_off"
	SETTLING_ON = "settling_on"
	PAUSED = "paused"


class DesiredState(Enum):
	"""Logical desired state for a controlled entity."""

	DETECTED = "detected"
	CLEARED = "cleared"
	NONE = "none"


class IntentReason(Enum):
	"""Reason PBL currently wants or does not want to control an entity."""

	PRESENCE = "presence"
	CLEARING = "clearing"
	PAUSED = "paused"
	DISABLED = "disabled"
	OWNERSHIP = "ownership"
	CONDITIONS = "conditions"
	NO_ACTION = "no_action"
	NONE = "none"


class ActuationStatus(Enum):
	"""Closed-loop command status for a controlled entity."""

	IDLE = "idle"
	PENDING = "pending"
	CONFIRMED = "confirmed"
	FAILED = "failed"
	CANCELED = "canceled"


# Reconciliation interval – safety-net that catches any inconsistency
_RECONCILIATION_INTERVAL = timedelta(seconds=60)
# Maximum time an entity can stay in WAITING_FOR_CLEAR before forced IDLE
_WAITING_FOR_CLEAR_MAX_SECONDS = 300  # 5 minutes
_ACTUATION_CONFIRMATION_SECONDS = 2
_ACTUATION_RETRY_DELAY_SECONDS = 1
_ACTUATION_MAX_ATTEMPTS = 3

# Persistent debug log file (uncapped)
_log_file_handler: logging.FileHandler | None = None
_file_logging_setup = False
_file_logging_lock = asyncio.Lock()
_force_debug_unsub: Callable[[], None] | None = None


def _force_component_logger_debug() -> None:
	logger = logging.getLogger(__package__)
	logger.disabled = False
	logger.setLevel(logging.DEBUG)
	logger.propagate = True


def _emit_direct_to_file(msg: str) -> None:
	"""Write directly to the debug file handler, bypassing logger-level filtering."""
	if _log_file_handler is None:
		return
	try:
		_log_file_handler.emit(
			logging.LogRecord(
				name=__package__,
				level=logging.INFO,
				pathname=__file__,
				lineno=0,
				msg=msg,
				args=(),
				exc_info=None,
			)
		)
		_log_file_handler.flush()
	except Exception:
		pass


async def _setup_file_logging(hass: HomeAssistant) -> None:
	"""Set up a persistent debug log file.

	This restores the behavior that existed before commit d690f40 ("Removing file logging").
	It is intentionally simple and does not cap or trim the file.
	"""
	global _log_file_handler, _file_logging_setup

	async with _file_logging_lock:
		# Ensure we only set up once, even if multiple entries initialize at once.
		if _file_logging_setup:
			return
		_file_logging_setup = True

		if _log_file_handler is None:
			try:
				log_path = hass.config.path("presence_based_lighting_debug.log")

				# Create FileHandler in executor to avoid blocking I/O.
				_log_file_handler = await hass.async_add_executor_job(
					logging.FileHandler,
					log_path,
					"a",
				)
				_log_file_handler.setLevel(logging.DEBUG)
				formatter = logging.Formatter(
					"%(asctime)s - %(name)s - %(levelname)s - %(message)s"
				)
				_log_file_handler.setFormatter(formatter)

				# Attach to the component logger so submodule logs propagate into it.
				_LOGGER.addHandler(_log_file_handler)

				# Force our component logger to DEBUG so INFO/DEBUG records are actually created.
				_force_component_logger_debug()

				# Marker line written directly to the file so we can confirm runtime code.
				_emit_direct_to_file(
					"PBL debug file logging active (uncapped) - commit 6379b86"
				)
				_LOGGER.info("File logging enabled at: %s", log_path)
			except Exception as err:
				_LOGGER.error("Failed to set up file logging: %s", err)
				# Allow retry on next setup attempt.
				_file_logging_setup = False
				return
		else:
			# If we already have a handler (e.g., reload), ensure it's still attached.
			if _log_file_handler not in _LOGGER.handlers:
				_LOGGER.addHandler(_log_file_handler)
			_force_component_logger_debug()

		global _force_debug_unsub
		if _force_debug_unsub is None:
			@callback
			def _tick(_now: datetime) -> None:
				_force_component_logger_debug()

			_force_debug_unsub = async_track_time_interval(
				hass,
				_tick,
				timedelta(seconds=30),
			)


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
	"""Set up the Presence Based Lighting component."""
	await async_register_services(hass, PresenceBasedLightingCoordinator)

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
			
			# Add default manual_disable_states if not present
			if CONF_MANUAL_DISABLE_STATES not in updated_config:
				updated_config[CONF_MANUAL_DISABLE_STATES] = list(DEFAULT_MANUAL_DISABLE_STATES)

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

		# Optional persistent debug file logging (uncapped).
		# Check per-entry config toggle; fall back to the hard kill-switch constant.
		if entry.data.get("file_logging_enabled", ENABLE_FILE_LOGGING):
			await _setup_file_logging(hass)

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
		self._ownership_manager = get_ownership_manager(hass)
		self._interceptor: PresenceLockInterceptor | None = None
		self._using_interceptor: bool = False
		self._reconciliation_unsub: Callable[[], None] | None = None
		# Maps an RLC tracking sensor entity_id -> the controlled entity_id it
		# mirrors.  Used to detect manual/external control from the debounced RLC
		# signal instead of the racy synchronous read during the controlled
		# entity's own state_changed event.
		self._rlc_to_entity: Dict[str, str] = {}
		
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
				
				initial_presence_allowed = entity.get(
					CONF_INITIAL_PRESENCE_ALLOWED, DEFAULT_INITIAL_PRESENCE_ALLOWED
				)
				initial_state = EntityAutomationState.IDLE
				if not initial_presence_allowed and entity.get(
					CONF_RESPECTS_PRESENCE_ALLOWED,
					DEFAULT_RESPECTS_PRESENCE_ALLOWED,
				):
					initial_state = EntityAutomationState.PAUSED

				self._entity_states[entity_id] = {
					"config": entity,
					"domain": entity_id.split(".")[0],
					"presence_allowed": initial_presence_allowed,
					"state": initial_state,
					"state_entered_at": dt_util.utcnow(),
					"callbacks": set(),
					"contexts": deque(maxlen=20),
					"off_timer": None,
					"intent": self._new_entity_intent(),
					"actuation": self._new_actuation_state(),
					"last_effective_state": None,  # Track RLC effective state for change detection
				}
				self._ownership_manager.register_entity(self.entry.entry_id, entity_id)
			
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

	def _new_entity_intent(self) -> dict:
		return {
			"desired": DesiredState.NONE,
			"target_state": None,
			"service_key": None,
			"reason": IntentReason.NONE,
			"authority": False,
			"force": False,
			"updated_at": dt_util.utcnow(),
		}

	def _new_actuation_state(self) -> dict:
		return {
			"status": ActuationStatus.IDLE,
			"target_state": None,
			"service_key": None,
			"context_ids": deque(maxlen=10),
			"attempts": 0,
			"timer": None,
			"last_observed_state": None,
			"last_error": None,
			"updated_at": dt_util.utcnow(),
		}

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
					self._rlc_to_entity[rlc_tracking_entity] = entity_id
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

			# RLC tracking sensors are mirrors of controlled entities that update a
			# fraction of a moment AFTER the controlled entity's own state_changed
			# event.  Listening to them lets manual/external control be detected from
			# the debounced signal even when the synchronous RLC read during the
			# controlled entity's event returned a stale value (listener-order race).
			if self._rlc_to_entity:
				self._listeners.append(
					async_track_state_change_event(
						self.hass,
						list(self._rlc_to_entity.keys()),
						self._handle_rlc_tracking_change,
					)
				)
				_LOGGER.debug(
					"Registered state change listener for %d RLC tracking sensors: %s",
					len(self._rlc_to_entity), list(self._rlc_to_entity.keys()),
				)

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

			# Periodic state reconciliation – safety net that catches any
			# inconsistency (e.g., missed events, transient sensor blips)
			self._reconciliation_unsub = async_track_time_interval(
				self.hass,
				self._periodic_reconciliation,
				_RECONCILIATION_INTERVAL,
			)

			# Initial reconciliation: set each entity's state based on current
			# room conditions so the state machine starts from reality, not IDLE.
			for eid, es in self._entity_states.items():
				if self._presence_switch_allows_entity(es):
					await self._reconcile_entity(eid, es)
			
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

			self._ownership_manager.unregister_entry(self.entry.entry_id)

			# Cancel auto-reenable schedules
			self._cancel_auto_reenable_schedules()

			# Cancel reconciliation timer
			if self._reconciliation_unsub:
				self._reconciliation_unsub()
				self._reconciliation_unsub = None

			# Cancel all per-entity timers
			cancelled_count = 0
			for entity_id, entity_state in self._entity_states.items():
				if entity_state["off_timer"]:
					entity_state["off_timer"].cancel()
					entity_state["off_timer"] = None
					cancelled_count += 1
				self._cancel_entity_actuation(entity_state, "coordinator stopped")
			
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
		if self._entity_respects_presence_allowed(entity_state):
			if initial_state:
				if entity_state["state"] == EntityAutomationState.PAUSED:
					self._set_entity_state(
						entity_id,
						entity_state,
						EntityAutomationState.IDLE,
						"presence_allowed restored enabled",
					)
			else:
				self._cancel_entity_timer(entity_state)
				self._set_entity_state(
					entity_id,
					entity_state,
					EntityAutomationState.PAUSED,
					"presence_allowed restored disabled",
				)
		entity_state["callbacks"].add(update_callback)
		update_callback()

		def _remove() -> None:
			entity_state["callbacks"].discard(update_callback)

		return _remove

	def get_presence_allowed(self, entity_id: str) -> bool:
		return self._entity_states[entity_id]["presence_allowed"]

	def get_entity_automation_state(self, entity_id: str) -> str:
		"""Return the current state machine state as a string for UI display."""
		return self._entity_states[entity_id]["state"].value

	def get_entity_control_state(self, entity_id: str) -> dict:
		"""Return intent and actuator details for diagnostics/UI attributes."""
		entity_state = self._entity_states[entity_id]
		intent = entity_state["intent"]
		actuation = entity_state["actuation"]
		return {
			"desired_state": intent["desired"].value,
			"desired_target_state": intent["target_state"],
			"intent_reason": intent["reason"].value,
			"intent_authority": intent["authority"],
			"actuation_status": actuation["status"].value,
			"actuation_target_state": actuation["target_state"],
			"actuation_attempts": actuation["attempts"],
			"actuation_last_observed_state": actuation["last_observed_state"],
			"actuation_last_error": actuation["last_error"],
		}

	def _set_entity_intent(
		self,
		entity_id: str,
		entity_state: dict,
		desired: DesiredState,
		service_key: str | None,
		target_state: str | None,
		reason: IntentReason,
		authority: bool,
		force: bool = False,
	) -> dict:
		intent = entity_state["intent"]
		intent.update(
			{
				"desired": desired,
				"service_key": service_key,
				"target_state": target_state,
				"reason": reason,
				"authority": authority,
				"force": force,
				"updated_at": dt_util.utcnow(),
			}
		)
		_LOGGER.debug(
			"[%s] intent desired=%s target=%s reason=%s authority=%s",
			entity_id,
			desired.value,
			target_state,
			reason.value,
			authority,
		)
		self._notify_switch(entity_id)
		return intent

	def _intent_for_service(
		self, entity_id: str, entity_state: dict, service_key: str, reason: IntentReason,
		force: bool = False,
	) -> dict:
		config = entity_state["config"]
		if service_key == CONF_PRESENCE_DETECTED_SERVICE:
			desired = DesiredState.DETECTED
			target_state = config[CONF_PRESENCE_DETECTED_STATE]
		else:
			desired = DesiredState.CLEARED
			target_state = config[CONF_PRESENCE_CLEARED_STATE]

		authority = True
		intent_reason = reason
		if config[service_key] == NO_ACTION:
			authority = False
			intent_reason = IntentReason.NO_ACTION
		elif not self._presence_switch_allows_entity(entity_state):
			authority = False
			intent_reason = IntentReason.DISABLED
		elif entity_state["state"] == EntityAutomationState.PAUSED:
			authority = False
			intent_reason = IntentReason.PAUSED
		elif (
			service_key == CONF_PRESENCE_CLEARED_SERVICE
			and self._ownership_manager.other_entry_wants_on(self.entry.entry_id, entity_id)
		):
			authority = False
			intent_reason = IntentReason.OWNERSHIP

		return self._set_entity_intent(
			entity_id,
			entity_state,
			desired,
			service_key,
			target_state,
			intent_reason,
			authority,
			force,
		)

	async def _apply_service_intent(
		self, entity_id: str, entity_state: dict, service_key: str, reason: IntentReason,
		force: bool = False,
	) -> bool:
		intent = self._intent_for_service(entity_id, entity_state, service_key, reason, force)
		return await self._apply_intent(entity_id, entity_state, intent)

	async def _apply_intent(self, entity_id: str, entity_state: dict, intent: dict) -> bool:
		if not intent["authority"] or not intent["service_key"]:
			self._cancel_entity_actuation(entity_state, intent["reason"].value)
			if (
				intent["desired"] == DesiredState.CLEARED
				and intent["reason"] in (IntentReason.OWNERSHIP, IntentReason.NO_ACTION)
			):
				self._set_entity_state(
					entity_id,
					entity_state,
					EntityAutomationState.IDLE,
					f"cleared intent suppressed: {intent['reason'].value}",
				)
			return False

		actuation = entity_state["actuation"]
		if (
			actuation["status"] == ActuationStatus.PENDING
			and actuation["target_state"] == intent["target_state"]
			and actuation["service_key"] == intent["service_key"]
		):
			if intent["desired"] == DesiredState.CLEARED and entity_state["state"] != EntityAutomationState.SETTLING_OFF:
				self._set_entity_state(entity_id, entity_state, EntityAutomationState.SETTLING_OFF, "pending cleared intent")
			return True

		await self._begin_entity_actuation(entity_id, entity_state, intent)
		return True

	async def _begin_entity_actuation(self, entity_id: str, entity_state: dict, intent: dict) -> None:
		config = entity_state["config"]
		service_key = intent["service_key"]
		target_state = intent["target_state"]

		self._cancel_entity_actuation(entity_state, "new intent")
		actuation = entity_state["actuation"]
		actuation.update(
			{
				"status": ActuationStatus.PENDING,
				"target_state": target_state,
				"service_key": service_key,
				"context_ids": deque(maxlen=10),
				"attempts": 0,
				"last_observed_state": None,
				"last_error": None,
				"updated_at": dt_util.utcnow(),
			}
		)

		if service_key == CONF_PRESENCE_CLEARED_SERVICE:
			self._set_entity_state(entity_id, entity_state, EntityAutomationState.SETTLING_OFF, "actuating cleared intent")
		elif entity_state["state"] == EntityAutomationState.IDLE:
			self._set_entity_state(entity_id, entity_state, EntityAutomationState.SETTLING_ON, "actuating detected intent")

		current_state = self.hass.states.get(entity_id)
		if current_state and current_state.state == target_state:
			self._confirm_entity_actuation(entity_id, entity_state, target_state)
			return

		if config[service_key] == NO_ACTION:
			self._confirm_entity_actuation(entity_id, entity_state, target_state)
			return

		await self._send_entity_actuation_attempt(entity_id, entity_state)

	def _actuation_target_is_still_valid(self, entity_id: str, entity_state: dict) -> bool:
		intent = entity_state["intent"]
		actuation = entity_state["actuation"]
		if not intent["authority"]:
			return False
		if intent["target_state"] != actuation["target_state"]:
			return False
		if intent["service_key"] != actuation["service_key"]:
			return False
		if entity_state["state"] == EntityAutomationState.PAUSED:
			return False
		if not self._presence_switch_allows_entity(entity_state):
			return False
		if intent["desired"] == DesiredState.CLEARED:
			if self._ownership_manager.other_entry_wants_on(self.entry.entry_id, entity_id):
				return False
			if not intent.get("force") and not self._are_clearing_sensors_clear():
				return False
		elif intent["desired"] == DesiredState.DETECTED:
			if not self._is_any_occupied() or not self._are_activation_conditions_met():
				return False
		return True

	def _cancel_actuation_timer(self, entity_state: dict) -> None:
		actuation = entity_state["actuation"]
		timer = actuation.get("timer")
		if timer is not None:
			timer.cancel()
			actuation["timer"] = None

	def _cancel_entity_actuation(self, entity_state: dict, reason: str) -> None:
		actuation = entity_state["actuation"]
		self._cancel_actuation_timer(entity_state)
		if actuation["status"] == ActuationStatus.PENDING:
			_LOGGER.debug(
				"[%s] actuation canceled: %s",
				entity_state["config"].get(CONF_ENTITY_ID, "unknown"),
				reason,
			)
		actuation.update(
			{
				"status": ActuationStatus.CANCELED,
				"target_state": None,
				"service_key": None,
				"attempts": 0,
				"last_error": reason,
				"updated_at": dt_util.utcnow(),
			}
		)

	def _schedule_actuation_timer(self, entity_id: str, entity_state: dict, delay: float, retry: bool) -> None:
		self._cancel_actuation_timer(entity_state)
		if retry:
			task = asyncio.create_task(self._execute_actuation_retry_timer(entity_id, entity_state, delay))
		else:
			task = asyncio.create_task(self._execute_actuation_confirmation_timer(entity_id, entity_state, delay))
		entity_state["actuation"]["timer"] = task

	async def _execute_actuation_confirmation_timer(
		self, entity_id: str, entity_state: dict, delay: float,
	) -> None:
		this_task = asyncio.current_task()
		try:
			await asyncio.sleep(delay)
			actuation = entity_state["actuation"]
			if actuation["status"] != ActuationStatus.PENDING:
				return
			current_state = self.hass.states.get(entity_id)
			observed = current_state.state if current_state else None
			actuation["last_observed_state"] = observed
			if observed == actuation["target_state"]:
				self._confirm_entity_actuation(entity_id, entity_state, observed)
			else:
				await self._retry_or_fail_entity_actuation(entity_id, entity_state, observed)
		except asyncio.CancelledError:
			_LOGGER.debug("[%s] Actuation confirmation timer cancelled", entity_id)
		except Exception as err:
			_LOGGER.exception("[%s] Error in actuation confirmation timer: %s", entity_id, err)
		finally:
			if entity_state["actuation"].get("timer") is this_task:
				entity_state["actuation"]["timer"] = None

	async def _execute_actuation_retry_timer(
		self, entity_id: str, entity_state: dict, delay: float,
	) -> None:
		this_task = asyncio.current_task()
		try:
			await asyncio.sleep(delay)
			await self._send_entity_actuation_attempt(entity_id, entity_state)
		except asyncio.CancelledError:
			_LOGGER.debug("[%s] Actuation retry timer cancelled", entity_id)
		except Exception as err:
			_LOGGER.exception("[%s] Error in actuation retry timer: %s", entity_id, err)
		finally:
			if entity_state["actuation"].get("timer") is this_task:
				entity_state["actuation"]["timer"] = None

	async def _send_entity_actuation_attempt(self, entity_id: str, entity_state: dict) -> None:
		actuation = entity_state["actuation"]
		if actuation["status"] != ActuationStatus.PENDING:
			return

		if not self._actuation_target_is_still_valid(entity_id, entity_state):
			self._cancel_entity_actuation(entity_state, "intent no longer valid")
			return

		current_state = self.hass.states.get(entity_id)
		observed = current_state.state if current_state else None
		actuation["last_observed_state"] = observed
		if observed == actuation["target_state"]:
			self._schedule_actuation_timer(entity_id, entity_state, _ACTUATION_CONFIRMATION_SECONDS, retry=False)
			return

		if actuation["attempts"] >= _ACTUATION_MAX_ATTEMPTS:
			self._fail_entity_actuation(entity_id, entity_state, observed)
			return

		config = entity_state["config"]
		service_key = actuation["service_key"]
		service = config[service_key]
		context = Context()
		entity_state["contexts"].append(context.id)
		actuation["context_ids"].append(context.id)
		actuation["attempts"] += 1
		actuation["updated_at"] = dt_util.utcnow()
		if not config.get(CONF_RLC_TRACKING_ENTITY):
			entity_state["last_effective_state"] = actuation["target_state"]

		_LOGGER.debug(
			"Calling service %s.%s for entity %s (actuation attempt %d/%d, target=%s)",
			entity_state["domain"],
			service,
			entity_id,
			actuation["attempts"],
			_ACTUATION_MAX_ATTEMPTS,
			actuation["target_state"],
		)
		await self.hass.services.async_call(
			entity_state["domain"],
			service,
			{"entity_id": entity_id},
			blocking=True,
			context=context,
		)
		_LOGGER.debug("Service call completed for %s", entity_id)
		self._schedule_actuation_timer(entity_id, entity_state, _ACTUATION_CONFIRMATION_SECONDS, retry=False)

	async def _handle_actuation_feedback(
		self, entity_id: str, entity_state: dict, observed_state: str,
	) -> None:
		actuation = entity_state["actuation"]
		actuation["last_observed_state"] = observed_state
		actuation["updated_at"] = dt_util.utcnow()
		if actuation["status"] != ActuationStatus.PENDING:
			return
		if observed_state == actuation["target_state"]:
			_LOGGER.debug(
				"[%s] Actuation observed target state %s; waiting for confirmation window",
				entity_id,
				observed_state,
			)
			return
		await self._retry_or_fail_entity_actuation(entity_id, entity_state, observed_state)

	async def _retry_or_fail_entity_actuation(
		self, entity_id: str, entity_state: dict, observed_state: str | None,
	) -> None:
		actuation = entity_state["actuation"]
		if not self._actuation_target_is_still_valid(entity_id, entity_state):
			self._cancel_entity_actuation(entity_state, "intent no longer valid")
			return
		if actuation["attempts"] >= _ACTUATION_MAX_ATTEMPTS:
			self._fail_entity_actuation(entity_id, entity_state, observed_state)
			return
		_LOGGER.debug(
			"[%s] Actuation target %s not converged (observed %s); scheduling retry %d/%d",
			entity_id,
			actuation["target_state"],
			observed_state,
			actuation["attempts"] + 1,
			_ACTUATION_MAX_ATTEMPTS,
		)
		self._schedule_actuation_timer(entity_id, entity_state, _ACTUATION_RETRY_DELAY_SECONDS, retry=True)

	def _confirm_entity_actuation(self, entity_id: str, entity_state: dict, observed_state: str | None) -> None:
		actuation = entity_state["actuation"]
		self._cancel_actuation_timer(entity_state)
		service_key = actuation["service_key"]
		actuation.update(
			{
				"status": ActuationStatus.CONFIRMED,
				"last_observed_state": observed_state,
				"last_error": None,
				"updated_at": dt_util.utcnow(),
			}
		)
		_LOGGER.debug("[%s] Actuation confirmed target=%s", entity_id, actuation["target_state"])
		if service_key == CONF_PRESENCE_CLEARED_SERVICE:
			self._set_entity_state(entity_id, entity_state, EntityAutomationState.IDLE, "actuation confirmed cleared")
		elif service_key == CONF_PRESENCE_DETECTED_SERVICE and entity_state["state"] == EntityAutomationState.SETTLING_ON:
			self._set_entity_state(entity_id, entity_state, EntityAutomationState.OCCUPIED, "actuation confirmed detected")
		self._notify_switch(entity_id)

	def _fail_entity_actuation(
		self, entity_id: str, entity_state: dict, observed_state: str | None,
	) -> None:
		actuation = entity_state["actuation"]
		self._cancel_actuation_timer(entity_state)
		message = (
			f"target {actuation['target_state']} did not converge "
			f"after {actuation['attempts']} attempts; observed {observed_state}"
		)
		actuation.update(
			{
				"status": ActuationStatus.FAILED,
				"last_observed_state": observed_state,
				"last_error": message,
				"updated_at": dt_util.utcnow(),
			}
		)
		_LOGGER.warning("[%s] Actuation failed: %s", entity_id, message)
		self._notify_switch(entity_id)

	async def async_set_presence_allowed(self, entity_id: str, allowed: bool) -> None:
		"""Set user-controlled presence_allowed state (persisted by switch)."""
		entity_state = self._entity_states[entity_id]
		if entity_state["presence_allowed"] == allowed:
			return

		entity_state["presence_allowed"] = allowed
		self._notify_switch(entity_id)

		if not self._entity_respects_presence_allowed(entity_state):
			await self._reconcile_entity(entity_id, entity_state)
			return

		if allowed:
			# Entering automation control – reconcile to the correct state
			if entity_state["state"] == EntityAutomationState.PAUSED:
				self._set_entity_state(entity_id, entity_state, EntityAutomationState.IDLE, "presence_allowed enabled")
			await self._reconcile_entity(entity_id, entity_state)
		else:
			# Leaving automation control – cancel any running timer and hold PAUSED.
			self._cancel_entity_timer(entity_state)
			self._cancel_entity_actuation(entity_state, "presence_allowed disabled")
			self._set_entity_state(entity_id, entity_state, EntityAutomationState.PAUSED, "presence_allowed disabled")

	def _entity_respects_presence_allowed(self, entity_state: dict) -> bool:
		return entity_state["config"].get(
			CONF_RESPECTS_PRESENCE_ALLOWED,
			DEFAULT_RESPECTS_PRESENCE_ALLOWED,
		)

	def _presence_switch_allows_entity(self, entity_state: dict) -> bool:
		if not self._entity_respects_presence_allowed(entity_state):
			return True
		return entity_state["presence_allowed"]

	def _legacy_room_switch_entity_id(self) -> str:
		room_name = self.entry.data.get(CONF_ROOM_NAME, "")
		return legacy_room_switch_entity_id(room_name)

	def _presence_switch_entity_ids(self, entity_id: str, entity_state: dict) -> set[str]:
		room_name = self.entry.data.get(CONF_ROOM_NAME, "")
		domain, object_id = entity_id.split(".", 1)
		object_label = object_id.replace("_", " ").title()
		candidates = {
			self._legacy_room_switch_entity_id(),
			f"switch.{slugify_entity_id(f'{room_name} Presence {object_label} Presence Allowed')}",
			f"switch.{slugify_entity_id(f'{room_name} Presence {object_id} Presence Allowed')}",
			f"switch.{slugify_entity_id(f'{room_name} Presence {entity_id} Presence Allowed')}",
			f"switch.{slugify_entity_id(f'{room_name} Presence {domain} {object_id} Presence Allowed')}",
		}

		state = self.hass.states.get(entity_id)
		friendly = state.attributes.get("friendly_name") if state else None
		if friendly:
			candidates.add(
				f"switch.{slugify_entity_id(f'{room_name} Presence {friendly} Presence Allowed')}"
			)

		return candidates

	def resolve_service_target_entities(self, target_switches: list[str]) -> list[str]:
		"""Resolve pause/resume service targets to controlled entity ids."""
		target_set = set(target_switches)
		if "*" in target_set or self._legacy_room_switch_entity_id() in target_set:
			return list(self._entity_states)

		matched = []
		for entity_id, entity_state in self._entity_states.items():
			if target_set & self._presence_switch_entity_ids(entity_id, entity_state):
				matched.append(entity_id)
		return matched

	def get_automation_paused(self, entity_id: str) -> bool:
		"""Get whether automation is temporarily paused for this entity."""
		return self._entity_states[entity_id]["state"] == EntityAutomationState.PAUSED

	def set_automation_paused(self, entity_id: str, paused: bool) -> None:
		"""Transition to/from PAUSED state (transient, based on manual control).
		
		This is separate from presence_allowed:
		- presence_allowed: User-controlled, persisted across reboots
		- PAUSED state: Automatic, transient, based on manual_disable_states
		"""
		entity_state = self._entity_states[entity_id]
		current_state = entity_state["state"]
		is_paused = current_state == EntityAutomationState.PAUSED
		
		if is_paused == paused:
			return
		
		if paused:
			self._cancel_entity_timer(entity_state)
			self._cancel_entity_actuation(entity_state, "manual control")
			self._set_entity_state(entity_id, entity_state, EntityAutomationState.PAUSED, "manual control")
		else:
			_LOGGER.debug("Automation resumed for %s, will reconcile state", entity_id)
			# Don't reconcile here synchronously – the caller may need to await it
			# Just set to IDLE; the caller or reconciliation will fix it
			self._set_entity_state(entity_id, entity_state, EntityAutomationState.IDLE, "manual control resumed")
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

			service = event.data.get("service")
			expanded_entities = self._expand_target_entities(target)

			for entity_id in expanded_entities:
				if self._is_context_ours(entity_id, event.context):
					continue
				await self._handle_external_action(entity_id, service)
		except Exception as err:
			_LOGGER.exception("Error handling service call event: %s", err)

	def _expand_target_entities(self, target: Any) -> list[str]:
		"""Expand service targets to controlled entities, following nested groups."""
		matched: list[str] = []
		seen: set[str] = set()
		to_visit = as_entity_list(target)

		while to_visit:
			entity_id = to_visit.pop(0)
			if not isinstance(entity_id, str):
				_LOGGER.debug(
					"Skipping non-string entity_id: %s (type: %s)",
					entity_id, type(entity_id),
				)
				continue
			if entity_id in seen:
				continue
			seen.add(entity_id)

			if entity_id in self._entity_states:
				matched.append(entity_id)
				continue

			state = self.hass.states.get(entity_id)
			if state and state.attributes.get("entity_id"):
				to_visit.extend(as_entity_list(state.attributes.get("entity_id")))

		return matched

	async def _handle_controlled_entity_change(self, event: Event) -> None:
		try:
			entity_id = event.data.get("entity_id")
			if not entity_id or entity_id not in self._entity_states:
				return

			new_state = event.data.get("new_state")
			old_state = event.data.get("old_state")
			if not new_state or not old_state or new_state.state == old_state.state:
				return

			entity_state = self._entity_states[entity_id]
			cfg = entity_state["config"]
			is_our_context = self._is_context_ours(entity_id, new_state.context)

			# Check if an RLC tracking entity is configured for this entity
			# If so, use the RLC sensor's state to determine if this is a "real" change
			rlc_tracking_entity = cfg.get(CONF_RLC_TRACKING_ENTITY)
			if rlc_tracking_entity:
				# Get the "real" state from the RLC sensor
				rlc_state = get_effective_state(self.hass, rlc_tracking_entity)
				if rlc_state is None:
					_LOGGER.debug(
						"RLC tracking entity %s unavailable for %s, falling back to direct state %s",
						rlc_tracking_entity, entity_id, new_state.state
					)
					effective_new_state = new_state.state
				else:
					# Use the RLC sensor's previous_valid_state as the effective state.
					effective_new_state = rlc_state
					last_effective = entity_state.get("last_effective_state")
					if last_effective is None:
						entity_state["last_effective_state"] = effective_new_state
						_LOGGER.debug(
							"RLC tracking entity %s for %s: first event, initializing last_effective_state to %s (skipping manual control)",
							rlc_tracking_entity, entity_id, effective_new_state
						)
						return
					if effective_new_state == last_effective:
						if is_our_context:
							await self._handle_actuation_feedback(entity_id, entity_state, new_state.state)
							return
						_LOGGER.debug(
							"RLC tracking entity %s for %s: effective state unchanged (%s), ignoring",
							rlc_tracking_entity, entity_id, effective_new_state
						)
						return
					entity_state["last_effective_state"] = effective_new_state
					_LOGGER.debug(
						"Using RLC tracking entity %s for %s: effective state = %s (raw state = %s)",
						rlc_tracking_entity, entity_id, effective_new_state, new_state.state
					)
			else:
				# No RLC tracking - use the entity's direct state
				effective_new_state = new_state.state

			if is_our_context:
				await self._handle_actuation_feedback(entity_id, entity_state, effective_new_state)
				return

			await self._process_external_controlled_change(entity_id, entity_state, effective_new_state)
		except Exception as err:
			_LOGGER.exception("Error handling controlled entity change for %s: %s", event.data.get("entity_id"), err)

	async def _handle_rlc_tracking_change(self, event: Event) -> None:
		"""Detect manual/external control from an RLC tracking sensor change.

		The controlled entity's own ``state_changed`` event and its RLC mirror are
		driven by the *same* underlying change, but the RLC sensor is updated by a
		sibling listener and its ``state_changed`` event is dispatched immediately
		afterwards.  Reading the RLC mirror synchronously inside
		``_handle_controlled_entity_change`` can therefore observe a stale
		``previous_valid_state`` and miss a genuine manual ``off`` (the change is
		deduped as "no effective change").  Handling the RLC sensor's own change
		closes that race while still ignoring spurious raw-state blips (reboot /
		availability) that never move the RLC ``previous_valid_state``.
		"""
		try:
			rlc_entity_id = event.data.get("entity_id")
			entity_id = self._rlc_to_entity.get(rlc_entity_id)
			if not entity_id or entity_id not in self._entity_states:
				return

			new_state = event.data.get("new_state")
			old_state = event.data.get("old_state")
			if not new_state:
				return

			new_effective = new_state.attributes.get(ATTR_PREVIOUS_VALID_STATE)
			old_effective = old_state.attributes.get(ATTR_PREVIOUS_VALID_STATE) if old_state else None
			if new_effective is None or new_effective == old_effective:
				# Timestamp-only update or unavailable RLC sensor - nothing real changed.
				return

			entity_state = self._entity_states[entity_id]
			if new_effective == entity_state.get("last_effective_state"):
				# Already processed (e.g. the synchronous read in
				# _handle_controlled_entity_change saw the fresh RLC value first).
				return
			entity_state["last_effective_state"] = new_effective

			# Ownership is derived from the controlled entity's settled context: if
			# we issued the change, the light's current context is one of ours.
			controlled = self.hass.states.get(entity_id)
			is_our_context = (
				controlled is not None
				and self._is_context_ours(entity_id, controlled.context)
			)
			if is_our_context:
				await self._handle_actuation_feedback(entity_id, entity_state, new_effective)
				return

			await self._process_external_controlled_change(entity_id, entity_state, new_effective)
		except Exception as err:
			_LOGGER.exception(
				"Error handling RLC tracking change for %s: %s",
				event.data.get("entity_id"), err,
			)

	async def _process_external_controlled_change(
		self, entity_id: str, entity_state: dict, effective_new_state: str | None,
	) -> None:
		"""Apply manual-control pause/resume logic for an external state change."""
		cfg = entity_state["config"]

		# Check presence lock first - this takes priority
		if await self._check_and_apply_presence_lock(entity_state, effective_new_state):
			return  # Presence lock handled the state change

		if not cfg[CONF_DISABLE_ON_EXTERNAL_CONTROL]:
			return

		if not self._presence_switch_allows_entity(entity_state):
			return

		# Determine whether this external change should pause or resume automation
		should_pause = self._should_external_change_pause(entity_id, cfg, effective_new_state)

		if should_pause:
			self.set_automation_paused(entity_id, True)
		else:
			self.set_automation_paused(entity_id, False)
			if await self._ensure_external_detected_action_expires(
				entity_id, entity_state, effective_new_state
			):
				return
			# Reconcile into the correct active state
			await self._reconcile_entity(entity_id, entity_state)


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
		
		if target_state and await self._check_and_apply_presence_lock(
			entity_state, target_state, force_fallback=True
		):
			return  # Presence lock handled the state change

		if not cfg[CONF_DISABLE_ON_EXTERNAL_CONTROL]:
			return

		if not self._presence_switch_allows_entity(entity_state):
			return

		# Determine whether this external action should pause or resume automation
		should_pause = self._should_external_change_pause(entity_id, cfg, target_state)

		if should_pause:
			self.set_automation_paused(entity_id, True)
		elif target_state:
			self.set_automation_paused(entity_id, False)
			if await self._ensure_external_detected_action_expires(
				entity_id, entity_state, target_state
			):
				return
			await self._reconcile_entity(entity_id, entity_state)

	async def _ensure_external_detected_action_expires(
		self, entity_id: str, entity_state: dict, target_state: str | None,
	) -> bool:
		"""Start an off timer for external turn-on actions while the room is clear.

		External service calls are observed before Home Assistant applies the new
		state.  If a broad automation turns a light on while the room is already
		vacant, a plain reconciliation can still see the old off state and miss the
		expiry path.  Treat the external detected state as a temporary occupied
		period so normal clearing logic turns it back off.
		"""
		config = entity_state["config"]
		if target_state != config[CONF_PRESENCE_DETECTED_STATE]:
			return False

		if not self._are_clearing_sensors_clear():
			return False

		if (
			entity_state["state"] == EntityAutomationState.CLEARING
			and entity_state.get("off_timer") is not None
		):
			_LOGGER.debug(
				"[%s] External detected action while sensors clear; existing off timer remains active",
				entity_id,
			)
			return True

		self._set_entity_state(
			entity_id,
			entity_state,
			EntityAutomationState.OCCUPIED,
			"external detected action while sensors clear",
		)
		await self._start_entity_off_timer(entity_id, entity_state)
		return True

	async def _check_and_apply_presence_lock(
		self, entity_state: dict, new_state: str, force_fallback: bool = False
	) -> bool:
		"""Check presence lock conditions and revert state if needed.
		
		Returns True if a presence lock was triggered and the state was reverted.
		
		When hass-interceptor is active, this is a fallback that should rarely
		trigger (interceptor blocks proactively). When not active, this is the
		primary mechanism that reverts state reactively after it changes.
		"""
		# Skip state-change fallback when using interceptor; service-call fallback
		# still runs because reaching the listener means the call was not blocked.
		if self._using_interceptor and not force_fallback:
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
			
			# --- Presence sensor turns ON ---
			if currently_on and entity_id in presence_sensors:
				_LOGGER.debug("Presence detected via %s", entity_id)
				for eid, es in self._entity_states.items():
					if not self._presence_switch_allows_entity(es):
						continue
					cur = es["state"]
					if cur in (
						EntityAutomationState.IDLE,
						EntityAutomationState.CLEARING,
						EntityAutomationState.WAITING_FOR_CLEAR,
						EntityAutomationState.SETTLING_OFF,
						EntityAutomationState.PENDING_ACTIVATION,
					):
						self._cancel_entity_timer(es)
						self._cancel_entity_actuation(es, "presence detected")
						if self._are_activation_conditions_met():
							self._set_entity_state(eid, es, EntityAutomationState.OCCUPIED, "presence detected")
							await self._apply_service_intent(eid, es, CONF_PRESENCE_DETECTED_SERVICE, IntentReason.PRESENCE)
						else:
							self._set_entity_state(eid, es, EntityAutomationState.PENDING_ACTIVATION, "presence detected, conditions not met")
				# Start off-timer for OCCUPIED entities ONLY if clearing sensors
				# are already clear (primer-sensor case: hallway PIR triggers but
				# nobody enters the room).  When clearing sensors are still active
				# the entity must stay in OCCUPIED and let the clearing-sensor-OFF
				# handler manage the transition naturally.
				if self._are_clearing_sensors_clear():
					await self._start_off_timer()

			# --- Clearing sensor turns OFF ---
			elif currently_off:
				effective_clearing = clearing_sensors if clearing_sensors else presence_sensors
				if entity_id in effective_clearing:
					all_clear = self._are_clearing_sensors_clear()
					if all_clear:
						_LOGGER.debug("All clearing sensors clear")
						for eid, es in self._entity_states.items():
							if not self._presence_switch_allows_entity(es):
								continue
							cur = es["state"]
							if cur == EntityAutomationState.OCCUPIED:
								# Start off-timer → CLEARING
								await self._start_entity_off_timer(eid, es)
							elif cur == EntityAutomationState.WAITING_FOR_CLEAR:
								# Sensors finally cleared – turn off immediately
								_LOGGER.debug("%s: sensors cleared while WAITING_FOR_CLEAR, turning off", eid)
								self._cancel_entity_timer(es)
								await self._apply_service_intent(eid, es, CONF_PRESENCE_CLEARED_SERVICE, IntentReason.CLEARING)
							elif cur == EntityAutomationState.PENDING_ACTIVATION:
								# Room emptied while waiting for conditions – turn off if light was on
								await self._apply_service_intent(eid, es, CONF_PRESENCE_CLEARED_SERVICE, IntentReason.CLEARING)
		except Exception as err:
			_LOGGER.exception("Error handling presence change: %s", err)

	async def _handle_activation_condition_change(self, event: Event) -> None:
		"""Handle state changes on activation condition entities.
		
		When activation conditions become true while entities are in
		PENDING_ACTIVATION state, transition them to OCCUPIED.
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
			
			if not self._are_activation_conditions_met():
				_LOGGER.debug("Activation condition changed but not all conditions met yet")
				return
			
			# Transition PENDING_ACTIVATION entities to OCCUPIED
			for eid, es in self._entity_states.items():
				if not self._presence_switch_allows_entity(es):
					continue
				if es["state"] == EntityAutomationState.PENDING_ACTIVATION:
					_LOGGER.debug(
						"Activation conditions now met – transitioning %s from PENDING to OCCUPIED", eid
					)
					self._cancel_entity_timer(es)
					self._set_entity_state(eid, es, EntityAutomationState.OCCUPIED, "activation conditions met")
					await self._apply_service_intent(eid, es, CONF_PRESENCE_DETECTED_SERVICE, IntentReason.CONDITIONS)
			
			# Start off-timer for newly-OCCUPIED entities ONLY if clearing
			# sensors are already clear (same primer-sensor guard as in
			# _handle_presence_change).
			if self._are_clearing_sensors_clear():
				await self._start_off_timer()
		except Exception as err:
			_LOGGER.exception("Error handling activation condition change: %s", err)

	async def _apply_presence_action(self, service_key: str) -> None:
		"""Apply presence action to all controlled entities that should follow presence.
		
		Note: With the state machine, most per-entity transitions are handled
		inline by the event handlers. This helper remains for bulk operations
		(e.g., auto-reenable) where we want to apply an action to all eligible entities.
		"""
		_LOGGER.debug("Applying presence action %s to %d entities: %s", 
					 service_key, len(self._entity_states), list(self._entity_states.keys()))
		
		for entity_id, entity_state in self._entity_states.items():
			entity_id = entity_state["config"].get(CONF_ENTITY_ID, "unknown")
			if not self._should_follow_presence(entity_state):
				_LOGGER.debug("Skipping %s - not following presence", entity_id)
				continue
			reason = IntentReason.PRESENCE if service_key == CONF_PRESENCE_DETECTED_SERVICE else IntentReason.CLEARING
			self._cancel_entity_actuation(entity_state, "bulk presence action")
			await self._apply_service_intent(entity_id, entity_state, service_key, reason)

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
				if self._ownership_manager.other_entry_wants_on(self.entry.entry_id, entity_id):
					_LOGGER.debug(
						"Suppressing cleared action for %s because another entry still wants it on",
						entity_id,
					)
					return
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
		- State is not PAUSED: Transient pause due to manual control
		"""
		return self._presence_switch_allows_entity(entity_state) and entity_state["state"] != EntityAutomationState.PAUSED

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
		"""Start per-entity off timers for all OCCUPIED entities."""
		for entity_id, entity_state in self._entity_states.items():
			if entity_state["state"] == EntityAutomationState.OCCUPIED:
				await self._start_entity_off_timer(entity_id, entity_state)

	async def _start_entity_off_timer(self, entity_id: str, entity_state: dict) -> None:
		"""Start (or restart) the off-timer for a single entity → CLEARING."""
		self._cancel_entity_timer(entity_state)

		config = entity_state["config"]
		delay = config.get(CONF_ENTITY_OFF_DELAY)
		if delay is None:
			delay = self.entry.data.get(CONF_OFF_DELAY, DEFAULT_OFF_DELAY)

		self._set_entity_state(entity_id, entity_state, EntityAutomationState.CLEARING, f"off-timer started ({delay}s)")
		task = asyncio.create_task(self._execute_entity_off_timer(entity_id, entity_state, delay))
		entity_state["off_timer"] = task

	async def _execute_entity_off_timer(self, entity_id: str, entity_state: dict, delay: int) -> None:
		"""Execute the off timer for a specific entity.
		
		When timer fires:
		- If clearing sensors are clear → transition to IDLE (turn off)
		- If not clear → transition to WAITING_FOR_CLEAR (event-driven recovery)
		"""
		this_task = asyncio.current_task()
		try:
			_LOGGER.debug("[%s] Off timer sleeping %ds (state: CLEARING)", entity_id, delay)
			await asyncio.sleep(delay)

			if self._are_clearing_sensors_clear():
				_LOGGER.debug("[%s] Timer fired, clearing sensors clear → cleared intent", entity_id)
				await self._apply_service_intent(entity_id, entity_state, CONF_PRESENCE_CLEARED_SERVICE, IntentReason.CLEARING)
			else:
				_LOGGER.debug(
					"[%s] Timer fired, clearing sensors NOT all clear → WAITING_FOR_CLEAR",
					entity_id,
				)
				self._set_entity_state(
					entity_id, entity_state, EntityAutomationState.WAITING_FOR_CLEAR,
					"timer fired, sensors not clear"
				)
				# No polling – the existing clearing-sensor listener in
				# _handle_presence_change will transition us to IDLE when sensors
				# clear.  The periodic reconciliation acts as a safety net.
		except asyncio.CancelledError:
			_LOGGER.debug("[%s] Off timer cancelled", entity_id)
		except Exception as err:
			_LOGGER.exception("[%s] Error in off timer: %s", entity_id, err)
		finally:
			if entity_state.get("off_timer") is this_task:
				entity_state["off_timer"] = None

	# -----------------------------------------------------------------
	# State machine helpers
	# -----------------------------------------------------------------

	def _set_entity_state(
		self, entity_id: str, entity_state: dict,
		new_state: EntityAutomationState, reason: str = "",
	) -> None:
		"""Transition an entity to a new state with logging."""
		old_state = entity_state["state"]
		if old_state == new_state:
			return
		entity_state["state"] = new_state
		entity_state["state_entered_at"] = dt_util.utcnow()
		desired_on = new_state in (
			EntityAutomationState.OCCUPIED,
			EntityAutomationState.CLEARING,
			EntityAutomationState.WAITING_FOR_CLEAR,
			EntityAutomationState.SETTLING_ON,
		)
		self._ownership_manager.set_desired_on(self.entry.entry_id, entity_id, desired_on)
		_LOGGER.debug(
			"[%s] %s → %s (%s)",
			entity_id, old_state.value, new_state.value, reason,
		)
		self._notify_switch(entity_id)

	def _cancel_entity_timer(self, entity_state: dict) -> None:
		"""Cancel any running off-timer / safety-timer for an entity."""
		timer = entity_state.get("off_timer")
		if timer is not None:
			timer.cancel()
			entity_state["off_timer"] = None

	def _should_external_change_pause(
		self, entity_id: str, cfg: dict, effective_new_state: str | None,
	) -> bool:
		"""Determine if an external state change should pause automation.

		Unified logic shared by _handle_controlled_entity_change and
		_handle_external_action.
		"""
		if CONF_MANUAL_DISABLE_STATES in cfg:
			manual_disable_states = cfg[CONF_MANUAL_DISABLE_STATES]
			should_pause = effective_new_state is not None and effective_new_state in manual_disable_states
			_LOGGER.debug(
				"Manual control: %s → %s (%s disable list) → %s",
				entity_id, effective_new_state,
				"in" if should_pause else "not in",
				"pause" if should_pause else "resume",
			)
			return should_pause
		else:
			# Legacy behaviour: cleared-state service pauses, detected-state resumes
			if effective_new_state == cfg.get(CONF_PRESENCE_CLEARED_STATE):
				return True
			return False

	async def _reconcile_entity(self, entity_id: str, entity_state: dict) -> None:
		"""Reconcile a single entity to the state it *should* be in given
		current room conditions.  Called after resume-from-pause, presence_allowed
		changes, and by the periodic safety net.
		"""
		if not self._presence_switch_allows_entity(entity_state):
			return

		cur = entity_state["state"]
		occupied = self._is_any_occupied()
		conditions_met = self._are_activation_conditions_met()
		clearing_clear = self._are_clearing_sensors_clear()

		if occupied and conditions_met:
			if cur not in (EntityAutomationState.OCCUPIED, EntityAutomationState.CLEARING, EntityAutomationState.SETTLING_ON):
				self._cancel_entity_timer(entity_state)
				self._cancel_entity_actuation(entity_state, "reconcile occupied")
				self._set_entity_state(entity_id, entity_state, EntityAutomationState.OCCUPIED, "reconcile: occupied + conditions met")
				await self._apply_service_intent(entity_id, entity_state, CONF_PRESENCE_DETECTED_SERVICE, IntentReason.PRESENCE)
				# If clearing sensors are already clear, start timer immediately
				if clearing_clear:
					await self._start_entity_off_timer(entity_id, entity_state)
		elif occupied and not conditions_met:
			# Conditions not met, but if the light is already on, treat as OCCUPIED
			# (conditions only gate *turning on*, not maintaining current state)
			current_ha_state = self.hass.states.get(entity_state["config"][CONF_ENTITY_ID])
			light_is_on = current_ha_state and current_ha_state.state == entity_state["config"].get(CONF_PRESENCE_DETECTED_STATE, "on")
			if light_is_on and cur not in (EntityAutomationState.OCCUPIED, EntityAutomationState.CLEARING, EntityAutomationState.SETTLING_ON):
				self._cancel_entity_timer(entity_state)
				self._cancel_entity_actuation(entity_state, "reconcile occupied light on")
				self._set_entity_state(entity_id, entity_state, EntityAutomationState.OCCUPIED, "reconcile: occupied, light already on")
				if clearing_clear:
					await self._start_entity_off_timer(entity_id, entity_state)
			elif not light_is_on and cur != EntityAutomationState.PENDING_ACTIVATION:
				self._cancel_entity_timer(entity_state)
				self._set_entity_state(entity_id, entity_state, EntityAutomationState.PENDING_ACTIVATION, "reconcile: occupied, conditions not met")
		else:
			# Room is empty
			if cur in (EntityAutomationState.OCCUPIED, EntityAutomationState.PENDING_ACTIVATION, EntityAutomationState.SETTLING_ON):
				# Start off-timer to turn off after delay
				if cur in (EntityAutomationState.OCCUPIED, EntityAutomationState.SETTLING_ON):
					await self._start_entity_off_timer(entity_id, entity_state)
				else:
					await self._apply_service_intent(entity_id, entity_state, CONF_PRESENCE_CLEARED_SERVICE, IntentReason.CLEARING)
			elif cur == EntityAutomationState.WAITING_FOR_CLEAR and clearing_clear:
				self._cancel_entity_timer(entity_state)
				await self._apply_service_intent(entity_id, entity_state, CONF_PRESENCE_CLEARED_SERVICE, IntentReason.CLEARING)
			elif cur == EntityAutomationState.IDLE:
				# Check if light is still on despite room being empty (e.g., after
				# re-enabling presence_allowed). Start the normal off-timer so startup
				# reconciliation keeps the configured delay semantics.
				current_ha_state = self.hass.states.get(entity_state["config"][CONF_ENTITY_ID])
				detected_state = entity_state["config"].get(CONF_PRESENCE_DETECTED_STATE, "on")
				if current_ha_state and current_ha_state.state == detected_state and clearing_clear:
					self._set_entity_state(entity_id, entity_state, EntityAutomationState.OCCUPIED, "reconcile: light on but room empty")
					await self._start_entity_off_timer(entity_id, entity_state)

	async def _periodic_reconciliation(self, _now: datetime) -> None:
		"""Safety-net called every _RECONCILIATION_INTERVAL.

		Catches any state inconsistency that slipped through event-driven
		handling (e.g., missed events, transient sensor blips).
		"""
		try:
			now = dt_util.utcnow()
			for entity_id, es in self._entity_states.items():
				if not self._presence_switch_allows_entity(es):
					continue

				cur = es["state"]

				# WAITING_FOR_CLEAR safety timeout
				if cur == EntityAutomationState.WAITING_FOR_CLEAR:
					if self._are_clearing_sensors_clear():
						_LOGGER.info(
							"[%s] Reconciliation: WAITING_FOR_CLEAR but sensors are clear → IDLE",
							entity_id,
						)
						self._cancel_entity_timer(es)
						await self._apply_service_intent(entity_id, es, CONF_PRESENCE_CLEARED_SERVICE, IntentReason.CLEARING)
					else:
						# Check if we've been waiting too long
						entered = es.get("state_entered_at")
						if entered and (now - entered).total_seconds() > _WAITING_FOR_CLEAR_MAX_SECONDS:
							self._cancel_entity_timer(es)
							# If presence sensors still show the room as occupied,
							# transition back to OCCUPIED instead of forcing IDLE.
							if self._is_any_occupied():
								_LOGGER.info(
									"[%s] WAITING_FOR_CLEAR for >%ds, but room still occupied → OCCUPIED",
									entity_id, _WAITING_FOR_CLEAR_MAX_SECONDS,
								)
								self._set_entity_state(entity_id, es, EntityAutomationState.OCCUPIED, "reconciliation: safety timeout but still occupied")
								await self._apply_service_intent(entity_id, es, CONF_PRESENCE_DETECTED_SERVICE, IntentReason.PRESENCE)
							else:
								_LOGGER.warning(
									"[%s] WAITING_FOR_CLEAR for >%ds, forcing IDLE (clearing sensors still not all clear)",
									entity_id, _WAITING_FOR_CLEAR_MAX_SECONDS,
								)
								await self._apply_service_intent(entity_id, es, CONF_PRESENCE_CLEARED_SERVICE, IntentReason.CLEARING, force=True)

				# CLEARING but timer somehow lost
				elif cur == EntityAutomationState.CLEARING and es.get("off_timer") is None:
					_LOGGER.warning(
						"[%s] Reconciliation: CLEARING but no timer running – restarting timer",
						entity_id,
					)
					await self._start_entity_off_timer(entity_id, es)

				# OCCUPIED but room is actually empty and clearing sensors clear
				elif cur == EntityAutomationState.OCCUPIED:
					if not self._is_any_occupied() and self._are_clearing_sensors_clear():
						_LOGGER.info(
							"[%s] Reconciliation: OCCUPIED but room empty + sensors clear → starting off-timer",
							entity_id,
						)
						await self._start_entity_off_timer(entity_id, es)

				# IDLE but room is occupied (missed a presence event?)
				elif cur == EntityAutomationState.IDLE:
					if self._is_any_occupied() and self._are_activation_conditions_met():
						_LOGGER.info(
							"[%s] Reconciliation: IDLE but room occupied + conditions met → OCCUPIED",
							entity_id,
						)
						await self._reconcile_entity(entity_id, es)
					elif self._are_clearing_sensors_clear():
						current_state = self.hass.states.get(entity_id)
						detected_state = es["config"].get(CONF_PRESENCE_DETECTED_STATE, STATE_ON)
						if current_state and current_state.state == detected_state:
							_LOGGER.info(
								"[%s] Reconciliation: IDLE but entity still on + room clear → cleared intent",
								entity_id,
							)
							await self._apply_service_intent(entity_id, es, CONF_PRESENCE_CLEARED_SERVICE, IntentReason.CLEARING)

				# PENDING but conditions are actually met
				elif cur == EntityAutomationState.PENDING_ACTIVATION:
					if self._are_activation_conditions_met() and self._is_any_occupied():
						_LOGGER.info(
							"[%s] Reconciliation: PENDING but conditions met → OCCUPIED",
							entity_id,
						)
						await self._reconcile_entity(entity_id, es)
					elif not self._is_any_occupied():
						self._set_entity_state(entity_id, es, EntityAutomationState.IDLE, "reconciliation: room empty")

		except Exception as err:
			_LOGGER.exception("Error in periodic reconciliation: %s", err)

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
			tracking = self._auto_reenable_tracking
			tracking["is_tracking"] = False
			tracking["window_start"] = None
			tracking["occupied_seconds"] = 0.0
			tracking["last_presence_change"] = None
			tracking["was_occupied"] = False
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
			if entity_state["state"] == EntityAutomationState.PAUSED:
				_LOGGER.info("Resuming automation for %s in %s", entity_id, room_name)
				self.set_automation_paused(entity_id, False)
				await self._reconcile_entity(entity_id, entity_state)

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

