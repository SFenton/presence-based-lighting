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
	STATE_OFF,
	STATE_ON,
)
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
	async_call_later,
	async_track_state_change_event,
	async_track_time_change,
	async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .command_context import CommandOrigin, get_command_context_registry
from .batch_observer import BlockedCommand, get_batch_observer
from .external_override import get_external_override_manager
from .const import (
	AUTOMATION_MODE_AUTOMATIC,
	AUTOMATION_MODE_PRESENCE_LOCK,
	ACTIVATION_CATCHUP_ANY_TRIGGER,
	ACTIVATION_CATCHUP_CLEARING_AUTHORITY,
	ACTIVATION_CATCHUP_NONE,
	AUTOMATION_CONTROL_STATE_ACTIVE,
	AUTOMATION_CONTROL_STATE_OFF,
	AUTOMATION_CONTROL_STATE_ON,
	AUTOMATION_CONTROL_STATE_PAUSED,
	AUTOMATION_CONTROL_STATE_QUIETED,
	BATCH_MODE_OFF,
	CONF_ACTIVATION_CONDITIONS,
	CONF_ACTIVATION_CATCHUP_MODE,
	CONF_AUTOMATION_MODE,
	CONF_BULK_COMMAND_POLICY,
	CONF_AUTO_REENABLE_END_TIME,
	CONF_AUTO_REENABLE_PRESENCE_SENSORS,
	CONF_AUTO_REENABLE_START_TIME,
	CONF_AUTO_REENABLE_VACANCY_THRESHOLD,
	CONF_CLEARING_SENSORS_AUTO_DISCOVERED,
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
	CONF_PRESENCE_CLEARED_TRANSITION,
	CONF_PRESENCE_DETECTED_BRIGHTNESS_PCT,
	CONF_PRESENCE_DETECTED_SERVICE,
	CONF_PRESENCE_DETECTED_TRANSITION,
	CONF_PRESENCE_DETECTED_STATE,
	CONF_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE,
	CONF_PRESENCE_SENSORS,
	CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
	CONF_REQUIRE_VACANCY_FOR_CLEARED,
	CONF_RESPECTS_PRESENCE_ALLOWED,
	CONF_RLC_TRACKING_ENTITY,
	CONF_ROOM_NAME,
	CONF_VACANCY_AUTHORITY_AUTO_DISCOVERED,
	CONF_VACANCY_AUTHORITY_SENSORS,
	CONF_BATCH_MIN_DISTINCT_ENTITIES,
	CONF_BATCH_RETAIN_SECONDS,
	CONF_BATCH_WINDOW_MS,
	CONF_HOMEKIT_BATCH_MODE,
	CONF_HONOR_EXTERNAL_OVERRIDE,
	CONF_QUIETED_MAX_AGE,
	CONF_QUIETED_MAX_AGE_ACTION,
	CONF_UNKNOWN_SOURCE_POLICY,
	DEFAULT_AUTOMATION_MODE,
	DEFAULT_ACTIVATION_CATCHUP_MODE,
	DEFAULT_BULK_COMMAND_POLICY,
	DEFAULT_AUTO_REENABLE_END_TIME,
	DEFAULT_AUTO_REENABLE_START_TIME,
	DEFAULT_AUTO_REENABLE_VACANCY_THRESHOLD,
	NO_ACTION,
	DEFAULT_BATCH_MIN_DISTINCT_ENTITIES,
	DEFAULT_BATCH_RETAIN_SECONDS,
	DEFAULT_BATCH_WINDOW_MS,
	DEFAULT_CLEARED_SERVICE,
	DEFAULT_CLEARED_STATE,
	DEFAULT_PRESENCE_CLEARED_TRANSITION,
	DEFAULT_DETECTED_SERVICE,
	DEFAULT_PRESENCE_DETECTED_BRIGHTNESS_PCT,
	DEFAULT_PRESENCE_DETECTED_TRANSITION,
	DEFAULT_DETECTED_STATE,
	DEFAULT_DISABLE_ON_EXTERNAL,
	DEFAULT_HOMEKIT_BATCH_MODE,
	DEFAULT_HONOR_EXTERNAL_OVERRIDE,
	DEFAULT_INITIAL_PRESENCE_ALLOWED,
	DEFAULT_MANUAL_DISABLE_STATES,
	DEFAULT_OFF_DELAY,
	DEFAULT_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE,
	DEFAULT_QUIETED_MAX_AGE,
	DEFAULT_QUIETED_MAX_AGE_ACTION,
	DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED,
	DEFAULT_REQUIRE_VACANCY_FOR_CLEARED,
	DEFAULT_RESPECTS_PRESENCE_ALLOWED,
	DEFAULT_UNKNOWN_SOURCE_POLICY,
	DOMAIN,
	ENABLE_FILE_LOGGING,
	EVENT_COMMAND_INTENT,
	EXTERNAL_POLICY_IGNORE,
	EXTERNAL_POLICY_PAUSE,
	EXTERNAL_POLICY_REARM_AFTER_CLEAR,
	PLATFORMS,
	SOURCE_HOMEKIT_BATCH,
	SOURCE_HOMEKIT_SINGLE,
	SOURCE_ADMIN,
	SOURCE_UNKNOWN,
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
	replace_entities_with_matching_rlc_sensors,
)
from .service_handlers import (
	SERVICE_PAUSE_AUTOMATION,
	SERVICE_RESUME_AUTOMATION,
	SERVICE_SET_AUTOMATION_STATE,
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
	  OCCUPIED ──clear conditions met──▶ CLEARING (timer started)
	  CLEARING ──timer fires + clear conditions met──▶ IDLE
	  CLEARING ──timer fires + clear conditions NOT met──▶ WAITING_FOR_CLEAR
	  CLEARING ──presence detected──▶ OCCUPIED
	  WAITING_FOR_CLEAR ──clear conditions met──▶ IDLE
	  WAITING_FOR_CLEAR ──presence detected──▶ OCCUPIED
	  WAITING_FOR_CLEAR ──safety timeout + room empty──▶ IDLE (forced)
	  WAITING_FOR_CLEAR ──safety timeout + room occupied──▶ OCCUPIED
	  PENDING_ACTIVATION ──conditions met──▶ OCCUPIED
	  PENDING_ACTIVATION ──room empties──▶ IDLE
	  PAUSED ──resume / state leaves disable list──▶ (reconciled)
	  Any state ──external manual control──▶ PAUSED
	  Any state ──bulk "all lights off"──▶ QUIETED
	  QUIETED ──room becomes vacant──▶ QUIETED (rearm latch armed, stays dark)
	  QUIETED ──rising presence AFTER vacancy──▶ OCCUPIED
	"""

	IDLE = "idle"
	OCCUPIED = "occupied"
	PENDING_ACTIVATION = "pending_activation"
	CLEARING = "clearing"
	WAITING_FOR_CLEAR = "waiting_for_clear"
	SETTLING_OFF = "settling_off"
	SETTLING_ON = "settling_on"
	PAUSED = "paused"
	QUIETED = "quieted"


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
_ACTUATION_RETRY_DELAYS_SECONDS = (1.0, 3.0, 7.0)
_ACTUATION_RETRY_JITTER_MAX_SECONDS = 0.75
_ACTUATION_MAX_ATTEMPTS = 3
_RLC_MIGRATION_RETRY_SECONDS = 30
_VACANCY_AUTHORITY_AUTOFILL_RETRY_SECONDS = 30
_UNTRUSTED_STARTUP_STATES = {"unavailable", "unknown"}
_BINARY_EFFECTIVE_STATES = {STATE_OFF, STATE_ON}
_EXPLICIT_PAUSE_SOURCES = {
	"admin",
	"presence_allowed",
	"service",
}
# Pause sources that originate from an external command on the controlled
# entity, and are therefore governed by the entity-scoped override record.
_EXTERNAL_PAUSE_SOURCES = {
	"external_state",
	"external_service",
	"external_override",
}

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


def _state_value(state: Any) -> str | None:
	if state is None:
		return None
	return getattr(state, "state", state)


def _is_untrusted_state_value(state: Any) -> bool:
	return _state_value(state) in _UNTRUSTED_STARTUP_STATES


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
	Version 7 -> 8: Add vacancy_authority_sensors for stable/fused clearing authority.
	Version 8 -> 9: Fold vacancy authority into clearing_sensors.
	Version 11 -> 12: Add per-entity bulk policy and safe quieted max-age action.
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

	if config_entry.version == 7:
		# Version 7 -> 8: Add vacancy_authority_sensors (empty preserves existing behavior).
		new_data = {**config_entry.data}
		if CONF_VACANCY_AUTHORITY_SENSORS not in new_data:
			new_data[CONF_VACANCY_AUTHORITY_SENSORS] = []

		hass.config_entries.async_update_entry(
			config_entry, data=new_data, version=8
		)
		_LOGGER.info(
			"Migration of entry %s from version 7 to 8 successful",
			config_entry.entry_id,
		)

	if config_entry.version == 8:
		# Version 8 -> 9: the stable room signal is the clearing sensor now.
		new_data = {**config_entry.data}
		legacy_authority = list(new_data.get(CONF_VACANCY_AUTHORITY_SENSORS, []) or [])
		legacy_auto_discovered = bool(
			new_data.get(CONF_VACANCY_AUTHORITY_AUTO_DISCOVERED, False)
		)

		if legacy_authority:
			new_data[CONF_CLEARING_SENSORS] = legacy_authority
			new_data[CONF_CLEARING_SENSORS_AUTO_DISCOVERED] = legacy_auto_discovered
		elif CONF_CLEARING_SENSORS_AUTO_DISCOVERED not in new_data:
			# Preserve a manual opt-out from the legacy auto-discovered authority.
			new_data[CONF_CLEARING_SENSORS_AUTO_DISCOVERED] = legacy_auto_discovered

		new_data.pop(CONF_VACANCY_AUTHORITY_SENSORS, None)
		new_data.pop(CONF_VACANCY_AUTHORITY_AUTO_DISCOVERED, None)

		hass.config_entries.async_update_entry(
			config_entry, data=new_data, version=9
		)
		_LOGGER.info(
			"Migration of entry %s from version 8 to 9 successful",
			config_entry.entry_id,
		)

	if config_entry.version == 9:
		# Version 9 -> 10: Presence Lock now respects manual/pause overrides by default.
		new_data = {**config_entry.data}
		controlled_entities = new_data.get(CONF_CONTROLLED_ENTITIES, [])
		updated_entities = []

		for entity_config in controlled_entities:
			updated_config = {**entity_config}
			updated_config.setdefault(
				CONF_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE,
				DEFAULT_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE,
			)
			updated_entities.append(updated_config)

		new_data[CONF_CONTROLLED_ENTITIES] = updated_entities
		hass.config_entries.async_update_entry(
			config_entry,
			data=new_data,
			version=10,
		)
		_LOGGER.info(
			"Migration of entry %s from version 9 to 10 successful",
			config_entry.entry_id,
		)

	if config_entry.version == 10:
		# Version 10 -> 11: entity-scoped external overrides and bulk command
		# detection. An external override is a fact about the controlled entity,
		# so every entry controlling it consults the same record by default -
		# otherwise a paired profile whose activation gate was closed at command
		# time resurrects the light the moment the gate flips. Entries that must
		# keep the old entry-local behaviour can set honor_external_override to
		# false explicitly.
		new_data = {**config_entry.data}
		controlled_entities = new_data.get(CONF_CONTROLLED_ENTITIES, [])
		updated_entities = []

		for entity_config in controlled_entities:
			updated_config = {**entity_config}
			updated_config.setdefault(
				CONF_HONOR_EXTERNAL_OVERRIDE,
				DEFAULT_HONOR_EXTERNAL_OVERRIDE,
			)
			updated_config.setdefault(
				CONF_UNKNOWN_SOURCE_POLICY,
				DEFAULT_UNKNOWN_SOURCE_POLICY,
			)
			updated_config.setdefault(CONF_QUIETED_MAX_AGE, DEFAULT_QUIETED_MAX_AGE)
			updated_entities.append(updated_config)

		new_data[CONF_CONTROLLED_ENTITIES] = updated_entities
		new_data.setdefault(CONF_HOMEKIT_BATCH_MODE, DEFAULT_HOMEKIT_BATCH_MODE)
		new_data.setdefault(CONF_BATCH_WINDOW_MS, DEFAULT_BATCH_WINDOW_MS)
		new_data.setdefault(CONF_BATCH_RETAIN_SECONDS, DEFAULT_BATCH_RETAIN_SECONDS)
		new_data.setdefault(
			CONF_BATCH_MIN_DISTINCT_ENTITIES,
			DEFAULT_BATCH_MIN_DISTINCT_ENTITIES,
		)
		hass.config_entries.async_update_entry(
			config_entry,
			data=new_data,
			version=11,
		)
		_LOGGER.info(
			"Migration of entry %s from version 10 to 11 successful",
			config_entry.entry_id,
		)

	if config_entry.version == 11:
		new_data = {**config_entry.data}
		controlled_entities = new_data.get(CONF_CONTROLLED_ENTITIES, [])
		updated_entities = []

		for entity_config in controlled_entities:
			updated_config = {**entity_config}
			updated_config.setdefault(
				CONF_BULK_COMMAND_POLICY,
				DEFAULT_BULK_COMMAND_POLICY,
			)
			updated_config.setdefault(
				CONF_QUIETED_MAX_AGE_ACTION,
				DEFAULT_QUIETED_MAX_AGE_ACTION,
			)
			updated_entities.append(updated_config)

		new_data[CONF_CONTROLLED_ENTITIES] = updated_entities
		hass.config_entries.async_update_entry(
			config_entry,
			data=new_data,
			version=12,
		)
		_LOGGER.info(
			"Migration of entry %s from version 11 to 12 successful",
			config_entry.entry_id,
		)

	return True


def _get_exact_room_aod_clearing_sensor(hass: HomeAssistant, room_name: str) -> str | None:
	"""Return an exact room-level occupancy status entity for clearing."""
	room_slug = slugify_entity_id(room_name)
	candidates = (
		f"sensor.{room_slug}_{room_slug}_occupancy_status_last_changed",
		f"sensor.{room_slug}_occupancy_status_last_changed",
		f"binary_sensor.{room_slug}_{room_slug}_occupancy_status",
		f"binary_sensor.{room_slug}_occupancy_status",
	)
	for candidate in candidates:
		if hass.states.get(candidate) is not None:
			return candidate
	return None


def _autofill_aod_clearing_sensors(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Persist exact room-level AOD occupancy status as the clearing sensor."""
	current = list(entry.data.get(CONF_CLEARING_SENSORS, []) or [])
	auto_discovered = bool(entry.data.get(CONF_CLEARING_SENSORS_AUTO_DISCOVERED, False))
	if not current and auto_discovered:
		return False
	if current and not auto_discovered:
		return False

	room_name = entry.data.get(CONF_ROOM_NAME, "")
	clearing_sensor = _get_exact_room_aod_clearing_sensor(hass, room_name)
	if not clearing_sensor:
		return False
	if current == [clearing_sensor] and auto_discovered:
		return False

	new_data = {**entry.data}
	new_data[CONF_CLEARING_SENSORS] = [clearing_sensor]
	new_data[CONF_CLEARING_SENSORS_AUTO_DISCOVERED] = True
	new_data.pop(CONF_VACANCY_AUTHORITY_SENSORS, None)
	new_data.pop(CONF_VACANCY_AUTHORITY_AUTO_DISCOVERED, None)
	hass.config_entries.async_update_entry(entry, data=new_data)
	_LOGGER.info(
		"Auto-filled %s as the clearing sensor for %s",
		clearing_sensor,
		room_name or entry.entry_id,
	)
	return True


def _schedule_aod_clearing_autofill_retry(hass: HomeAssistant, entry: ConfigEntry) -> None:
	"""Retry AOD clearing auto-fill after startup so helper entities can load."""

	@callback
	def _retry_aod_clearing_autofill(_now: datetime) -> None:
		_autofill_aod_clearing_sensors(hass, entry)

	unsub = async_call_later(
		hass,
		_VACANCY_AUTHORITY_AUTOFILL_RETRY_SECONDS,
		_retry_aod_clearing_autofill,
	)
	entry.async_on_unload(unsub)


def _migrate_configured_sensors_to_rlc(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Persistently replace configured raw sensors with matching RLC sensors."""
	new_data = {**entry.data}
	replacements: dict[str, str] = {}

	for key in (
		CONF_PRESENCE_SENSORS,
		CONF_CLEARING_SENSORS,
		CONF_AUTO_REENABLE_PRESENCE_SENSORS,
	):
		current = list(new_data.get(key, []) or [])
		updated, key_replacements = replace_entities_with_matching_rlc_sensors(
			hass, current, valid_effective_states=_BINARY_EFFECTIVE_STATES
		)
		if updated != current:
			new_data[key] = updated
			replacements.update(key_replacements)

	if not replacements:
		return False

	hass.config_entries.async_update_entry(entry, data=new_data)
	_LOGGER.info(
		"Migrated %s Presence Based Lighting sensors to RLC equivalents: %s",
		entry.data.get(CONF_ROOM_NAME, entry.entry_id),
		replacements,
	)
	return True


def _schedule_rlc_sensor_migration_retry(hass: HomeAssistant, entry: ConfigEntry) -> None:
	"""Retry RLC migration after startup so RLC entities have time to load."""

	@callback
	def _retry_rlc_migration(_now: datetime) -> None:
		_migrate_configured_sensors_to_rlc(hass, entry)

	unsub = async_call_later(hass, _RLC_MIGRATION_RETRY_SECONDS, _retry_rlc_migration)
	entry.async_on_unload(unsub)


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

		if not _migrate_configured_sensors_to_rlc(hass, entry):
			_schedule_rlc_sensor_migration_retry(hass, entry)

		if not _autofill_aod_clearing_sensors(hass, entry):
			_schedule_aod_clearing_autofill_retry(hass, entry)

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
		self._command_context_registry = get_command_context_registry(hass)
		self._override_manager = get_external_override_manager(hass)
		self._batch_observer = get_batch_observer(hass)
		self._batch_unsub: Callable[[], None] | None = None
		self._batch_replay_tasks: set[asyncio.Task] = set()
		self._paused_state_save_lock = asyncio.Lock()
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
					"context_targets": {},
					"off_timer": None,
					"work_generation": 0,
					"intent": self._new_entity_intent(),
					"actuation": self._new_actuation_state(),
					"pause": None,
					"last_effective_state": None,  # Track RLC effective state for change detection
					"last_observed_state": None,  # Last trusted direct state, for redundancy checks
				}
				self._ownership_manager.register_entity(self.entry.entry_id, entity_id)
				self._batch_observer.register_managed_entity(self.entry.entry_id, entity_id)
				self._override_manager.register_entity(
					self.entry.entry_id,
					entity_id,
					self._handle_external_override_changed,
					bulk_policy=entity.get(
						CONF_BULK_COMMAND_POLICY,
						DEFAULT_BULK_COMMAND_POLICY,
					),
					max_age_seconds=entity.get(
						CONF_QUIETED_MAX_AGE,
						DEFAULT_QUIETED_MAX_AGE,
					),
					max_age_action=entity.get(
						CONF_QUIETED_MAX_AGE_ACTION,
						DEFAULT_QUIETED_MAX_AGE_ACTION,
					),
				)

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
			"presence_lock_override": False,
			"updated_at": dt_util.utcnow(),
		}

	def _new_actuation_state(self) -> dict:
		return {
			"status": ActuationStatus.IDLE,
			"target_state": None,
			"service_key": None,
			"generation": None,
			"context_ids": deque(maxlen=10),
			"attempts": 0,
			"force_service_call": False,
			"dispatching": False,
			"timer": None,
			"next_retry_delay": None,
			"last_observed_state": None,
			"last_error": None,
			"updated_at": dt_util.utcnow(),
		}

	def _actuation_generation_is_current(self, entity_state: dict) -> bool:
		"""Return whether the actuation belongs to current delayed work."""
		generation = entity_state["actuation"].get("generation")
		return (
			generation is None
			or generation == self._entity_work_generation(entity_state)
		)

	def _actuation_is_currently_pending(self, entity_state: dict) -> bool:
		"""Return whether a live current-generation actuation is pending."""
		actuation = entity_state["actuation"]
		return (
			actuation["status"] == ActuationStatus.PENDING
			and self._actuation_generation_is_current(entity_state)
			and (
				bool(actuation.get("dispatching"))
				or actuation.get("timer") is not None
			)
		)

	async def async_start(self) -> None:
		"""Begin tracking sensors and controlled entities."""

		try:
			_LOGGER.debug("Starting coordinator for entry: %s", self.entry.entry_id)

			# Set up hass-interceptor for normalization and Presence Lock.
			self._interceptor = PresenceLockInterceptor(
				self.hass,
				self.entry,
				self._is_any_occupied,
				self._entity_may_enforce_presence_lock,
				entry_is_active_func=self._entry_is_active,
				is_clearing_authority_occupied_func=(
					self._is_clearing_authority_occupied
				),
				classify_command_context_func=self._classify_interceptor_command,
				handle_blocked_command_func=self._handle_blocked_interceptor_command,
			)
			interceptors_registered = self._interceptor.setup()
			self._using_interceptor = (
				self._interceptor.has_presence_lock_interceptors
			)

			if interceptors_registered:
				_LOGGER.info(
					"Using hass-interceptor for pre-dispatch service handling"
				)
			elif is_interceptor_available():
				_LOGGER.debug(
					"hass-interceptor available but no eligible entities configured"
				)
			else:
				_LOGGER.debug(
					"hass-interceptor not installed, using fallback (reactive reversion)"
				)

			controlled_ids = list(self._entity_states.keys())
			presence_sensors = self.entry.data.get(CONF_PRESENCE_SENSORS, [])
			clearing_sensors = self.entry.data.get(CONF_CLEARING_SENSORS, [])
			legacy_authority_sensors = self.entry.data.get(CONF_VACANCY_AUTHORITY_SENSORS, [])
			if legacy_authority_sensors and (
				not clearing_sensors
				or self.entry.data.get(CONF_VACANCY_AUTHORITY_AUTO_DISCOVERED)
			):
				clearing_sensors = legacy_authority_sensors
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
					rlc_state = self._get_valid_rlc_effective_state(entity_id, entity_state)
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
				else:
					# Seed the observed state for non-RLC entities too, so a
					# redundant external command aimed at an entity that is
					# already in the target state cannot manufacture a pause.
					current = self.hass.states.get(entity_id)
					if current is not None and not _is_untrusted_state_value(current):
						entity_state["last_observed_state"] = current.state

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
				_LOGGER.debug("Registered state change listener for presence/clearing/vacancy authority sensors")

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

			# Bulk ("all lights off") detection is domain-wide: a single listener
			# and a single batch view are shared by every config entry, because a
			# whole-home command by definition spans rooms.
			self._configure_batch_observer()
			if self._batch_observer.mode != BATCH_MODE_OFF:
				self._batch_observer.async_start(self.entry.entry_id)
				self._batch_unsub = self._batch_observer.subscribe_confirmed(
					self._handle_batch_confirmed
				)
				_LOGGER.debug(
					"Bulk command detection active (effective config: %s)",
					self._batch_observer.effective_config,
				)
			else:
				_LOGGER.debug("Bulk command detection disabled by configuration")
			if self._using_interceptor:
				_LOGGER.info(
					"Presence Lock interceptor is active; early blocked HomeKit batch "
					"commands will be replayed after batch confirmation"
				)

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

			await self._load_paused_state()

			# Restore persisted suppression before an overdue reset evaluates it.
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
			self._command_context_registry.unregister_entry(self.entry.entry_id)
			self._override_manager.unregister_entry(self.entry.entry_id)
			# Drop our batch-confirmation callback before releasing the shared
			# observer, so a partial unload never leaves a stale subscription
			# bound to a torn-down coordinator.
			if self._batch_unsub is not None:
				self._batch_unsub()
				self._batch_unsub = None
			self._batch_observer.unregister_entry(self.entry.entry_id)
			for task in self._batch_replay_tasks:
				task.cancel()
			self._batch_replay_tasks.clear()

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
					entity_state["pause"] = None
					self._set_entity_state(
						entity_id,
						entity_state,
						EntityAutomationState.IDLE,
						"presence_allowed restored enabled",
					)
			else:
				self._cancel_entity_timer(entity_state)
				entity_state["pause"] = self._build_pause_metadata(
					entity_id,
					entity_state,
					"presence_allowed restored disabled",
					"presence_allowed",
				)
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
		pause = entity_state.get("pause") or {}
		override = self._override_manager.get(entity_id)
		suppressed = entity_state["state"] in (
			EntityAutomationState.PAUSED,
			EntityAutomationState.QUIETED,
		)
		return {
			"desired_state": intent["desired"].value,
			"desired_target_state": intent["target_state"],
			"intent_reason": intent["reason"].value,
			"intent_authority": intent["authority"],
			"actuation_status": actuation["status"].value,
			"actuation_target_state": actuation["target_state"],
			"actuation_attempts": actuation["attempts"],
			"actuation_next_retry_delay": actuation.get("next_retry_delay"),
			"actuation_last_observed_state": actuation["last_observed_state"],
			"actuation_last_error": actuation["last_error"],
			"pause_source": pause.get("source"),
			"pause_reason": pause.get("reason"),
			"pause_paused_at": pause.get("paused_at"),
			"activation_conditions_met": self._entry_is_active(),
			"quieted": entity_state["state"] == EntityAutomationState.QUIETED,
			"automation_suppressed": suppressed,
			"suppression_kind": entity_state["state"].value if suppressed else None,
			"bulk_command_policy": self._override_manager.bulk_policy_for(entity_id),
			"external_override_policy": override.policy if override else None,
			"external_override_source": override.source if override else None,
			"external_override_reason": override.reason if override else None,
			"external_override_at": override.created_at if override else None,
			"external_override_expires_at": override.expires_at() if override else None,
			"external_override_batch_id": override.batch_id if override else None,
			"external_override_batch_size": override.batch_size if override else 0,
			"rearm_latched": bool(override.rearm_latched) if override else False,
			"rearm_latched_at": override.rearm_latched_at if override else None,
			"rearm_armed_by": override.rearm_armed_by if override else None,
			"quieted_max_age_action": override.max_age_action if override else None,
			"quieted_max_age_reached_at": override.max_age_reached_at if override else None,
			"unknown_source_count": self._override_manager.unknown_source_count(entity_id),
			"homekit_batch_mode": self._batch_observer.mode,
		}

	def _valid_effective_states_for_entity(self, entity_state: dict) -> set[str]:
		config = entity_state["config"]
		states = {
			config.get(CONF_PRESENCE_DETECTED_STATE, DEFAULT_DETECTED_STATE),
			config.get(CONF_PRESENCE_CLEARED_STATE, DEFAULT_CLEARED_STATE),
		}
		states.update(config.get(CONF_MANUAL_DISABLE_STATES, DEFAULT_MANUAL_DISABLE_STATES) or [])
		return {state for state in states if state is not None}

	def _get_valid_rlc_effective_state(self, entity_id: str, entity_state: dict) -> str | None:
		rlc_tracking_entity = entity_state["config"].get(CONF_RLC_TRACKING_ENTITY)
		if not rlc_tracking_entity:
			return None

		effective_state = get_effective_state(self.hass, rlc_tracking_entity)
		if effective_state is None:
			return None
		if effective_state not in self._valid_effective_states_for_entity(entity_state):
			_LOGGER.warning(
				"RLC tracking entity %s for %s has invalid effective state %s; ignoring RLC value",
				rlc_tracking_entity,
				entity_id,
				effective_state,
			)
			return None
		return effective_state

	def _build_pause_metadata(self, entity_id: str, entity_state: dict, reason: str, source: str) -> dict:
		return {
			"entity_id": entity_id,
			"source": source,
			"reason": reason,
			"paused_at": dt_util.utcnow().isoformat(),
			"controlled_state": self._get_trusted_effective_controlled_state(entity_state),
			"room_occupied": self._is_any_occupied(),
			"clearing_clear": self._can_clear_room(),
		}

	def _pause_metadata_from_storage(self, entity_id: str, data: dict) -> dict:
		paused = data.get("paused")
		if isinstance(paused, dict) and isinstance(paused.get(entity_id), dict):
			metadata = dict(paused[entity_id])
			metadata.setdefault("entity_id", entity_id)
			metadata.setdefault("source", "legacy")
			return metadata
		return {
			"entity_id": entity_id,
			"source": "legacy",
			"reason": "legacy pause restored",
			"paused_at": data.get("saved_at"),
		}

	def _should_restore_pause(self, entity_id: str, entity_state: dict, metadata: dict) -> bool:
		source = metadata.get("source") or "legacy"
		if source in _EXPLICIT_PAUSE_SOURCES:
			return True
		if not self._presence_switch_allows_entity(entity_state):
			return True

		current_state = self._get_trusted_effective_controlled_state(entity_state)
		if current_state is None:
			return True
		if not self._manual_disable_state_matches(entity_state, current_state):
			_LOGGER.info(
				"Clearing restored pause for %s: controlled state %s is not a manual-disable state",
				entity_id,
				current_state,
			)
			return False
		if self._is_any_occupied() or not self._are_clearing_sensors_clear():
			return True

		_LOGGER.info(
			"Clearing stale restored pause for %s: room is clear and controlled state is already %s",
			entity_id,
			current_state,
		)
		return False

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
		presence_lock_override: bool = False,
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
				"presence_lock_override": presence_lock_override,
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

	def _recover_entity_after_invalid_actuation(
		self,
		entity_id: str,
		entity_state: dict,
	) -> None:
		"""Return a canceled settling state to an event-recoverable state."""
		cur = entity_state["state"]
		if cur not in (
			EntityAutomationState.SETTLING_OFF,
			EntityAutomationState.SETTLING_ON,
		):
			return

		occupied = self._is_any_occupied()
		if cur == EntityAutomationState.SETTLING_OFF:
			if occupied:
				new_state = (
					EntityAutomationState.OCCUPIED
					if self._entry_is_active()
					else EntityAutomationState.PENDING_ACTIVATION
				)
			elif self._can_clear_room():
				new_state = EntityAutomationState.IDLE
			else:
				new_state = EntityAutomationState.WAITING_FOR_CLEAR
		elif occupied:
			new_state = (
				EntityAutomationState.OCCUPIED
				if self._entry_is_active()
				else EntityAutomationState.PENDING_ACTIVATION
			)
		else:
			new_state = EntityAutomationState.IDLE

		self._set_entity_state(
			entity_id,
			entity_state,
			new_state,
			"recover canceled actuation",
		)

	async def _recover_taskless_pending_actuation(
		self,
		entity_id: str,
		entity_state: dict,
	) -> bool:
		"""Re-dispatch a PENDING actuation that has no live task."""
		actuation = entity_state["actuation"]
		if (
			actuation["status"] != ActuationStatus.PENDING
			or self._actuation_is_currently_pending(entity_state)
		):
			return False

		service_key = actuation.get("service_key")
		if not service_key:
			self._cancel_entity_actuation(
				entity_state,
				"taskless pending actuation",
			)
			self._recover_entity_after_invalid_actuation(
				entity_id,
				entity_state,
			)
			return True

		intent = entity_state["intent"]
		reason = intent.get("reason", IntentReason.NONE)
		if reason == IntentReason.NONE:
			reason = (
				IntentReason.PRESENCE
				if service_key == CONF_PRESENCE_DETECTED_SERVICE
				else IntentReason.CLEARING
			)
		_LOGGER.warning(
			"[%s] Re-dispatching taskless pending actuation",
			entity_id,
		)
		await self._apply_service_intent(
			entity_id,
			entity_state,
			service_key,
			reason,
			force=bool(intent.get("force")),
			presence_lock_override=bool(
				intent.get("presence_lock_override")
			),
		)
		return True

	def _intent_for_service(
		self, entity_id: str, entity_state: dict, service_key: str, reason: IntentReason,
		force: bool = False, presence_lock_override: bool = False,
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
		elif (
			not presence_lock_override
			and not self._presence_switch_allows_entity(entity_state)
		):
			authority = False
			intent_reason = IntentReason.DISABLED
		elif (
			not presence_lock_override
			and entity_state["state"] == EntityAutomationState.PAUSED
		):
			authority = False
			intent_reason = IntentReason.PAUSED
		elif (
			service_key == CONF_PRESENCE_DETECTED_SERVICE
			and not self._entry_is_active()
		):
			authority = False
			intent_reason = IntentReason.CONDITIONS
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
			presence_lock_override,
		)

	async def _apply_service_intent(
		self, entity_id: str, entity_state: dict, service_key: str, reason: IntentReason,
		force: bool = False, presence_lock_override: bool = False,
	) -> bool:
		intent = self._intent_for_service(
			entity_id,
			entity_state,
			service_key,
			reason,
			force,
			presence_lock_override,
		)
		return await self._apply_intent(entity_id, entity_state, intent)

	async def _apply_intent(self, entity_id: str, entity_state: dict, intent: dict) -> bool:
		if not intent["authority"] or not intent["service_key"]:
			self._cancel_entity_actuation(entity_state, intent["reason"].value)
			if (
				intent["desired"] == DesiredState.CLEARED
				and intent["reason"] in (IntentReason.OWNERSHIP, IntentReason.NO_ACTION)
				and entity_state["state"] != EntityAutomationState.PAUSED
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
			self._actuation_is_currently_pending(entity_state)
			and actuation["target_state"] == intent["target_state"]
			and actuation["service_key"] == intent["service_key"]
			and not intent.get("force")
		):
			if (
				intent["desired"] == DesiredState.CLEARED
				and entity_state["state"] not in (
					EntityAutomationState.SETTLING_OFF,
					EntityAutomationState.PAUSED,
				)
			):
				self._set_entity_state(entity_id, entity_state, EntityAutomationState.SETTLING_OFF, "pending cleared intent")
			return True

		await self._begin_entity_actuation(entity_id, entity_state, intent)
		return True

	async def _begin_entity_actuation(self, entity_id: str, entity_state: dict, intent: dict) -> None:
		config = entity_state["config"]
		service_key = intent["service_key"]
		target_state = intent["target_state"]
		previous_actuation = entity_state["actuation"]
		overrides_opposing_actuation = (
			previous_actuation["status"] == ActuationStatus.PENDING
			and previous_actuation["target_state"] is not None
			and previous_actuation["target_state"] != target_state
		)
		force_service_call = bool(intent.get("force")) or overrides_opposing_actuation

		self._cancel_entity_actuation(entity_state, "new intent")
		generation = self._bump_entity_work_generation(entity_state, "new actuation intent")
		actuation = entity_state["actuation"]
		actuation.update(
			{
				"status": ActuationStatus.PENDING,
				"target_state": target_state,
				"service_key": service_key,
				"generation": generation,
				"context_ids": deque(maxlen=10),
				"attempts": 0,
				"force_service_call": force_service_call,
				"dispatching": False,
				"next_retry_delay": None,
				"last_observed_state": None,
				"last_error": None,
				"updated_at": dt_util.utcnow(),
			}
		)

		if (
			service_key == CONF_PRESENCE_CLEARED_SERVICE
			and entity_state["state"] != EntityAutomationState.PAUSED
		):
			self._set_entity_state(entity_id, entity_state, EntityAutomationState.SETTLING_OFF, "actuating cleared intent")
		elif entity_state["state"] == EntityAutomationState.IDLE:
			self._set_entity_state(entity_id, entity_state, EntityAutomationState.SETTLING_ON, "actuating detected intent")

		current_state = self.hass.states.get(entity_id)
		if current_state and current_state.state == target_state and not force_service_call:
			self._confirm_entity_actuation(entity_id, entity_state, target_state)
			return

		if config[service_key] == NO_ACTION:
			self._confirm_entity_actuation(entity_id, entity_state, target_state)
			return

		await self._send_entity_actuation_attempt(entity_id, entity_state)

	def _cleared_intent_blocked_by_presence(self, entity_state: dict) -> bool:
		cfg = entity_state["config"]
		if self.entry.data.get(CONF_CLEARING_SENSORS):
			return False
		return (
			cfg.get(CONF_REQUIRE_VACANCY_FOR_CLEARED, DEFAULT_REQUIRE_VACANCY_FOR_CLEARED)
			and self._is_any_occupied()
		)

	def _actuation_target_is_still_valid(self, entity_id: str, entity_state: dict) -> bool:
		intent = entity_state["intent"]
		actuation = entity_state["actuation"]
		if not self._actuation_generation_is_current(entity_state):
			return False
		if not intent["authority"]:
			return False
		if intent["target_state"] != actuation["target_state"]:
			return False
		if intent["service_key"] != actuation["service_key"]:
			return False
		presence_lock_override = bool(intent.get("presence_lock_override"))
		if (
			not presence_lock_override
			and entity_state["state"] == EntityAutomationState.PAUSED
		):
			return False
		if (
			not presence_lock_override
			and not self._presence_switch_allows_entity(entity_state)
		):
			return False
		if intent["desired"] == DesiredState.CLEARED:
			if self._ownership_manager.other_entry_wants_on(self.entry.entry_id, entity_id):
				return False
			if not intent.get("force"):
				if not self._can_clear_room():
					return False
				if self._cleared_intent_blocked_by_presence(entity_state):
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
				"generation": self._entity_work_generation(entity_state),
				"attempts": 0,
				"force_service_call": False,
				"dispatching": False,
				"next_retry_delay": None,
				"last_error": reason,
				"updated_at": dt_util.utcnow(),
			}
		)

	def _schedule_actuation_timer(self, entity_id: str, entity_state: dict, delay: float, retry: bool) -> None:
		self._cancel_actuation_timer(entity_state)
		generation = entity_state["actuation"].get("generation")
		if retry:
			task = asyncio.create_task(
				self._execute_actuation_retry_timer(entity_id, entity_state, delay, generation)
			)
		else:
			task = asyncio.create_task(
				self._execute_actuation_confirmation_timer(entity_id, entity_state, delay, generation)
			)
		entity_state["actuation"]["timer"] = task

	def _actuation_retry_delay(self, entity_id: str, attempt_number: int) -> float:
		delay = _ACTUATION_RETRY_DELAYS_SECONDS[
			min(max(attempt_number - 1, 0), len(_ACTUATION_RETRY_DELAYS_SECONDS) - 1)
		]
		jitter_slots = int(_ACTUATION_RETRY_JITTER_MAX_SECONDS * 1000)
		if jitter_slots <= 0:
			return delay
		stable_hash = sum(ord(ch) for ch in entity_id) + attempt_number * 97
		jitter = (stable_hash % (jitter_slots + 1)) / 1000
		return delay + jitter

	def _build_service_data(
		self,
		entity_id: str,
		entity_state: dict,
		service_key: str,
	) -> dict:
		"""Build typed service data for one PBL-owned action."""
		config = entity_state["config"]
		service = config[service_key]
		service_data = {"entity_id": entity_id}
		if entity_state["domain"] != "light":
			return service_data

		if (
			service_key == CONF_PRESENCE_DETECTED_SERVICE
			and service == DEFAULT_DETECTED_SERVICE
		):
			service_data["brightness_pct"] = config.get(
				CONF_PRESENCE_DETECTED_BRIGHTNESS_PCT,
				DEFAULT_PRESENCE_DETECTED_BRIGHTNESS_PCT,
			)
			service_data["transition"] = config.get(
				CONF_PRESENCE_DETECTED_TRANSITION,
				DEFAULT_PRESENCE_DETECTED_TRANSITION,
			)
		elif (
			service_key == CONF_PRESENCE_CLEARED_SERVICE
			and service == DEFAULT_CLEARED_SERVICE
		):
			try:
				transition = float(
					config.get(
						CONF_PRESENCE_CLEARED_TRANSITION,
						DEFAULT_PRESENCE_CLEARED_TRANSITION,
					)
				)
			except (TypeError, ValueError):
				transition = DEFAULT_PRESENCE_CLEARED_TRANSITION
			if transition > 0:
				service_data["transition"] = transition
		return service_data

	@staticmethod
	def _actuation_confirmation_delay(service_data: dict) -> float:
		"""Allow configured transitions to settle before checking convergence."""
		try:
			transition = float(service_data.get("transition", 0))
		except (TypeError, ValueError):
			transition = 0
		return max(_ACTUATION_CONFIRMATION_SECONDS, transition + 1)

	async def _execute_actuation_confirmation_timer(
		self, entity_id: str, entity_state: dict, delay: float, generation: int | None = None,
	) -> None:
		this_task = asyncio.current_task()
		try:
			await asyncio.sleep(delay)
			if not self._entity_work_generation_matches(entity_id, entity_state, generation, "actuation confirmation"):
				return
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
		self, entity_id: str, entity_state: dict, delay: float, generation: int | None = None,
	) -> None:
		this_task = asyncio.current_task()
		try:
			await asyncio.sleep(delay)
			await self._send_entity_actuation_attempt(entity_id, entity_state, generation)
		except asyncio.CancelledError:
			_LOGGER.debug("[%s] Actuation retry timer cancelled", entity_id)
		except Exception as err:
			_LOGGER.exception("[%s] Error in actuation retry timer: %s", entity_id, err)
		finally:
			if entity_state["actuation"].get("timer") is this_task:
				entity_state["actuation"]["timer"] = None

	async def _send_entity_actuation_attempt(
		self, entity_id: str, entity_state: dict, generation: int | None = None,
	) -> None:
		actuation = entity_state["actuation"]
		if generation is None:
			generation = actuation.get("generation")
		if not self._entity_work_generation_matches(entity_id, entity_state, generation, "actuation attempt"):
			return
		if actuation["status"] != ActuationStatus.PENDING:
			return

		if not self._actuation_target_is_still_valid(entity_id, entity_state):
			self._cancel_entity_actuation(entity_state, "intent no longer valid")
			self._recover_entity_after_invalid_actuation(entity_id, entity_state)
			return

		current_state = self.hass.states.get(entity_id)
		observed = current_state.state if current_state else None
		actuation["last_observed_state"] = observed
		if observed == actuation["target_state"] and not actuation.get("force_service_call"):
			actuation["next_retry_delay"] = None
			self._schedule_actuation_timer(entity_id, entity_state, _ACTUATION_CONFIRMATION_SECONDS, retry=False)
			return
		actuation["force_service_call"] = False

		if actuation["attempts"] >= _ACTUATION_MAX_ATTEMPTS:
			self._fail_entity_actuation(entity_id, entity_state, observed)
			return

		config = entity_state["config"]
		service_key = actuation["service_key"]
		service = config[service_key]
		context = Context()
		actuation["attempts"] += 1
		actuation["updated_at"] = dt_util.utcnow()
		if not config.get(CONF_RLC_TRACKING_ENTITY):
			entity_state["last_effective_state"] = actuation["target_state"]

		if not self._entity_work_generation_matches(entity_id, entity_state, generation, "actuation service call"):
			return
		if not self._actuation_target_is_still_valid(entity_id, entity_state):
			self._cancel_entity_actuation(entity_state, "intent no longer valid")
			self._recover_entity_after_invalid_actuation(entity_id, entity_state)
			return
		self._register_command_context(
			entity_id,
			context,
			actuation["target_state"],
		)
		actuation["context_ids"].append(context.id)

		_LOGGER.debug(
			"Calling service %s.%s for entity %s (actuation attempt %d/%d, target=%s)",
			entity_state["domain"],
			service,
			entity_id,
			actuation["attempts"],
			_ACTUATION_MAX_ATTEMPTS,
			actuation["target_state"],
		)
		dispatch_token = object()
		actuation["dispatching"] = dispatch_token
		service_data = self._build_service_data(
			entity_id,
			entity_state,
			service_key,
		)
		try:
			await self.hass.services.async_call(
				entity_state["domain"],
				service,
				service_data,
				blocking=True,
				context=context,
			)
		except asyncio.CancelledError:
			raise
		except Exception as err:
			if actuation.get("dispatching") is not dispatch_token:
				return
			actuation["last_error"] = str(err)
			actuation["updated_at"] = dt_util.utcnow()
			_LOGGER.warning(
				"[%s] Actuation service call failed: %s",
				entity_id,
				err,
			)
			if self._entity_work_generation_matches(
				entity_id,
				entity_state,
				generation,
				"actuation service error",
			):
				await self._retry_or_fail_entity_actuation(
					entity_id,
					entity_state,
					observed,
				)
			return
		finally:
			if actuation.get("dispatching") is dispatch_token:
				actuation["dispatching"] = False
		_LOGGER.debug("Service call completed for %s", entity_id)
		if self._entity_work_generation_matches(entity_id, entity_state, generation, "actuation confirmation scheduling"):
			self._schedule_actuation_timer(
				entity_id,
				entity_state,
				self._actuation_confirmation_delay(service_data),
				retry=False,
			)

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

	async def _handle_sibling_controlled_change(
		self,
		entity_id: str,
		entity_state: dict,
		observed_state: str,
	) -> None:
		"""Observe a sibling PBL command without treating it as manual control."""
		actuation = entity_state["actuation"]
		actuation["last_observed_state"] = observed_state
		actuation["updated_at"] = dt_util.utcnow()
		if actuation["status"] == ActuationStatus.PENDING:
			await self._handle_actuation_feedback(
				entity_id,
				entity_state,
				observed_state,
			)
		elif actuation["status"] == ActuationStatus.FAILED:
			await self._handle_external_change_matching_actuation_target(
				entity_id,
				entity_state,
				observed_state,
			)
		self._notify_switch(entity_id)

	async def _handle_external_change_matching_actuation_target(
		self, entity_id: str, entity_state: dict, effective_new_state: str | None,
	) -> bool:
		actuation = entity_state["actuation"]
		if actuation["status"] != ActuationStatus.FAILED:
			return False
		if effective_new_state is None or effective_new_state != actuation["target_state"]:
			return False
		if actuation["target_state"] is None or actuation["service_key"] is None:
			return False
		if not self._actuation_target_is_still_valid(entity_id, entity_state):
			return False

		_LOGGER.debug(
			"[%s] External-looking state %s matches failed actuation target; confirming late convergence",
			entity_id,
			effective_new_state,
		)
		self._confirm_entity_actuation(entity_id, entity_state, effective_new_state)
		return True

	async def _retry_or_fail_entity_actuation(
		self, entity_id: str, entity_state: dict, observed_state: str | None,
	) -> None:
		actuation = entity_state["actuation"]
		if not self._actuation_target_is_still_valid(entity_id, entity_state):
			self._cancel_entity_actuation(entity_state, "intent no longer valid")
			self._recover_entity_after_invalid_actuation(entity_id, entity_state)
			return
		if actuation["attempts"] >= _ACTUATION_MAX_ATTEMPTS:
			self._fail_entity_actuation(entity_id, entity_state, observed_state)
			return
		next_attempt = actuation["attempts"] + 1
		retry_delay = self._actuation_retry_delay(entity_id, next_attempt)
		actuation["next_retry_delay"] = retry_delay
		_LOGGER.debug(
			"[%s] Actuation target %s not converged (observed %s); scheduling retry %d/%d in %.3fs",
			entity_id,
			actuation["target_state"],
			observed_state,
			next_attempt,
			_ACTUATION_MAX_ATTEMPTS,
			retry_delay,
		)
		self._schedule_actuation_timer(entity_id, entity_state, retry_delay, retry=True)

	def _confirm_entity_actuation(self, entity_id: str, entity_state: dict, observed_state: str | None) -> None:
		actuation = entity_state["actuation"]
		self._cancel_actuation_timer(entity_state)
		service_key = actuation["service_key"]
		actuation.update(
			{
				"status": ActuationStatus.CONFIRMED,
				"dispatching": False,
				"next_retry_delay": None,
				"last_observed_state": observed_state,
				"last_error": None,
				"updated_at": dt_util.utcnow(),
			}
		)
		_LOGGER.debug("[%s] Actuation confirmed target=%s", entity_id, actuation["target_state"])
		if (
			service_key == CONF_PRESENCE_CLEARED_SERVICE
			and entity_state["state"] != EntityAutomationState.PAUSED
		):
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
				"dispatching": False,
				"next_retry_delay": None,
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
		if not self._set_presence_allowed_value(entity_id, entity_state, allowed):
			return

		if not self._entity_respects_presence_allowed(entity_state):
			await self._reconcile_entity(entity_id, entity_state)
			return

		if allowed:
			# Entering automation control – reconcile to the correct state
			if entity_state["state"] == EntityAutomationState.PAUSED:
				self.set_automation_paused(
					entity_id,
					False,
					reason="presence_allowed enabled",
					source="presence_allowed",
				)
			await self._reconcile_entity(entity_id, entity_state)
		else:
			# Leaving automation control – cancel any running timer and hold PAUSED.
			self.set_automation_paused(
				entity_id,
				True,
				reason="presence_allowed disabled",
				source="presence_allowed",
			)
		self._schedule_paused_state_save()

	def _set_presence_allowed_value(
		self,
		entity_id: str,
		entity_state: dict,
		allowed: bool,
	) -> bool:
		"""Set the switch-facing value without applying transition side effects."""
		if entity_state["presence_allowed"] == allowed:
			return False
		entity_state["presence_allowed"] = allowed
		self._notify_switch(entity_id)
		return True

	async def async_set_automation_control_state(
		self,
		entity_id: str,
		control_state: str,
	) -> None:
		"""Apply one administrative On/Off/Paused/Quieted/Active state."""
		entity_state = self._entity_states[entity_id]

		if control_state == AUTOMATION_CONTROL_STATE_ON:
			record = self._override_manager.get(entity_id)
			if record is not None and record.source == SOURCE_ADMIN:
				self._clear_external_override(entity_id, "admin selected on")
			self._set_presence_allowed_value(entity_id, entity_state, True)
			if (
				entity_state["state"] == EntityAutomationState.PAUSED
				and (entity_state.get("pause") or {}).get("source")
				not in _EXTERNAL_PAUSE_SOURCES
			):
				self.set_automation_paused(
					entity_id,
					False,
					reason="admin selected on",
					source="admin",
				)
			await self._reconcile_entity(entity_id, entity_state)
			return
		if control_state == AUTOMATION_CONTROL_STATE_OFF:
			await self.async_set_presence_allowed(entity_id, False)
			return

		honors_external_override = self._entity_honors_external_override(entity_state)
		if (
			control_state == AUTOMATION_CONTROL_STATE_QUIETED
			and not honors_external_override
		):
			raise ValueError(
				f"{entity_id} cannot enter Quieted while honor_external_override is false"
			)

		self._set_presence_allowed_value(entity_id, entity_state, True)

		if control_state == AUTOMATION_CONTROL_STATE_PAUSED:
			if not honors_external_override:
				self.set_automation_paused(
					entity_id,
					True,
					reason="admin selected paused",
					source="admin",
				)
				return
			self._override_manager.set_override(
				entity_id,
				EXTERNAL_POLICY_PAUSE,
				source=SOURCE_ADMIN,
				reason="admin selected paused",
			)
			return

		if control_state == AUTOMATION_CONTROL_STATE_QUIETED:
			rearm_latched = self._can_clear_room()
			self._override_manager.set_override(
				entity_id,
				EXTERNAL_POLICY_REARM_AFTER_CLEAR,
				source=SOURCE_ADMIN,
				reason="admin selected quieted",
				rearm_latched=rearm_latched,
				rearm_latched_at=(
					dt_util.utcnow().isoformat() if rearm_latched else None
				),
				rearm_armed_by="already clear at admin request" if rearm_latched else None,
				max_age_seconds=self._override_manager.max_age_seconds_for(entity_id),
				max_age_action=self._override_manager.max_age_action_for(entity_id),
			)
			return

		if control_state == AUTOMATION_CONTROL_STATE_ACTIVE:
			self._clear_external_override(entity_id, "admin selected active")
			if entity_state["state"] == EntityAutomationState.PAUSED:
				self.set_automation_paused(
					entity_id,
					False,
					reason="admin selected active",
					source="admin",
				)
			if entity_state["state"] == EntityAutomationState.QUIETED:
				self._exit_quieted(entity_id, entity_state, "admin selected active")
			await self._reconcile_entity(entity_id, entity_state)
			return

		raise ValueError(f"Unsupported automation control state: {control_state}")

	def _entity_respects_presence_allowed(self, entity_state: dict) -> bool:
		return entity_state["config"].get(
			CONF_RESPECTS_PRESENCE_ALLOWED,
			DEFAULT_RESPECTS_PRESENCE_ALLOWED,
		)

	def _presence_switch_allows_entity(self, entity_state: dict) -> bool:
		if not self._entity_respects_presence_allowed(entity_state):
			return True
		return entity_state["presence_allowed"]

	def _presence_lock_respects_manual_override(self, entity_state: dict) -> bool:
		return entity_state["config"].get(
			CONF_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE,
			DEFAULT_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE,
		)

	def _presence_lock_enabled(self, entity_state: dict) -> bool:
		config = entity_state["config"]
		return bool(
			config.get(CONF_REQUIRE_OCCUPANCY_FOR_DETECTED, DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED)
			or config.get(CONF_REQUIRE_VACANCY_FOR_CLEARED, DEFAULT_REQUIRE_VACANCY_FOR_CLEARED)
		)

	def _manual_disable_state_matches(self, entity_state: dict, state: str | None) -> bool:
		if state is None:
			return False
		manual_disable_states = entity_state["config"].get(
			CONF_MANUAL_DISABLE_STATES,
			DEFAULT_MANUAL_DISABLE_STATES,
		)
		return state in manual_disable_states

	def _presence_lock_should_yield_to_manual_override(
		self, entity_state: dict, state: str | None,
	) -> bool:
		"""Return whether Presence Lock should stand down for a user override."""
		if not self._presence_lock_enabled(entity_state):
			return False
		if not self._presence_lock_respects_manual_override(entity_state):
			return False
		if not self._presence_switch_allows_entity(entity_state):
			return True
		if entity_state["state"] == EntityAutomationState.PAUSED:
			return True
		return self._manual_disable_state_matches(entity_state, state)

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

	def set_automation_paused(
		self,
		entity_id: str,
		paused: bool,
		reason: str = "explicit automation pause",
		source: str = "service",
	) -> None:
		"""Transition to/from PAUSED state (transient, based on manual control).

		This is separate from presence_allowed:
		- presence_allowed: User-controlled, persisted across reboots
		- PAUSED state: Automatic, transient, based on manual_disable_states
		"""
		entity_state = self._entity_states[entity_id]
		current_state = entity_state["state"]
		is_paused = current_state == EntityAutomationState.PAUSED

		if is_paused == paused:
			if paused:
				entity_state["pause"] = self._build_pause_metadata(entity_id, entity_state, reason, source)
				self._schedule_paused_state_save()
			return

		if paused:
			self._bump_entity_work_generation(entity_state, reason)
			self._cancel_entity_timer(entity_state)
			self._cancel_entity_actuation(entity_state, "manual control")
			entity_state["pause"] = self._build_pause_metadata(entity_id, entity_state, reason, source)
			self._set_entity_state(entity_id, entity_state, EntityAutomationState.PAUSED, "manual control")
		else:
			_LOGGER.debug("Automation resumed for %s, will reconcile state", entity_id)
			# Don't reconcile here synchronously – the caller may need to await it
			# Just set to IDLE; the caller or reconciliation will fix it
			self._cancel_entity_actuation(
				entity_state,
				"manual control resumed",
			)
			self._bump_entity_work_generation(entity_state, reason)
			entity_state["pause"] = None
			self._set_entity_state(entity_id, entity_state, EntityAutomationState.IDLE, "manual control resumed")
		self._notify_switch(entity_id)
		self._schedule_paused_state_save()
		if not paused and self._override_manager.get(entity_id) is not None:
			self._handle_external_override_changed(entity_id)

	def _notify_switch(self, entity_id: str) -> None:
		for callback_fn in list(self._entity_states[entity_id]["callbacks"]):
			callback_fn()

	def _get_paused_persistence_path(self) -> Path:
		"""Get the path to the manual pause persistence file."""
		return Path(self.hass.config.path(".storage")) / f"pbl_paused_{self.entry.entry_id}.json"

	def _override_persistence_payload(self, entity_id: str) -> dict:
		"""Serialise an entity-scoped override with its original creation time.

		``created_at`` must survive the restart: restoring through
		``set_override`` would re-stamp "now" and restart the max-age budget on
		every Home Assistant restart, so a hold could never age out.
		"""
		record = self._override_manager.get(entity_id)
		if record is None:
			return {"reason": "quieted", "rearm_latched": False}
		return {
			"policy": record.policy,
			"source": record.source,
			"reason": record.reason,
			"created_at": record.created_at,
			"rearm_latched": bool(record.rearm_latched),
			"rearm_latched_at": record.rearm_latched_at,
			"rearm_armed_by": record.rearm_armed_by,
			"batch_id": record.batch_id,
			"batch_size": record.batch_size,
			"max_age_seconds": record.max_age_seconds,
			"max_age_action": record.max_age_action,
			"max_age_reached_at": record.max_age_reached_at,
		}

	def _schedule_paused_state_save(self) -> None:
		"""Schedule a save for manual pause state without blocking state handlers."""
		try:
			create_task = getattr(self.hass, "async_create_task", None)
			if create_task:
				create_task(self._save_paused_state())
			else:
				asyncio.get_running_loop().create_task(self._save_paused_state())
		except RuntimeError:
			_LOGGER.debug("No running loop available to persist paused state")

	async def _save_paused_state(self) -> None:
		"""Persist manually paused and quieted entities for restart recovery."""
		async with self._paused_state_save_lock:
			try:
				paused_entities = [
					entity_id
					for entity_id, entity_state in self._entity_states.items()
					if entity_state["state"] == EntityAutomationState.PAUSED
				]
				quieted_entities = [
					entity_id
					for entity_id, entity_state in self._entity_states.items()
					if entity_state["state"] == EntityAutomationState.QUIETED
				]
				override_entities = [
					entity_id
					for entity_id in self._entity_states
					if self._override_manager.get(entity_id) is not None
				]
				path = self._get_paused_persistence_path()

				if not paused_entities and not quieted_entities and not override_entities:
					await self.hass.async_add_executor_job(
						lambda: path.unlink(missing_ok=True)
					)
					return

				data = {
					"paused_entities": paused_entities,
					"paused": {
						entity_id: (
							self._entity_states[entity_id].get("pause")
							or self._build_pause_metadata(
								entity_id,
								self._entity_states[entity_id],
								"pause persisted",
								"legacy",
							)
						)
						for entity_id in paused_entities
					},
					"saved_at": dt_util.utcnow().isoformat(),
				}
				if override_entities:
					data["external_overrides"] = {
						entity_id: self._override_persistence_payload(entity_id)
						for entity_id in override_entities
					}
				if quieted_entities:
					data["quieted_entities"] = quieted_entities
					data["quieted"] = {
						entity_id: self._override_persistence_payload(entity_id)
						for entity_id in quieted_entities
					}
				await self.hass.async_add_executor_job(
					lambda: path.write_text(json.dumps(data))
				)
				_LOGGER.debug("Saved manual pause state: %s", data)
			except Exception as err:
				_LOGGER.exception("Failed to save manual pause state: %s", err)

	async def _load_paused_state(self) -> None:
		"""Restore manually paused entities from storage."""
		try:
			path = self._get_paused_persistence_path()
			if not path.exists():
				return

			data = await self.hass.async_add_executor_job(
				lambda: json.loads(path.read_text())
			)
			paused_entities = set(data.get("paused_entities", []))
			explicit_paused_entities = {
				entity_id
				for entity_id in paused_entities
				if self._pause_metadata_from_storage(entity_id, data).get("source")
				in _EXPLICIT_PAUSE_SOURCES
			}
			restored_external: set[str] = set()
			cleared_stale_override = False
			for entity_id, meta in (data.get("external_overrides") or {}).items():
				entity_state = self._entity_states.get(entity_id)
				if not entity_state:
					continue
				policy = meta.get("policy") or EXTERNAL_POLICY_PAUSE
				source = meta.get("source") or SOURCE_UNKNOWN
				if (
					policy == EXTERNAL_POLICY_PAUSE
					and source != SOURCE_ADMIN
					and entity_id not in explicit_paused_entities
				):
					current_state = self._get_trusted_effective_controlled_state(
						entity_state
					)
					if (
						current_state is not None
						and not self._manual_disable_state_matches(
							entity_state,
							current_state,
						)
					):
						cleared_stale_override = True
						continue
				record = self._override_manager.restore_override(
					entity_id,
					policy,
					source=source,
					reason=meta.get("reason") or "external override restored",
					created_at=meta.get("created_at"),
					rearm_latched=bool(meta.get("rearm_latched")),
					rearm_latched_at=meta.get("rearm_latched_at"),
					rearm_armed_by=meta.get("rearm_armed_by"),
					batch_id=meta.get("batch_id"),
					batch_size=meta.get("batch_size") or 0,
					max_age_seconds=meta.get("max_age_seconds"),
					max_age_action=meta.get("max_age_action")
					or self._quieted_max_age_action(entity_state),
					max_age_reached_at=meta.get("max_age_reached_at"),
					notify=False,
				)
				if record is None:
					continue
				restored_external.add(entity_id)
				if entity_id not in explicit_paused_entities:
					self._handle_external_override_changed(entity_id)

			cleared_stale_pause = False
			for entity_id in paused_entities:
				entity_state = self._entity_states.get(entity_id)
				if not entity_state or not self._entity_respects_presence_allowed(entity_state):
					continue
				metadata = self._pause_metadata_from_storage(entity_id, data)
				if (
					entity_id in restored_external
					and metadata.get("source") not in _EXPLICIT_PAUSE_SOURCES
				):
					continue
				if not self._should_restore_pause(entity_id, entity_state, metadata):
					entity_state["pause"] = None
					cleared_stale_pause = True
					continue
				self._cancel_entity_timer(entity_state)
				self._cancel_entity_actuation(entity_state, "manual pause restored")
				entity_state["pause"] = metadata
				self._set_entity_state(
					entity_id,
					entity_state,
					EntityAutomationState.PAUSED,
					"manual pause restored",
				)
				self._notify_switch(entity_id)
			if cleared_stale_pause or cleared_stale_override:
				self._schedule_paused_state_save()

			# Restore quieted holds. A quieted entity is dark on purpose, so the
			# hold is re-armed rather than reconciled; only a fresh rising
			# presence edge after vacancy may bring it back.
			quieted_entities = set(data.get("quieted_entities", []))
			quieted_meta = data.get("quieted") or {}
			for entity_id in quieted_entities:
				if entity_id in restored_external:
					continue
				entity_state = self._entity_states.get(entity_id)
				if not entity_state:
					continue
				meta = quieted_meta.get(entity_id) or {}
				# Restore, never re-create: the original creation time drives the
				# max-age safeguard, so a restart must not reset the hold's age.
				record = self._override_manager.restore_override(
					entity_id,
					meta.get("policy") or EXTERNAL_POLICY_REARM_AFTER_CLEAR,
					source=meta.get("source") or SOURCE_HOMEKIT_BATCH,
					reason=meta.get("reason") or "quieted hold restored",
					created_at=meta.get("created_at"),
					rearm_latched=bool(meta.get("rearm_latched")),
					rearm_latched_at=meta.get("rearm_latched_at"),
					rearm_armed_by=meta.get("rearm_armed_by"),
					batch_id=meta.get("batch_id"),
					batch_size=meta.get("batch_size") or 0,
					max_age_seconds=(
						meta.get("max_age_seconds")
						if meta.get("max_age_seconds") is not None
						else self._quieted_max_age(entity_state)
					),
					max_age_action=meta.get("max_age_action")
					or self._quieted_max_age_action(entity_state),
					max_age_reached_at=meta.get("max_age_reached_at"),
					notify=False,
				)
				if record is None:
					continue
				self._enter_quieted(entity_id, entity_state, record)
			_LOGGER.debug("Loaded manual pause state: %s", data)
		except Exception as err:
			_LOGGER.debug("No valid manual pause state to load: %s", err)

	async def _handle_service_call(self, event: Event) -> None:
		try:
			service_data = event.data.get("service_data") or {}
			target = service_data.get("entity_id")
			if not target:
				return

			service = event.data.get("service")
			expanded_entities = self._expand_target_entities(target)
			direct_targets = set(as_entity_list(target))

			for entity_id in expanded_entities:
				entity_state = self._entity_states[entity_id]
				config = entity_state["config"]
				expected_target_state = None
				if service == config.get(CONF_PRESENCE_DETECTED_SERVICE):
					expected_target_state = config.get(CONF_PRESENCE_DETECTED_STATE)
				elif service == config.get(CONF_PRESENCE_CLEARED_SERVICE):
					expected_target_state = config.get(CONF_PRESENCE_CLEARED_STATE)
				origin = self._classify_command_context(
					entity_id,
					event.context,
					include_parent=True,
					expected_target_state=expected_target_state,
				)
				if origin != CommandOrigin.EXTERNAL:
					continue
				if expected_target_state == config.get(CONF_PRESENCE_DETECTED_STATE):
					self._batch_observer.cancel_blocked_commands(entity_id)
				await self._handle_external_action(
					entity_id,
					service,
					event.context,
					interceptor_handles_direct_target=(
						entity_id in direct_targets
						and self._interceptor is not None
						and self._interceptor.handles_presence_lock(
							entity_id,
							service,
						)
					),
				)
		except Exception as err:
			_LOGGER.exception("Error handling service call event: %s", err)

	def _expand_target_entities(self, target: Any) -> list[str]:
		"""Expand service targets to controlled entities, following nested groups.

		Two different group flavours must be followed. Home Assistant light
		groups publish their members in ``attributes.entity_id``; Zigbee2MQTT
		groups publish theirs in ``attributes.group_entities``. Following only
		the former made every entity behind a Z2M group invisible to targeting.
		"""
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

			state = self.hass.states.get(entity_id)
			if not state:
				continue
			attributes = getattr(state, "attributes", None) or {}
			for attribute in ("entity_id", "group_entities"):
				members = attributes.get(attribute)
				if not members:
					continue
				to_visit.extend(
					member for member in as_entity_list(members) if member not in seen
				)

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
			origin = self._classify_command_context(
				entity_id,
				new_state.context,
				include_parent=True,
			)

			# Check if an RLC tracking entity is configured for this entity
			# If so, use the RLC sensor's state to determine if this is a "real" change
			rlc_tracking_entity = cfg.get(CONF_RLC_TRACKING_ENTITY)
			if rlc_tracking_entity:
				# Get the "real" state from the RLC sensor
				rlc_state = self._get_valid_rlc_effective_state(entity_id, entity_state)
				if rlc_state is None:
					if (
						old_state.state in _UNTRUSTED_STARTUP_STATES
						or new_state.state in _UNTRUSTED_STARTUP_STATES
					):
						_LOGGER.debug(
							"RLC tracking entity %s unavailable for %s, ignoring startup state %s -> %s",
							rlc_tracking_entity, entity_id, old_state.state, new_state.state,
						)
						return
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
						if origin == CommandOrigin.OWN:
							await self._handle_actuation_feedback(entity_id, entity_state, new_state.state)
						elif origin == CommandOrigin.SIBLING:
							await self._handle_sibling_controlled_change(
								entity_id,
								entity_state,
								new_state.state,
							)
						else:
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
				if _is_untrusted_state_value(old_state) or _is_untrusted_state_value(new_state):
					_LOGGER.debug(
						"Ignoring untrusted controlled-entity transition for %s: %s -> %s",
						entity_id,
						old_state.state,
						new_state.state,
					)
					return
				effective_new_state = new_state.state
				# Track the observed state so redundant external commands can be
				# recognised without racing the live state machine. Kept separate
				# from last_effective_state, which is RLC-specific.
				entity_state["last_observed_state"] = effective_new_state

			if origin == CommandOrigin.OWN:
				await self._handle_actuation_feedback(entity_id, entity_state, effective_new_state)
				return
			if origin == CommandOrigin.SIBLING:
				await self._handle_sibling_controlled_change(
					entity_id,
					entity_state,
					effective_new_state,
				)
				return

			await self._process_external_controlled_change(
				entity_id, entity_state, effective_new_state, new_state.context
			)
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
			if new_effective not in self._valid_effective_states_for_entity(entity_state):
				_LOGGER.warning(
					"Ignoring RLC tracking change for %s via %s: invalid effective state %s",
					entity_id,
					rlc_entity_id,
					new_effective,
				)
				return
			last_effective = entity_state.get("last_effective_state")
			if last_effective is None or old_effective is None:
				entity_state["last_effective_state"] = new_effective
				_LOGGER.debug(
					"RLC tracking entity %s for %s initialized baseline to %s (old_effective=%s)",
					rlc_entity_id, entity_id, new_effective, old_effective,
				)
				return
			if new_effective == last_effective:
				# Already processed (e.g. the synchronous read in
				# _handle_controlled_entity_change saw the fresh RLC value first).
				return
			entity_state["last_effective_state"] = new_effective

			# Ownership is derived from the controlled entity's settled context: if
			# PBL issued the change, classify it before applying manual-control logic.
			controlled = self.hass.states.get(entity_id)
			origin = self._classify_command_context(
				entity_id,
				controlled.context if controlled is not None else None,
				include_parent=True,
			)
			if origin == CommandOrigin.OWN:
				await self._handle_actuation_feedback(entity_id, entity_state, new_effective)
				return
			if origin == CommandOrigin.SIBLING:
				await self._handle_sibling_controlled_change(
					entity_id,
					entity_state,
					new_effective,
				)
				return

			await self._process_external_controlled_change(
				entity_id,
				entity_state,
				new_effective,
				controlled.context if controlled is not None else None,
			)
		except Exception as err:
			_LOGGER.exception(
				"Error handling RLC tracking change for %s: %s",
				event.data.get("entity_id"), err,
			)

	async def _process_external_controlled_change(
		self, entity_id: str, entity_state: dict, effective_new_state: str | None,
		context: Context | None = None,
	) -> None:
		"""Apply manual-control pause/resume logic for an external state change."""
		cfg = entity_state["config"]

		if await self._handle_external_change_matching_actuation_target(
			entity_id, entity_state, effective_new_state
		):
			return

		# Classify before Presence Lock gets a vote. A whole-home off that lands
		# in an occupied room would otherwise be reverted by the presence-lock
		# fallback before the quieted hold is ever recorded.
		resolved = self._resolve_external_policy(entity_id, entity_state, context)
		is_bulk = self._resolved_policy_is_confirmed_bulk(resolved)

		if not is_bulk:
			if self._presence_lock_should_yield_to_manual_override(entity_state, effective_new_state):
				if self._manual_disable_state_matches(entity_state, effective_new_state):
					self._apply_external_policy(
						entity_id,
						entity_state,
						context,
						"presence lock yielded to manual override",
						resolved=resolved,
					)
				return

			# Check presence lock first - this takes priority
			if await self._check_and_apply_presence_lock(entity_state, effective_new_state):
				return  # Presence lock handled the state change

		if not cfg[CONF_DISABLE_ON_EXTERNAL_CONTROL]:
			if entity_state["state"] == EntityAutomationState.QUIETED:
				return
			if await self._ensure_external_detected_action_expires(
				entity_id,
				entity_state,
				effective_new_state,
			):
				return
			if self._hold_allowed_presence_lock_clear(
				entity_id,
				entity_state,
				effective_new_state,
				context,
				resolved,
			):
				return
			self._adopt_external_action_in_flight(
				entity_id,
				entity_state,
				effective_new_state,
			)
			return

		if not self._presence_switch_allows_entity(entity_state):
			return
		if (
			entity_state["state"] == EntityAutomationState.QUIETED
			and effective_new_state == cfg[CONF_PRESENCE_CLEARED_STATE]
		):
			return

		# Determine whether this external change should pause or resume automation
		should_pause = self._should_external_change_pause(entity_id, cfg, effective_new_state)

		if should_pause:
			# No redundancy guard is needed here: a redundant command produces no
			# state transition at all, and _handle_controlled_entity_change has
			# already discarded no-op events before reaching this point.
			self._apply_external_policy(
				entity_id,
				entity_state,
				context,
				"external controlled entity change",
				resolved=resolved,
			)
		else:
			self._clear_external_override(
				entity_id, "external controlled entity change resumed"
			)
			self.set_automation_paused(
				entity_id,
				False,
				reason="external controlled entity change resumed",
				source="external_state",
			)
			if await self._ensure_external_detected_action_expires(
				entity_id,
				entity_state,
				effective_new_state,
			):
				return
			if self._adopt_external_action_in_flight(
				entity_id,
				entity_state,
				effective_new_state,
			):
				return
			# Reconcile into the correct active state
			await self._reconcile_entity(entity_id, entity_state)


	async def _handle_external_action(
		self,
		entity_id: str,
		service: str | None,
		context: Context | None = None,
		*,
		interceptor_handles_direct_target: bool = False,
	) -> None:
		entity_state = self._entity_states[entity_id]
		cfg = entity_state["config"]

		# Check presence lock first - this takes priority
		# Determine what state the service would result in
		target_state = None
		if service == cfg[CONF_PRESENCE_DETECTED_SERVICE]:
			target_state = cfg[CONF_PRESENCE_DETECTED_STATE]
		elif service == cfg[CONF_PRESENCE_CLEARED_SERVICE]:
			target_state = cfg[CONF_PRESENCE_CLEARED_STATE]

		if target_state and self._presence_lock_should_yield_to_manual_override(
			entity_state, target_state,
		):
			return

		# Classify before Presence Lock: a whole-home off must not be reverted by
		# the presence-lock fallback just because the room is occupied.
		resolved = self._resolve_external_policy(entity_id, entity_state, context)
		is_bulk = self._resolved_policy_is_confirmed_bulk(resolved)

		if (
			not is_bulk
			and target_state
			and not (
				self._using_interceptor
				and interceptor_handles_direct_target
			)
			and await self._check_and_apply_presence_lock(
				entity_state, target_state, force_fallback=True
			)
		):
			return  # Presence lock handled the state change

		if not cfg[CONF_DISABLE_ON_EXTERNAL_CONTROL]:
			if await self._ensure_external_detected_action_expires(
				entity_id,
				entity_state,
				target_state,
			):
				return
			if self._hold_allowed_presence_lock_clear(
				entity_id,
				entity_state,
				target_state,
				context,
				resolved,
			):
				return
			self._adopt_external_action_in_flight(
				entity_id,
				entity_state,
				target_state,
			)
			return

		if not self._presence_switch_allows_entity(entity_state):
			return
		if (
			entity_state["state"] == EntityAutomationState.QUIETED
			and target_state == cfg[CONF_PRESENCE_CLEARED_STATE]
		):
			return

		# Determine whether this external action should pause or resume automation
		should_pause = self._should_external_change_pause(entity_id, cfg, target_state)
		if should_pause and self._external_pause_action_is_untrusted_or_redundant(
			entity_id, entity_state, target_state
		):
			return

		if should_pause:
			self._apply_external_policy(
				entity_id,
				entity_state,
				context,
				f"external service {service}",
				resolved=resolved,
			)
		elif target_state:
			self._clear_external_override(
				entity_id, f"external service {service} resumed"
			)
			self.set_automation_paused(
				entity_id,
				False,
				reason=f"external service {service} resumed",
				source="external_service",
			)
			if await self._ensure_external_detected_action_expires(
				entity_id,
				entity_state,
				target_state,
			):
				return
			if self._adopt_external_action_in_flight(
				entity_id,
				entity_state,
				target_state,
			):
				return
			await self._reconcile_entity(entity_id, entity_state)

	def _hold_allowed_presence_lock_clear(
		self,
		entity_id: str,
		entity_state: dict,
		target_state: str | None,
		context: Context | None,
		resolved: tuple[str, str, Any],
	) -> bool:
		"""Keep an allowed external clear dark until a fresh presence edge."""
		config = entity_state["config"]
		if target_state != config[CONF_PRESENCE_CLEARED_STATE]:
			return False
		if (
			not self._presence_lock_enabled(entity_state)
			or self._is_clearing_authority_occupied()
		):
			return False
		if self._resolved_policy_is_confirmed_bulk(resolved):
			return False

		source, _policy, decision = resolved
		policy = self._apply_external_policy(
			entity_id,
			entity_state,
			context,
			"external clear accepted by clearing authority",
			resolved=(
				source,
				EXTERNAL_POLICY_REARM_AFTER_CLEAR,
				decision,
			),
		)
		if (
			policy == EXTERNAL_POLICY_REARM_AFTER_CLEAR
			and self._can_clear_room()
		):
			self._override_manager.arm_rearm_latch(
				entity_id,
				"already clear at external clear",
			)
		return True

	def _get_trusted_effective_controlled_state(self, entity_state: dict) -> str | None:
		cfg = entity_state["config"]
		rlc_tracking_entity = cfg.get(CONF_RLC_TRACKING_ENTITY)
		if rlc_tracking_entity:
			return self._get_valid_rlc_effective_state(cfg[CONF_ENTITY_ID], entity_state)

		current_state = self.hass.states.get(cfg[CONF_ENTITY_ID])
		if current_state and current_state.state not in _UNTRUSTED_STARTUP_STATES:
			return current_state.state
		return None

	def _external_pause_action_is_untrusted_or_redundant(
		self, entity_id: str, entity_state: dict, target_state: str | None,
	) -> bool:
		"""Return whether an external pause action should be ignored.

		A ``turn_off`` aimed at an entity that is already off changes nothing, so
		it must not manufacture a pause. Whole-home commands routinely include
		entities that are already in the requested state, which previously left
		untouched rooms paused for no reason.

		Redundancy is judged against the last effective state PBL actually
		observed, not against the live state. ``EVENT_CALL_SERVICE`` is delivered
		before the entity's own state write, so reading the live state here can
		race with the very command being classified.
		"""
		if target_state is None:
			return False
		if not entity_state["config"].get(CONF_RLC_TRACKING_ENTITY):
			effective_state = self._get_trusted_effective_controlled_state(entity_state)
			if effective_state is None:
				_LOGGER.debug(
					"Ignoring external pause action for %s because no trusted effective state is available",
					entity_id,
				)
				return True
			observed_state = entity_state.get("last_observed_state")
			if observed_state is not None and observed_state == target_state:
				_LOGGER.debug(
					"Ignoring redundant external pause action for %s: last observed state already %s",
					entity_id, target_state,
				)
				return True
			return False

		effective_state = self._get_trusted_effective_controlled_state(entity_state)
		if effective_state is None:
			_LOGGER.debug(
				"Ignoring external pause action for %s because no trusted effective state is available",
				entity_id,
			)
			return True
		if effective_state == target_state:
			_LOGGER.debug(
				"Ignoring redundant external pause action for %s: effective state already %s",
				entity_id, target_state,
			)
			return True
		return False

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
		if not self._entry_is_active():
			return False

		if not self._can_clear_room():
			return False

		if (
			entity_state["state"] == EntityAutomationState.CLEARING
			and entity_state.get("off_timer") is not None
		):
			_LOGGER.debug(
				"[%s] External detected action while clear conditions are met; existing off timer remains active",
				entity_id,
			)
			return True

		self._set_entity_state(
			entity_id,
			entity_state,
			EntityAutomationState.OCCUPIED,
			"external detected action while clear conditions are met",
		)
		await self._start_entity_off_timer(entity_id, entity_state)
		return True

	def _adopt_external_action_in_flight(
		self,
		entity_id: str,
		entity_state: dict,
		target_state: str | None,
	) -> bool:
		"""Adopt a compatible external command without racing its state update."""
		config = entity_state["config"]
		if not self._entry_is_active():
			return False

		if target_state == config[CONF_PRESENCE_DETECTED_STATE]:
			if not self._is_any_occupied() or not self._are_activation_conditions_met():
				return False
			desired = DesiredState.DETECTED
			service_key = CONF_PRESENCE_DETECTED_SERVICE
			reason = IntentReason.PRESENCE
			state = EntityAutomationState.OCCUPIED
		elif target_state == config[CONF_PRESENCE_CLEARED_STATE]:
			if not self._can_clear_room():
				return False
			if self._ownership_manager.other_entry_wants_on(
				self.entry.entry_id,
				entity_id,
			):
				return False
			desired = DesiredState.CLEARED
			service_key = CONF_PRESENCE_CLEARED_SERVICE
			reason = IntentReason.CLEARING
			state = EntityAutomationState.IDLE
		else:
			return False

		self._cancel_entity_timer(entity_state)
		self._cancel_entity_actuation(
			entity_state,
			"external action in flight",
		)
		self._set_entity_state(
			entity_id,
			entity_state,
			state,
			"external action in flight",
		)
		self._set_entity_intent(
			entity_id,
			entity_state,
			desired,
			service_key,
			target_state,
			reason,
			True,
		)
		_LOGGER.debug(
			"[%s] External action toward %s is in flight; deferring reconciliation",
			entity_id,
			target_state,
		)
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
		cfg = entity_state["config"]
		entity_id = cfg[CONF_ENTITY_ID]

		require_occ = cfg.get(CONF_REQUIRE_OCCUPANCY_FOR_DETECTED, DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED)
		require_vac = cfg.get(CONF_REQUIRE_VACANCY_FOR_CLEARED, DEFAULT_REQUIRE_VACANCY_FOR_CLEARED)

		if not self._entry_is_active():
			return False

		# A quieted entity is deliberately holding dark after a whole-home
		# command. Presence Lock must not fight that hold, or the bulk off is
		# reverted the moment it lands in an occupied room.
		if entity_state["state"] == EntityAutomationState.QUIETED:
			_LOGGER.debug(
				"Presence lock: skipping %s because it is quieted after a bulk command",
				entity_id,
			)
			return False

		if self._presence_lock_should_yield_to_manual_override(entity_state, new_state):
			_LOGGER.debug(
				"Presence lock: allowing manual override for %s (state=%s)",
				entity_id,
				new_state,
			)
			return False

		# If entity is being turned ON (detected state) but room is empty and lock is enabled
		if new_state == cfg[CONF_PRESENCE_DETECTED_STATE] and require_occ and not self._is_any_occupied():
			_LOGGER.debug("Presence lock (fallback): reverting %s to cleared state (room is empty)", entity_id)
			# Force the reversion without checking current state (since the triggering action may still be in progress)
			await self._force_apply_action(entity_state, CONF_PRESENCE_CLEARED_SERVICE)
			return True

		# If entity is being turned OFF (cleared state) but room is occupied and lock is enabled
		if (
			new_state == cfg[CONF_PRESENCE_CLEARED_STATE]
			and require_vac
			and self._is_clearing_authority_occupied()
		):
			_LOGGER.debug(
				"Presence lock (fallback): reverting %s to detected state "
				"(clearing authority occupied)",
				entity_id,
			)
			# Force the reversion without checking current state (since the triggering action may still be in progress)
			await self._force_apply_action(entity_state, CONF_PRESENCE_DETECTED_SERVICE)
			return True

		return False

	async def _force_apply_action(self, entity_state: dict, service_key: str) -> None:
		"""Apply a Presence Lock correction through the normal actuator."""
		entity_id = entity_state["config"][CONF_ENTITY_ID]
		reason = (
			IntentReason.PRESENCE
			if service_key == CONF_PRESENCE_DETECTED_SERVICE
			else IntentReason.CLEARING
		)
		await self._apply_service_intent(
			entity_id,
			entity_state,
			service_key,
			reason,
			force=True,
			presence_lock_override=not self._presence_lock_respects_manual_override(
				entity_state
			),
		)

	async def _handle_presence_change(self, event: Event) -> None:
		"""Handle state changes on presence and clearing sensors.

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
				if new_effective not in _BINARY_EFFECTIVE_STATES:
					_LOGGER.warning(
						"Ignoring RLC presence sensor %s with invalid effective state %s",
						entity_id,
						new_effective,
					)
					return
				currently_on = new_effective == "on"
				currently_off = new_effective == "off"
			else:
				# For regular sensors, check if state actually changed
				if new_state.state == old_state.state:
					return
				if _is_untrusted_state_value(old_state) or _is_untrusted_state_value(new_state):
					_LOGGER.debug(
						"Ignoring untrusted presence sensor transition for %s: %s -> %s",
						entity_id,
						old_state.state,
						new_state.state,
					)
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
				# A quieted hold is released only by a rising presence edge that
				# follows real vacancy. Releasing here drops the entity back to
				# IDLE so the normal transition below turns it on.
				self._release_latched_quieted_entities()
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
				# Start off-timer for OCCUPIED entities ONLY if all clear
				# conditions are already met (primer-sensor case: hallway PIR
				# triggers but nobody enters the room). When clear conditions are
				# not met, the entity must stay in OCCUPIED and let the OFF handler
				# manage the transition naturally.
				if self._can_clear_room():
					await self._start_off_timer()

			# --- Clearing sensor turns ON ---
			elif currently_on and entity_id in clearing_sensors:
				_LOGGER.debug("Clearing sensor occupied via %s", entity_id)
				for eid, es in self._entity_states.items():
					if not self._presence_switch_allows_entity(es):
						continue
					cur = es["state"]
					if cur in (
						EntityAutomationState.CLEARING,
						EntityAutomationState.WAITING_FOR_CLEAR,
						EntityAutomationState.SETTLING_OFF,
						EntityAutomationState.PENDING_ACTIVATION,
					):
						self._cancel_entity_timer(es)
						self._cancel_entity_actuation(es, "clearing sensor occupied")
						if self._entry_is_active():
							self._set_entity_state(
								eid,
								es,
								EntityAutomationState.OCCUPIED,
								"clearing sensor occupied",
							)
							await self._apply_service_intent(
								eid,
								es,
								CONF_PRESENCE_DETECTED_SERVICE,
								IntentReason.PRESENCE,
							)
						else:
							self._set_entity_state(
								eid,
								es,
								EntityAutomationState.PENDING_ACTIVATION,
								"clearing sensor occupied, conditions not met",
							)

			# --- Clearing sensor turns OFF ---
			elif currently_off:
				effective_clearing = set(clearing_sensors if clearing_sensors else presence_sensors)
				if entity_id in effective_clearing:
					all_clear = self._can_clear_room()
					if all_clear:
						_LOGGER.debug("All clear conditions met")
						# Vacancy only *arms* a quieted hold. The entity stays
						# dark until presence rises again.
						self._arm_rearm_latches_for_vacancy()
						for eid, es in self._entity_states.items():
							if not self._presence_switch_allows_entity(es):
								continue
							cur = es["state"]
							if cur == EntityAutomationState.OCCUPIED:
								# Start off-timer → CLEARING
								await self._start_entity_off_timer(eid, es)
							elif cur == EntityAutomationState.WAITING_FOR_CLEAR:
								# Clear conditions finally met – turn off immediately
								_LOGGER.debug("%s: clear conditions met while WAITING_FOR_CLEAR, turning off", eid)
								self._cancel_entity_timer(es)
								await self._apply_service_intent(eid, es, CONF_PRESENCE_CLEARED_SERVICE, IntentReason.CLEARING)
							elif cur == EntityAutomationState.PENDING_ACTIVATION:
								# Preserve the configured vacancy delay even while
								# this entry is activation-gated and non-owning.
								await self._clear_pending_activation_after_vacancy(
									eid,
									es,
								)
		except Exception as err:
			_LOGGER.exception("Error handling presence change: %s", err)

	async def _handle_activation_condition_change(self, event: Event) -> None:
		"""Handle activation-gate changes and shared ownership handoff."""
		try:
			entity_id = event.data.get("entity_id")
			new_state = event.data.get("new_state")
			old_state = event.data.get("old_state")

			if not new_state or not old_state:
				return
			if new_state.state == old_state.state:
				return

			_LOGGER.debug(
				"Activation condition %s changed: %s -> %s",
				entity_id, old_state.state, new_state.state
			)

			if not self._entry_is_active():
				for eid, es in self._entity_states.items():
					cur = es["state"]
					if cur in (
						EntityAutomationState.OCCUPIED,
						EntityAutomationState.SETTLING_ON,
					):
						self._bump_entity_work_generation(
							es,
							"activation conditions disabled",
						)
						self._cancel_entity_timer(es)
						self._cancel_entity_actuation(
							es,
							"activation conditions disabled",
						)
						self._set_entity_state(
							eid,
							es,
							EntityAutomationState.PENDING_ACTIVATION,
							"activation conditions disabled",
						)
						self._set_entity_intent(
							eid,
							es,
							DesiredState.NONE,
							None,
							None,
							IntentReason.CONDITIONS,
							False,
						)
					elif cur in (
						EntityAutomationState.PENDING_ACTIVATION,
						EntityAutomationState.IDLE,
					):
						self._publish_ownership(eid, es)
						self._set_entity_intent(
							eid,
							es,
							DesiredState.NONE,
							None,
							None,
							IntentReason.CONDITIONS,
							False,
						)
					else:
						# States already clearing are allowed to finish, but an
						# inactive entry must never retain shared on-ownership.
						self._publish_ownership(eid, es)
				return

			# Transition PENDING_ACTIVATION entities to OCCUPIED
			for eid, es in self._entity_states.items():
				self._publish_ownership(eid, es)
				if not self._presence_switch_allows_entity(es):
					continue
				if (
					es["state"] == EntityAutomationState.PENDING_ACTIVATION
					and self._activation_catchup_allowed()
				):
					_LOGGER.debug(
						"Activation conditions now met – transitioning %s from PENDING to OCCUPIED", eid
					)
					self._cancel_entity_timer(es)
					self._set_entity_state(eid, es, EntityAutomationState.OCCUPIED, "activation conditions met")
					await self._apply_service_intent(eid, es, CONF_PRESENCE_DETECTED_SERVICE, IntentReason.CONDITIONS)

			# Start off-timer for newly-OCCUPIED entities ONLY if clear
			# conditions are already met (same primer-sensor guard as in
			# _handle_presence_change).
			if self._can_clear_room():
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
		"""Apply a legacy helper action through the normal actuator."""
		entity_id = entity_state["config"][CONF_ENTITY_ID]
		reason = (
			IntentReason.PRESENCE
			if service_key == CONF_PRESENCE_DETECTED_SERVICE
			else IntentReason.CLEARING
		)
		await self._apply_service_intent(
			entity_id,
			entity_state,
			service_key,
			reason,
		)

	def _should_follow_presence(self, entity_state: dict) -> bool:
		"""Check if automation should apply to this entity.

		Returns True if presence automation should affect this entity.
		Requires both:
		- presence_allowed: User-controlled toggle (persisted across reboots)
		- State is not PAUSED: Transient pause due to manual control
		"""
		return self._presence_switch_allows_entity(entity_state) and entity_state["state"] not in (
			EntityAutomationState.PAUSED,
			EntityAutomationState.QUIETED,
		)

	def _register_command_context(
		self,
		entity_id: str,
		context: Context,
		target_state: str | None,
	) -> None:
		"""Register a PBL command before dispatching its service call."""
		entity_state = self._entity_states[entity_id]
		contexts = entity_state["contexts"]
		context_targets = entity_state["context_targets"]
		if contexts.maxlen and len(contexts) == contexts.maxlen:
			context_targets.pop(contexts[0], None)
		contexts.append(context.id)
		context_targets[context.id] = target_state
		self._command_context_registry.register(
			context.id,
			self.entry.entry_id,
			entity_id,
			target_state,
		)

	def _classify_command_context(
		self,
		entity_id: str,
		context: Context | None,
		*,
		include_parent: bool,
		expected_target_state: str | None = None,
	) -> CommandOrigin:
		"""Classify a command context across all PBL config entries."""
		origin = self._command_context_registry.classify(
			self.entry.entry_id,
			entity_id,
			context,
			include_parent=include_parent,
			expected_target_state=expected_target_state,
		)
		if origin != CommandOrigin.EXTERNAL or context is None:
			return origin

		entity_state = self._entity_states[entity_id]
		context_ids = entity_state["contexts"]
		context_targets = entity_state["context_targets"]
		context_id = getattr(context, "id", None)
		parent_id = getattr(context, "parent_id", None)
		if (
			context_id in context_ids
			and (
				expected_target_state is None
				or context_targets.get(context_id) == expected_target_state
			)
		):
			return CommandOrigin.OWN
		if (
			include_parent
			and parent_id in context_ids
			and (
				expected_target_state is None
				or context_targets.get(parent_id) == expected_target_state
			)
		):
			return CommandOrigin.OWN
		return CommandOrigin.EXTERNAL

	def _classify_interceptor_command(
		self,
		entity_id: str,
		context: Context | None,
		target_state: str | None,
	) -> CommandOrigin:
		"""Classify one intercepted command for its protected entity."""
		return self._classify_command_context(
			entity_id,
			context,
			include_parent=True,
			expected_target_state=target_state,
		)

	def _is_context_ours(self, entity_id: str, context: Context | None) -> bool:
		return (
			self._classify_command_context(
				entity_id,
				context,
				include_parent=True,
			)
			== CommandOrigin.OWN
		)

	# ------------------------------------------------------------------
	# External command classification and entity-scoped overrides
	# ------------------------------------------------------------------

	def _entity_honors_external_override(self, entity_state: dict) -> bool:
		"""Return whether this entity consults entity-scoped external overrides."""
		return entity_state["config"].get(
			CONF_HONOR_EXTERNAL_OVERRIDE,
			DEFAULT_HONOR_EXTERNAL_OVERRIDE,
		)

	def _unknown_source_policy(self, entity_state: dict) -> str:
		"""Return the policy for external commands we cannot attribute."""
		return entity_state["config"].get(
			CONF_UNKNOWN_SOURCE_POLICY,
			DEFAULT_UNKNOWN_SOURCE_POLICY,
		)

	def _quieted_max_age(self, entity_state: dict) -> float | None:
		"""Return the stale-hold threshold for a quieted hold, if configured."""
		value = entity_state["config"].get(CONF_QUIETED_MAX_AGE, DEFAULT_QUIETED_MAX_AGE)
		try:
			seconds = float(value)
		except (TypeError, ValueError):
			return None
		return seconds if seconds > 0 else None

	def _quieted_max_age_action(self, entity_state: dict) -> str:
		"""Return the configured one-shot action when a quieted hold gets stale."""
		return entity_state["config"].get(
			CONF_QUIETED_MAX_AGE_ACTION,
			DEFAULT_QUIETED_MAX_AGE_ACTION,
		)

	def _configure_batch_observer(self) -> None:
		"""Register this entry's bulk-detection settings with the shared observer."""
		data = self.entry.data
		self._batch_observer.configure_entry(
			self.entry.entry_id,
			mode=data.get(CONF_HOMEKIT_BATCH_MODE, DEFAULT_HOMEKIT_BATCH_MODE),
			window_ms=data.get(CONF_BATCH_WINDOW_MS, DEFAULT_BATCH_WINDOW_MS),
			retain_seconds=data.get(CONF_BATCH_RETAIN_SECONDS, DEFAULT_BATCH_RETAIN_SECONDS),
			min_distinct_entities=data.get(
				CONF_BATCH_MIN_DISTINCT_ENTITIES,
				DEFAULT_BATCH_MIN_DISTINCT_ENTITIES,
			),
		)

	def _resolve_external_policy(
		self, entity_id: str, entity_state: dict, context: Context | None,
	) -> tuple[str, str, Any]:
		"""Classify an external command into (source, policy, batch decision).

		UNKNOWN deliberately keeps today's PAUSE semantics: a wall switch on a
		direct-relay device reaches Home Assistant as an untraceable state change,
		and rearming it automatically would silently defeat a physical off.
		"""
		fallback = self._unknown_source_policy(entity_state)
		decision = None
		for context_id in (
			getattr(context, "id", None),
			getattr(context, "parent_id", None),
		):
			if not context_id:
				continue
			decision = self._batch_observer.classify(context_id)
			if decision is not None:
				break

		if decision is None:
			return SOURCE_UNKNOWN, fallback, None
		if not decision.confirmed:
			# A single HomeKit accessory command: a genuine per-room manual off.
			return SOURCE_HOMEKIT_SINGLE, fallback, decision
		if not decision.enforcing:
			_LOGGER.info(
				"Bulk command observed for %s (batch %s, size %d) but batch mode is "
				"'%s'; applying %s",
				entity_id,
				decision.batch_id,
				decision.size,
				self._batch_observer.mode,
				fallback,
			)
			return SOURCE_HOMEKIT_BATCH, fallback, decision
		return (
			SOURCE_HOMEKIT_BATCH,
			self._override_manager.bulk_policy_for(entity_id),
			decision,
		)

	@staticmethod
	def _resolved_policy_is_confirmed_bulk(
		resolved: tuple[str, str, Any],
	) -> bool:
		"""Return whether a classification came from an enforced bulk command."""
		decision = resolved[2]
		return bool(
			decision is not None
			and getattr(decision, "confirmed", False)
			and getattr(decision, "enforcing", False)
		)

	def _emit_command_intent(
		self,
		entity_id: str,
		source: str,
		policy: str,
		decision: Any,
		reason: str,
	) -> None:
		"""Emit a concise diagnostic event for one classification decision."""
		payload = {
			"entry_id": self.entry.entry_id,
			"room": self.entry.data.get(CONF_ROOM_NAME),
			"entity_id": entity_id,
			"source": source,
			"policy": policy,
			"reason": reason,
			"batch_id": getattr(decision, "batch_id", None),
			"batch_size": getattr(decision, "size", 0),
			"batch_mode": self._batch_observer.mode,
		}
		_LOGGER.debug("Command intent for %s: %s", entity_id, payload)
		try:
			fire = getattr(self.hass.bus, "async_fire", None)
			if fire is not None:
				result = fire(EVENT_COMMAND_INTENT, payload)
				# Some test doubles expose an async bus; never leave a coroutine
				# dangling from a synchronous diagnostic emit.
				if asyncio.iscoroutine(result):
					result.close()
		except Exception as err:  # pragma: no cover - defensive
			_LOGGER.debug("Could not fire %s event: %s", EVENT_COMMAND_INTENT, err)

	def _apply_external_policy(
		self,
		entity_id: str,
		entity_state: dict,
		context: Context | None,
		reason: str,
		resolved: tuple[str, str, Any] | None = None,
	) -> str:
		"""Classify and record an entity-scoped external override."""
		source, policy, decision = resolved or self._resolve_external_policy(
			entity_id, entity_state, context
		)
		if policy == EXTERNAL_POLICY_IGNORE:
			self._emit_command_intent(entity_id, source, policy, decision, reason)
			self._override_manager.note_source(entity_id, source)
			return policy

		batch_id = getattr(decision, "batch_id", None)
		existing = self._override_manager.get(entity_id)
		if existing is not None and existing.source == SOURCE_ADMIN:
			self._emit_command_intent(
				entity_id,
				existing.source,
				existing.policy,
				decision,
				"admin override preserved",
			)
			return existing.policy
		self._emit_command_intent(entity_id, source, policy, decision, reason)
		same_batch = (
			existing is not None
			and batch_id is not None
			and existing.batch_id == batch_id
		)
		confirmed_bulk = bool(
			decision is not None
			and getattr(decision, "confirmed", False)
			and source == SOURCE_HOMEKIT_BATCH
		)
		self._override_manager.set_override(
			entity_id,
			policy,
			source=source,
			reason=reason,
			batch_id=batch_id,
			batch_size=getattr(decision, "size", 0),
			rearm_latched=bool(existing.rearm_latched) if same_batch else False,
			rearm_latched_at=existing.rearm_latched_at if same_batch else None,
			rearm_armed_by=existing.rearm_armed_by if same_batch else None,
			max_age_seconds=(
				(
					self._override_manager.max_age_seconds_for(entity_id)
					if confirmed_bulk
					else self._quieted_max_age(entity_state)
				)
				if policy == EXTERNAL_POLICY_REARM_AFTER_CLEAR
				else None
			),
			max_age_action=(
				(
					self._override_manager.max_age_action_for(entity_id)
					if confirmed_bulk
					else self._quieted_max_age_action(entity_state)
				)
				if policy == EXTERNAL_POLICY_REARM_AFTER_CLEAR
				else DEFAULT_QUIETED_MAX_AGE_ACTION
			),
			max_age_reached_at=existing.max_age_reached_at if same_batch else None,
		)
		return policy

	def _clear_external_override(self, entity_id: str, reason: str) -> None:
		"""Clear the entity-scoped override shared by every controlling entry."""
		self._override_manager.clear(entity_id, reason)

	def _loaded_controllers_for_entity(
		self,
		entity_id: str,
	) -> list[PresenceBasedLightingCoordinator]:
		"""Return loaded coordinators controlling an entity in stable order."""
		controllers = {self.entry.entry_id: self}
		domain_data = self.hass.data.get(DOMAIN, {})
		for entry_id in self._override_manager.entries_for(entity_id):
			candidate = domain_data.get(entry_id)
			if (
				isinstance(candidate, PresenceBasedLightingCoordinator)
				and entity_id in candidate._entity_states
			):
				controllers[entry_id] = candidate
		return [controllers[entry_id] for entry_id in sorted(controllers)]

	def _clear_scheduled_reset_override(self, entity_id: str) -> bool:
		"""Clear a non-admin PAUSE at the scheduled auto-reenable reset."""
		record = self._override_manager.get(entity_id)
		if record is None or not record.is_paused or record.source == SOURCE_ADMIN:
			return False

		_LOGGER.info(
			"Scheduled reset releasing shared pause on %s (source=%s): %s",
			entity_id,
			record.source,
			record.reason,
		)
		self._clear_external_override(
			entity_id,
			"scheduled auto re-enable reset",
		)
		return True

	def _handle_external_override_changed(self, entity_id: str) -> None:
		"""Sync local automation state to the entity-scoped override record.

		Invoked for *every* config entry controlling the entity, including paired
		profiles whose activation gate is currently closed, so a later gate flip
		cannot hand control to a profile that never saw the override.
		"""
		entity_state = self._entity_states.get(entity_id)
		if entity_state is None:
			return
		if not self._entity_honors_external_override(entity_state):
			return

		record = self._override_manager.get(entity_id)
		if (
			record is not None
			and entity_state["state"] == EntityAutomationState.PAUSED
			and (entity_state.get("pause") or {}).get("source")
			in _EXPLICIT_PAUSE_SOURCES
		):
			return
		if record is None:
			if entity_state["state"] == EntityAutomationState.QUIETED:
				self._exit_quieted(entity_id, entity_state, "external override cleared")
			elif (
				entity_state["state"] == EntityAutomationState.PAUSED
				and (entity_state.get("pause") or {}).get("source") in _EXTERNAL_PAUSE_SOURCES
			):
				self.set_automation_paused(
					entity_id,
					False,
					reason="external override cleared",
					source="external_override",
				)
			return

		if record.is_quieted:
			self._enter_quieted(entity_id, entity_state, record)
		elif record.is_paused:
			self.set_automation_paused(
				entity_id,
				True,
				reason=record.reason,
				source="external_override",
			)

	def _enter_quieted(
		self, entity_id: str, entity_state: dict, record: Any,
	) -> None:
		"""Hold an entity dark after a bulk command without stranding it.

		Entering QUIETED never emits a service call: the bulk command already
		turned the entity off, and re-asserting anything here would either fight
		that command or bounce the light straight back on.
		"""
		pause_source = (
			"external_batch"
			if record.source == SOURCE_HOMEKIT_BATCH
			else "external_override"
		)
		if entity_state["state"] == EntityAutomationState.QUIETED:
			entity_state["pause"] = self._build_pause_metadata(
				entity_id, entity_state, record.reason, pause_source,
			)
			self._notify_switch(entity_id)
			self._schedule_paused_state_save()
			return

		self._bump_entity_work_generation(entity_state, "bulk command quieted")
		self._cancel_entity_timer(entity_state)
		self._cancel_entity_actuation(entity_state, "bulk command quieted")
		entity_state["pause"] = self._build_pause_metadata(
			entity_id, entity_state, record.reason, pause_source,
		)
		self._set_entity_intent(
			entity_id,
			entity_state,
			DesiredState.NONE,
			None,
			None,
			IntentReason.PAUSED,
			False,
		)
		self._set_entity_state(
			entity_id,
			entity_state,
			EntityAutomationState.QUIETED,
			"bulk command quieted",
		)
		self._notify_switch(entity_id)
		self._schedule_paused_state_save()

	def _exit_quieted(self, entity_id: str, entity_state: dict, reason: str) -> None:
		"""Release a quieted hold. Never turns the entity on by itself."""
		if entity_state["state"] != EntityAutomationState.QUIETED:
			return
		self._bump_entity_work_generation(entity_state, reason)
		self._cancel_entity_timer(entity_state)
		self._cancel_entity_actuation(entity_state, reason)
		entity_state["pause"] = None
		self._set_entity_state(entity_id, entity_state, EntityAutomationState.IDLE, reason)
		self._notify_switch(entity_id)
		self._schedule_paused_state_save()

	def get_quieted(self, entity_id: str) -> bool:
		"""Return whether this entity is holding dark after a bulk command."""
		return self._entity_states[entity_id]["state"] == EntityAutomationState.QUIETED

	def _arm_rearm_latches_for_vacancy(self) -> None:
		"""Arm rearm latches once the room is genuinely vacant.

		Arming only records that vacancy happened; the entity stays dark until a
		subsequent rising presence edge.
		"""
		for entity_id, entity_state in self._entity_states.items():
			if entity_state["state"] != EntityAutomationState.QUIETED:
				continue
			if self._override_manager.arm_rearm_latch(entity_id, "room vacant"):
				_LOGGER.debug(
					"[%s] Rearm latch armed; awaiting fresh presence before resuming",
					entity_id,
				)

	def _release_latched_quieted_entities(self) -> None:
		"""Release quieted holds whose rearm latch was armed by real vacancy."""
		for entity_id, entity_state in self._entity_states.items():
			if entity_state["state"] != EntityAutomationState.QUIETED:
				continue
			record = self._override_manager.get(entity_id)
			if record is None or not record.rearm_latched:
				continue
			_LOGGER.debug(
				"[%s] Rising presence after vacancy; releasing quieted hold",
				entity_id,
			)
			self._clear_external_override(entity_id, "rising presence after vacancy")

	def _handle_batch_confirmed(
		self,
		batch: Any,
		new_target_entity_id: str | None = None,
	) -> None:
		"""Apply the configured bulk policy to every targeted managed entity."""
		if not self._batch_observer.enforcing or batch.service != "turn_off":
			return

		self._override_manager.update_confirmed_batch_size(
			batch.batch_id,
			batch.size,
		)
		affected_entities: set[str] = set()
		target_entity_ids = (
			{new_target_entity_id}
			if new_target_entity_id is not None
			else batch.entity_ids
		)
		for target_entity_id in target_entity_ids:
			affected_entities.update(self._expand_target_entities(target_entity_id))
		affected_entities.intersection_update(self._entity_states)

		for entity_id in affected_entities:
			entity_state = self._entity_states[entity_id]
			pause_source = (entity_state.get("pause") or {}).get("source")
			if (
				entity_state["state"] == EntityAutomationState.PAUSED
				and pause_source not in _EXTERNAL_PAUSE_SOURCES
			):
				continue
			policy = self._override_manager.bulk_policy_for(entity_id)
			rearm_latched = (
				policy == EXTERNAL_POLICY_REARM_AFTER_CLEAR
				and self._can_clear_room()
			)
			if self._override_manager.upsert_confirmed_batch(
				batch.batch_id,
				entity_id,
				policy,
				source=SOURCE_HOMEKIT_BATCH,
				reason=f"bulk {batch.service} across {batch.size} entities",
				batch_size=batch.size,
				rearm_latched=rearm_latched,
				rearm_armed_by="already clear at batch" if rearm_latched else None,
				max_age_seconds=(
					self._override_manager.max_age_seconds_for(entity_id)
					if policy == EXTERNAL_POLICY_REARM_AFTER_CLEAR
					else None
				),
				max_age_action=self._override_manager.max_age_action_for(entity_id),
			):
				self._emit_command_intent(
					entity_id,
					SOURCE_HOMEKIT_BATCH,
					policy,
					batch,
					"confirmed bulk command",
				)

		blocked_commands = self._batch_observer.pop_blocked_for_batch(
			batch.batch_id,
			set(self._entity_states),
		)
		if blocked_commands:
			task = asyncio.create_task(
				self._replay_blocked_batch_commands(blocked_commands)
			)
			self._batch_replay_tasks.add(task)
			task.add_done_callback(self._batch_replay_tasks.discard)

	def _handle_blocked_interceptor_command(
		self,
		entity_id: str,
		service: str,
		context: Context | None,
		data: dict,
	) -> bool:
		"""Record a blocked external OFF for possible whole-home replay."""
		service_data = {"entity_id": entity_id}
		params = data.get("params")
		if isinstance(params, dict):
			service_data.update(params)
		else:
			service_data.update(
				{
					key: value
					for key, value in data.items()
					if key != "entity_id"
				}
			)
		target_state = self._entity_states[entity_id]["config"].get(
			CONF_PRESENCE_CLEARED_STATE,
			DEFAULT_CLEARED_STATE,
		)
		return self._batch_observer.record_blocked_command(
			getattr(context, "id", None),
			entity_id,
			entity_id.split(".", 1)[0],
			service,
			target_state,
			service_data,
		)

	async def _replay_blocked_batch_commands(
		self,
		commands: list[BlockedCommand],
	) -> None:
		"""Replay early blocked HomeKit batch commands exactly once."""
		await asyncio.sleep(0)
		for command in commands:
			current = self.hass.states.get(command.entity_id)
			if current is not None and current.state == command.target_state:
				continue
			context = Context(parent_id=command.context_id)
			self._register_command_context(
				command.entity_id,
				context,
				command.target_state,
			)
			try:
				await self.hass.services.async_call(
					command.domain,
					command.service,
					command.service_data,
					blocking=True,
					context=context,
				)
			except Exception as err:
				_LOGGER.warning(
					"[%s] Failed to replay confirmed bulk %s: %s",
					command.entity_id,
					command.service,
					err,
				)

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

	def _is_clearing_authority_occupied(self) -> bool:
		"""Return true only with positive occupied evidence from clear sensors."""
		clearing = getattr(self, "_clearing_sensors", None)
		if not clearing:
			clearing = set(
				self.entry.data.get(CONF_CLEARING_SENSORS, [])
				or self.entry.data.get(CONF_PRESENCE_SENSORS, [])
			)
		return any(is_entity_on(self.hass, sensor) for sensor in clearing)

	def _can_clear_room(self) -> bool:
		"""Return true when the configured clearing sensors say the room is clear."""
		return self._are_clearing_sensors_clear()

	def _activation_catchup_allowed(self) -> bool:
		"""Return whether existing occupancy may activate after a gate opens."""
		if not self.entry.data.get(CONF_ACTIVATION_CONDITIONS):
			return self._is_any_occupied()
		mode = self.entry.data.get(
			CONF_ACTIVATION_CATCHUP_MODE,
			DEFAULT_ACTIVATION_CATCHUP_MODE,
		)
		if mode == ACTIVATION_CATCHUP_NONE:
			return False
		if mode == ACTIVATION_CATCHUP_CLEARING_AUTHORITY:
			return not self._are_clearing_sensors_clear()
		return mode == ACTIVATION_CATCHUP_ANY_TRIGGER and self._is_any_occupied()

	def _entry_is_active(self) -> bool:
		"""Return whether this config entry's activation gate is open."""
		return self._are_activation_conditions_met()

	def _are_activation_conditions_met(self) -> bool:
		"""Check if all activation conditions are satisfied (AND gate).

		If no activation conditions are configured, returns True (always allow).
		If any activation condition is off/false, returns False.
		All conditions must be on/true for lights to activate.
		"""
		conditions = getattr(self, '_activation_conditions', None)
		if conditions is None:
			conditions = set(self.entry.data.get(CONF_ACTIVATION_CONDITIONS, []))
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

	async def _clear_pending_activation_after_vacancy(
		self,
		entity_id: str,
		entity_state: dict,
	) -> None:
		"""Clear a pending entity while preserving delay if it is still on."""
		current_state = self.hass.states.get(entity_id)
		cleared_state = entity_state["config"].get(
			CONF_PRESENCE_CLEARED_STATE,
			STATE_OFF,
		)
		if current_state is not None and current_state.state == cleared_state:
			await self._apply_service_intent(
				entity_id,
				entity_state,
				CONF_PRESENCE_CLEARED_SERVICE,
				IntentReason.CLEARING,
			)
			return
		await self._start_entity_off_timer(entity_id, entity_state)

	async def _start_entity_off_timer(self, entity_id: str, entity_state: dict) -> None:
		"""Start (or restart) the off-timer for a single entity → CLEARING."""
		self._cancel_entity_timer(entity_state)
		self._cancel_entity_actuation(entity_state, "start off timer")
		generation = self._bump_entity_work_generation(entity_state, "start off timer")

		config = entity_state["config"]
		delay = config.get(CONF_ENTITY_OFF_DELAY)
		if delay is None:
			delay = self.entry.data.get(CONF_OFF_DELAY, DEFAULT_OFF_DELAY)

		self._set_entity_state(entity_id, entity_state, EntityAutomationState.CLEARING, f"off-timer started ({delay}s)")
		task = asyncio.create_task(self._execute_entity_off_timer(entity_id, entity_state, delay, generation))
		entity_state["off_timer"] = task

	async def _execute_entity_off_timer(
		self, entity_id: str, entity_state: dict, delay: int, generation: int | None = None,
	) -> None:
		"""Execute the off timer for a specific entity.

		When timer fires:
		- If clear conditions are met → transition to IDLE (turn off)
		- If not clear → transition to WAITING_FOR_CLEAR (event-driven recovery)
		"""
		this_task = asyncio.current_task()
		try:
			_LOGGER.debug("[%s] Off timer sleeping %ds (state: CLEARING)", entity_id, delay)
			while True:
				await asyncio.sleep(delay)
				if not self._entity_work_generation_matches(entity_id, entity_state, generation, "off timer"):
					return

				if self._can_clear_room():
					if self._cleared_intent_blocked_by_presence(entity_state):
						_LOGGER.debug(
							"[%s] Timer fired, but presence sensors still occupied -> keeping off timer active",
							entity_id,
						)
						if delay <= 0:
							await asyncio.sleep(1)
						continue
					_LOGGER.debug("[%s] Timer fired, clear conditions met → cleared intent", entity_id)
					await self._apply_service_intent(entity_id, entity_state, CONF_PRESENCE_CLEARED_SERVICE, IntentReason.CLEARING)
					return

				_LOGGER.debug(
					"[%s] Timer fired, clear conditions NOT all met → WAITING_FOR_CLEAR",
					entity_id,
				)
				self._set_entity_state(
					entity_id, entity_state, EntityAutomationState.WAITING_FOR_CLEAR,
					"timer fired, sensors not clear"
				)
				return
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
			self._publish_ownership(entity_id, entity_state)
			return
		entity_state["state"] = new_state
		entity_state["state_entered_at"] = dt_util.utcnow()
		self._publish_ownership(entity_id, entity_state)
		_LOGGER.debug(
			"[%s] %s → %s (%s)",
			entity_id, old_state.value, new_state.value, reason,
		)
		self._notify_switch(entity_id)

	def _publish_ownership(self, entity_id: str, entity_state: dict) -> None:
		"""Publish whether this active entry still needs the shared entity on."""
		owning_state = entity_state["state"] in (
			EntityAutomationState.OCCUPIED,
			EntityAutomationState.CLEARING,
			EntityAutomationState.WAITING_FOR_CLEAR,
			EntityAutomationState.SETTLING_ON,
		)
		self._ownership_manager.set_desired_on(
			self.entry.entry_id,
			entity_id,
			owning_state and self._entry_is_active(),
		)

	def _entity_may_enforce_presence_lock(self, entity_id: str) -> bool:
		"""Return whether this entity may currently enforce Presence Lock."""
		entity_state = self._entity_states.get(entity_id)
		if entity_state is None:
			return False
		if not self._entry_is_active():
			return False
		# A quieted entity is deliberately holding dark after a whole-home
		# command. It must not enforce Presence Lock (including through the
		# interceptor), regardless of the manual-override setting.
		if entity_state["state"] == EntityAutomationState.QUIETED:
			return False
		if not self._presence_lock_respects_manual_override(entity_state):
			return True
		return (
			self._presence_switch_allows_entity(entity_state)
			and entity_state["state"] not in (
				EntityAutomationState.PAUSED,
				EntityAutomationState.QUIETED,
			)
		)

	def _cancel_entity_timer(self, entity_state: dict) -> None:
		"""Cancel any running off-timer / safety-timer for an entity."""
		timer = entity_state.get("off_timer")
		if timer is not None:
			timer.cancel()
			entity_state["off_timer"] = None

	def _entity_work_generation(self, entity_state: dict) -> int:
		"""Return the current generation for delayed work tied to this entity."""
		return int(entity_state.get("work_generation", 0))

	def _bump_entity_work_generation(self, entity_state: dict, reason: str) -> int:
		"""Invalidate delayed work that captured an older generation."""
		generation = self._entity_work_generation(entity_state) + 1
		entity_state["work_generation"] = generation
		_LOGGER.debug(
			"[%s] work generation advanced to %d (%s)",
			entity_state["config"].get(CONF_ENTITY_ID, "unknown"),
			generation,
			reason,
		)
		return generation

	def _entity_work_generation_matches(
		self,
		entity_id: str,
		entity_state: dict,
		generation: int | None,
		work_type: str,
	) -> bool:
		"""Return whether delayed work still belongs to the current entity generation."""
		if generation is None:
			return True
		current_generation = self._entity_work_generation(entity_state)
		if generation == current_generation:
			return True
		_LOGGER.debug(
			"[%s] Skipping stale %s for generation %s; current generation is %d",
			entity_id,
			work_type,
			generation,
			current_generation,
		)
		return False

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
		if entity_state["state"] in (
			EntityAutomationState.PAUSED,
			EntityAutomationState.QUIETED,
		):
			return

		cur = entity_state["state"]
		occupied = self._is_any_occupied()
		conditions_met = self._are_activation_conditions_met()
		clearing_clear = self._can_clear_room()

		if occupied and conditions_met:
			if cur not in (EntityAutomationState.OCCUPIED, EntityAutomationState.CLEARING, EntityAutomationState.SETTLING_ON):
				if (
					cur in (
						EntityAutomationState.IDLE,
						EntityAutomationState.PENDING_ACTIVATION,
					)
					and not self._activation_catchup_allowed()
				):
					self._cancel_entity_timer(entity_state)
					self._cancel_entity_actuation(
						entity_state,
						"activation catch-up suppressed",
					)
					self._set_entity_state(
						entity_id,
						entity_state,
						EntityAutomationState.PENDING_ACTIVATION,
						"activation catch-up suppressed",
					)
					self._set_entity_intent(
						entity_id,
						entity_state,
						DesiredState.NONE,
						None,
						None,
						IntentReason.CONDITIONS,
						False,
					)
					return
				self._cancel_entity_timer(entity_state)
				self._cancel_entity_actuation(entity_state, "reconcile occupied")
				self._set_entity_state(entity_id, entity_state, EntityAutomationState.OCCUPIED, "reconcile: occupied + conditions met")
				await self._apply_service_intent(entity_id, entity_state, CONF_PRESENCE_DETECTED_SERVICE, IntentReason.PRESENCE)
				# If clear conditions are already met, start timer immediately
				if clearing_clear:
					await self._start_entity_off_timer(entity_id, entity_state)
			else:
				current_ha_state = self.hass.states.get(entity_state["config"][CONF_ENTITY_ID])
				detected_state = entity_state["config"].get(CONF_PRESENCE_DETECTED_STATE, STATE_ON)
				if current_ha_state and current_ha_state.state != detected_state:
					_LOGGER.info(
						"[%s] Reconciliation: occupied but entity is %s, reapplying detected intent",
						entity_id,
						current_ha_state.state,
					)
					await self._apply_service_intent(entity_id, entity_state, CONF_PRESENCE_DETECTED_SERVICE, IntentReason.PRESENCE)
		elif occupied and not conditions_met:
			# Conditions gate control, not the current physical state. Keep the
			# light unchanged while relinquishing ownership until the gate reopens.
			if cur in (
				EntityAutomationState.CLEARING,
				EntityAutomationState.SETTLING_OFF,
				EntityAutomationState.WAITING_FOR_CLEAR,
			):
				self._publish_ownership(entity_id, entity_state)
			elif cur != EntityAutomationState.PENDING_ACTIVATION:
				self._cancel_entity_timer(entity_state)
				self._cancel_entity_actuation(
					entity_state,
					"reconcile: activation conditions not met",
				)
				self._set_entity_state(entity_id, entity_state, EntityAutomationState.PENDING_ACTIVATION, "reconcile: occupied, conditions not met")
			else:
				self._publish_ownership(entity_id, entity_state)
		else:
			# Room is empty
			if cur in (EntityAutomationState.OCCUPIED, EntityAutomationState.PENDING_ACTIVATION, EntityAutomationState.SETTLING_ON):
				# Start off-timer to turn off after delay
				if cur in (EntityAutomationState.OCCUPIED, EntityAutomationState.SETTLING_ON):
					await self._start_entity_off_timer(entity_id, entity_state)
				elif clearing_clear:
					await self._clear_pending_activation_after_vacancy(
						entity_id,
						entity_state,
					)
				else:
					self._publish_ownership(entity_id, entity_state)
			elif cur == EntityAutomationState.WAITING_FOR_CLEAR and clearing_clear:
				self._cancel_entity_timer(entity_state)
				await self._apply_service_intent(entity_id, entity_state, CONF_PRESENCE_CLEARED_SERVICE, IntentReason.CLEARING)
			elif cur == EntityAutomationState.IDLE:
				# Check if light is still on despite room being empty (e.g., after
				# re-enabling presence_allowed). Start the normal off-timer so startup
				# reconciliation keeps the configured delay semantics.
				current_ha_state = self.hass.states.get(entity_state["config"][CONF_ENTITY_ID])
				detected_state = entity_state["config"].get(CONF_PRESENCE_DETECTED_STATE, "on")
				if (
					self._entry_is_active()
					and current_ha_state
					and current_ha_state.state == detected_state
					and clearing_clear
				):
					self._set_entity_state(entity_id, entity_state, EntityAutomationState.OCCUPIED, "reconcile: light on but room empty")
					await self._start_entity_off_timer(entity_id, entity_state)

	async def _periodic_reconciliation(self, _now: datetime) -> None:
		"""Safety-net called every _RECONCILIATION_INTERVAL.

		Catches any state inconsistency that slipped through event-driven
		handling (e.g., missed events, transient sensor blips).
		"""
		try:
			now = dt_util.utcnow()
			for entity_id, action in self._override_manager.apply_max_age_safeguard():
				_LOGGER.info(
					"Quieted hold for %s exceeded its max age; action=%s",
					entity_id,
					action,
				)
			for entity_id, es in self._entity_states.items():
				if await self._recover_taskless_pending_actuation(
					entity_id,
					es,
				):
					continue

				if not self._presence_switch_allows_entity(es):
					continue

				if es["state"] == EntityAutomationState.QUIETED:
					continue

				cur = es["state"]

				# WAITING_FOR_CLEAR safety timeout
				if cur == EntityAutomationState.WAITING_FOR_CLEAR:
					if self._can_clear_room():
						_LOGGER.info(
							"[%s] Reconciliation: WAITING_FOR_CLEAR but clear conditions are met → IDLE",
							entity_id,
						)
						self._cancel_entity_timer(es)
						await self._apply_service_intent(entity_id, es, CONF_PRESENCE_CLEARED_SERVICE, IntentReason.CLEARING)
					else:
						# Check if we've been waiting too long
						entered = es.get("state_entered_at")
						if entered and (now - entered).total_seconds() > _WAITING_FOR_CLEAR_MAX_SECONDS:
							self._cancel_entity_timer(es)
							# If trigger or clearing sensors still show the room as
							# occupied, transition back to OCCUPIED instead of forcing IDLE.
							if self._is_any_occupied() or not self._are_clearing_sensors_clear():
								if self._entry_is_active():
									_LOGGER.info(
										"[%s] WAITING_FOR_CLEAR for >%ds, but room still occupied → OCCUPIED",
										entity_id, _WAITING_FOR_CLEAR_MAX_SECONDS,
									)
									self._set_entity_state(entity_id, es, EntityAutomationState.OCCUPIED, "reconciliation: safety timeout but still occupied")
									await self._apply_service_intent(entity_id, es, CONF_PRESENCE_DETECTED_SERVICE, IntentReason.PRESENCE)
								else:
									self._publish_ownership(entity_id, es)
							else:
								_LOGGER.warning(
									"[%s] WAITING_FOR_CLEAR for >%ds, forcing cleared actuation after clear conditions were met",
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

				elif (
					cur in (
						EntityAutomationState.SETTLING_OFF,
						EntityAutomationState.SETTLING_ON,
					)
					and not self._actuation_is_currently_pending(es)
				):
					if (
						es["actuation"]["status"] == ActuationStatus.FAILED
						and es["actuation"].get("service_key")
					):
						service_key = es["actuation"]["service_key"]
						reason = es["intent"].get("reason", IntentReason.NONE)
						if reason == IntentReason.NONE:
							reason = (
								IntentReason.PRESENCE
								if service_key == CONF_PRESENCE_DETECTED_SERVICE
								else IntentReason.CLEARING
							)
						await self._apply_service_intent(
							entity_id,
							es,
							service_key,
							reason,
							presence_lock_override=bool(
								es["intent"].get("presence_lock_override")
							),
						)
					else:
						if es["actuation"]["status"] == ActuationStatus.PENDING:
							self._cancel_entity_actuation(
								es,
								"stale actuation generation",
							)
						self._recover_entity_after_invalid_actuation(entity_id, es)
						await self._reconcile_entity(entity_id, es)

				# OCCUPIED but room is actually empty and clear conditions are met
				elif cur == EntityAutomationState.OCCUPIED:
					if not self._is_any_occupied() and self._can_clear_room():
						_LOGGER.info(
							"[%s] Reconciliation: OCCUPIED but room empty + clear conditions met → starting off-timer",
							entity_id,
						)
						await self._start_entity_off_timer(entity_id, es)
					elif self._is_any_occupied():
						await self._reconcile_entity(entity_id, es)

				# IDLE but room is occupied (missed a presence event?)
				elif cur == EntityAutomationState.IDLE:
					if (
						self._are_activation_conditions_met()
						and self._activation_catchup_allowed()
					):
						_LOGGER.info(
							"[%s] Reconciliation: IDLE but room occupied + conditions met → OCCUPIED",
							entity_id,
						)
						await self._reconcile_entity(entity_id, es)
					elif self._entry_is_active() and self._can_clear_room():
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
					if (
						self._are_activation_conditions_met()
						and self._activation_catchup_allowed()
					):
						_LOGGER.info(
							"[%s] Reconciliation: PENDING but conditions met → OCCUPIED",
							entity_id,
						)
						await self._reconcile_entity(entity_id, es)
					elif not self._is_any_occupied():
						await self._reconcile_entity(entity_id, es)

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
			released_shared_pause = (
				self._clear_scheduled_reset_override(entity_id)
				if self._entity_honors_external_override(entity_state)
				else False
			)
			local_state_changed = False

			if not entity_state["presence_allowed"]:
				_LOGGER.info("Re-enabling presence lighting for %s in %s", entity_id, room_name)
				self._set_presence_allowed_value(entity_id, entity_state, True)
				local_state_changed = True

			# Also resume automation if paused
			if entity_state["state"] == EntityAutomationState.PAUSED:
				_LOGGER.info("Resuming automation for %s in %s", entity_id, room_name)
				self.set_automation_paused(
					entity_id,
					False,
					reason="auto re-enable",
					source="auto_reenable",
				)
				local_state_changed = True

			if not released_shared_pause and not local_state_changed:
				continue

			controllers = (
				self._loaded_controllers_for_entity(entity_id)
				if released_shared_pause
				else [self]
			)
			await asyncio.gather(
				*(controller._save_paused_state() for controller in controllers)
			)
			for controller in controllers:
				await controller._reconcile_entity(
					entity_id,
					controller._entity_states[entity_id],
				)

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
