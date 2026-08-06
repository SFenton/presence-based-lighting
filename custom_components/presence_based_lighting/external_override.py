"""Entity-scoped external override tracking shared by every config entry.

An override caused by an external action on a *controlled entity* is a fact
about that entity, not about one config entry. Paired room profiles (for
example "Master Bathroom" and "Master Bathroom (Master Bedroom Lights Off)")
control the same light behind opposing activation gates, so an override
recorded by whichever profile happened to be active must be visible to the
other. Otherwise a gate flip hands control to a profile that never saw the
override and it immediately resurrects the light.

Explicit per-entry controls (the Presence Allowed switch, or ``pause_automation``
targeting a specific PBL switch) intentionally stay entry-local and are not
represented here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import TYPE_CHECKING, Callable

from homeassistant.util import dt as dt_util

from .const import (
	DEFAULT_BULK_COMMAND_POLICY,
	DEFAULT_QUIETED_MAX_AGE,
	DEFAULT_QUIETED_MAX_AGE_ACTION,
	DOMAIN,
	EXTERNAL_POLICY_IGNORE,
	EXTERNAL_POLICY_PAUSE,
	EXTERNAL_POLICY_REARM_AFTER_CLEAR,
	QUIETED_MAX_AGE_ACTION_ARM,
	QUIETED_MAX_AGE_ACTION_DIAGNOSTIC,
	QUIETED_MAX_AGE_ACTION_PAUSE,
	SOURCE_HOMEKIT_BATCH,
	SOURCE_HOMEKIT_SINGLE,
	SOURCE_UNKNOWN,
)

if TYPE_CHECKING:
	from homeassistant.core import HomeAssistant

MANAGER_KEY = "_external_override_manager"

_LOGGER = logging.getLogger(__package__)


def _parse_iso_datetime(value: str) -> datetime | None:
	"""Parse a persisted ISO timestamp, normalising to an aware UTC datetime."""
	try:
		parsed = datetime.fromisoformat(value)
	except (TypeError, ValueError):
		return None
	if parsed.tzinfo is None:
		return parsed.replace(tzinfo=timezone.utc)
	return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ExternalOverrideRecord:
	"""One entity-scoped external override."""

	entity_id: str
	policy: str
	source: str
	reason: str
	created_dt: object
	created_monotonic: float
	batch_id: str | None = None
	batch_size: int = 0
	rearm_latched: bool = False
	rearm_latched_at: str | None = None
	rearm_armed_by: str | None = None
	max_age_seconds: float | None = None
	max_age_action: str = DEFAULT_QUIETED_MAX_AGE_ACTION
	max_age_reached_at: str | None = None

	@property
	def created_at(self) -> str:
		"""Return the ISO timestamp this override was recorded."""
		return self.created_dt.isoformat()

	@property
	def is_quieted(self) -> bool:
		"""Return whether this override is a rearm-after-clear hold."""
		return self.policy == EXTERNAL_POLICY_REARM_AFTER_CLEAR

	@property
	def is_paused(self) -> bool:
		"""Return whether this override is an indefinite pause."""
		return self.policy == EXTERNAL_POLICY_PAUSE

	def expires_at(self) -> str | None:
		"""Return the ISO timestamp at which this hold becomes stale."""
		if not self.max_age_seconds:
			return None
		return (self.created_dt + timedelta(seconds=self.max_age_seconds)).isoformat()

	def is_past_max_age(self, now_monotonic: float) -> bool:
		"""Return whether the defensive max-age safeguard has elapsed."""
		if not self.max_age_seconds:
			return False
		return (now_monotonic - self.created_monotonic) >= self.max_age_seconds


class ExternalOverrideManager:
	"""Domain-wide registry of entity-scoped external overrides."""

	def __init__(self, time_source: Callable[[], float] | None = None) -> None:
		self._now = time_source or monotonic
		self._overrides: dict[str, ExternalOverrideRecord] = {}
		self._entities: dict[str, set[str]] = {}
		self._listeners: dict[str, dict[str, Callable[[str], None]]] = {}
		self._unknown_counts: dict[str, int] = {}
		self._entity_configs: dict[str, dict[str, dict[str, object]]] = {}

	# ------------------------------------------------------------------
	# Registration
	# ------------------------------------------------------------------

	def register_entity(
		self,
		entry_id: str,
		entity_id: str,
		listener: Callable[[str], None] | None = None,
		*,
		bulk_policy: str = DEFAULT_BULK_COMMAND_POLICY,
		max_age_seconds: float | None = DEFAULT_QUIETED_MAX_AGE,
		max_age_action: str = DEFAULT_QUIETED_MAX_AGE_ACTION,
	) -> None:
		"""Register that a config entry controls this entity."""
		self._entities.setdefault(entity_id, set()).add(entry_id)
		self._entity_configs.setdefault(entity_id, {})[entry_id] = {
			"bulk_policy": bulk_policy,
			"max_age_seconds": max_age_seconds,
			"max_age_action": max_age_action,
		}
		if listener is not None:
			self._listeners.setdefault(entity_id, {})[entry_id] = listener

	def unregister_entry(self, entry_id: str) -> None:
		"""Drop registrations owned by an unloaded config entry."""
		for entity_id in list(self._entities):
			self._entities[entity_id].discard(entry_id)
			if not self._entities[entity_id]:
				self._entities.pop(entity_id, None)
		for entity_id in list(self._entity_configs):
			self._entity_configs[entity_id].pop(entry_id, None)
			if not self._entity_configs[entity_id]:
				self._entity_configs.pop(entity_id, None)
		for entity_id in list(self._listeners):
			self._listeners[entity_id].pop(entry_id, None)
			if not self._listeners[entity_id]:
				self._listeners.pop(entity_id, None)

	def entries_for(self, entity_id: str) -> set[str]:
		"""Return every config entry controlling this entity."""
		return set(self._entities.get(entity_id, set()))

	def bulk_policy_for(self, entity_id: str) -> str:
		"""Return the strictest configured bulk-command policy for an entity."""
		policies = {
			str(config.get("bulk_policy", DEFAULT_BULK_COMMAND_POLICY))
			for config in self._entity_configs.get(entity_id, {}).values()
		}
		if EXTERNAL_POLICY_PAUSE in policies:
			return EXTERNAL_POLICY_PAUSE
		return EXTERNAL_POLICY_REARM_AFTER_CLEAR

	def max_age_action_for(self, entity_id: str) -> str:
		"""Return the safest configured stale-hold action for an entity."""
		actions = {
			str(config.get("max_age_action", DEFAULT_QUIETED_MAX_AGE_ACTION))
			for config in self._entity_configs.get(entity_id, {}).values()
		}
		if QUIETED_MAX_AGE_ACTION_PAUSE in actions:
			return QUIETED_MAX_AGE_ACTION_PAUSE
		if QUIETED_MAX_AGE_ACTION_DIAGNOSTIC in actions:
			return QUIETED_MAX_AGE_ACTION_DIAGNOSTIC
		return QUIETED_MAX_AGE_ACTION_ARM

	def max_age_seconds_for(self, entity_id: str) -> float | None:
		"""Return the earliest positive stale-hold threshold for an entity."""
		values: list[float] = []
		for config in self._entity_configs.get(entity_id, {}).values():
			try:
				value = float(config.get("max_age_seconds", DEFAULT_QUIETED_MAX_AGE))
			except (TypeError, ValueError):
				continue
			if value > 0:
				values.append(value)
		return min(values) if values else None

	# ------------------------------------------------------------------
	# Override lifecycle
	# ------------------------------------------------------------------

	def get(self, entity_id: str) -> ExternalOverrideRecord | None:
		"""Return the current override for an entity, if any."""
		return self._overrides.get(entity_id)

	def unknown_source_count(self, entity_id: str) -> int:
		"""Return how many unattributable external commands this entity saw."""
		return self._unknown_counts.get(entity_id, 0)

	def note_source(self, entity_id: str, source: str) -> None:
		"""Track source coverage so unknown attribution stays observable."""
		if source == SOURCE_UNKNOWN:
			self._unknown_counts[entity_id] = self._unknown_counts.get(entity_id, 0) + 1

	def set_override(
		self,
		entity_id: str,
		policy: str,
		*,
		source: str = SOURCE_UNKNOWN,
		reason: str = "external control",
		batch_id: str | None = None,
		batch_size: int = 0,
		rearm_latched: bool = False,
		rearm_latched_at: str | None = None,
		rearm_armed_by: str | None = None,
		max_age_seconds: float | None = None,
		max_age_action: str = DEFAULT_QUIETED_MAX_AGE_ACTION,
		max_age_reached_at: str | None = None,
		notify: bool = True,
	) -> ExternalOverrideRecord | None:
		"""Record an entity-scoped override and notify every controlling entry."""
		if policy == EXTERNAL_POLICY_IGNORE:
			return None
		existing = self._overrides.get(entity_id)
		same_batch = (
			existing is not None
			and batch_id is not None
			and existing.batch_id == batch_id
		)
		record = ExternalOverrideRecord(
			entity_id=entity_id,
			policy=policy,
			source=source,
			reason=reason,
			created_dt=existing.created_dt if same_batch else dt_util.utcnow(),
			created_monotonic=(
				existing.created_monotonic if same_batch else self._now()
			),
			batch_id=batch_id,
			batch_size=batch_size,
			rearm_latched=rearm_latched,
			rearm_latched_at=rearm_latched_at,
			rearm_armed_by=rearm_armed_by,
			max_age_seconds=max_age_seconds,
			max_age_action=max_age_action,
			max_age_reached_at=max_age_reached_at,
		)
		self._overrides[entity_id] = record
		self.note_source(entity_id, source)
		_LOGGER.debug(
			"External override recorded for %s: policy=%s source=%s batch=%s (%s)",
			entity_id,
			policy,
			source,
			batch_id,
			reason,
		)
		if notify:
			self._notify(entity_id)
		return record

	def clear(self, entity_id: str, reason: str = "cleared", notify: bool = True) -> bool:
		"""Clear an entity's override. Returns True if one was present."""
		record = self._overrides.pop(entity_id, None)
		if record is None:
			return False
		_LOGGER.debug("External override cleared for %s (%s)", entity_id, reason)
		if notify:
			self._notify(entity_id)
		return True

	def restore_override(
		self,
		entity_id: str,
		policy: str,
		*,
		source: str = SOURCE_UNKNOWN,
		reason: str = "override restored",
		created_at: str | None = None,
		rearm_latched: bool = False,
		rearm_latched_at: str | None = None,
		rearm_armed_by: str | None = None,
		batch_id: str | None = None,
		batch_size: int = 0,
		max_age_seconds: float | None = None,
		max_age_action: str = DEFAULT_QUIETED_MAX_AGE_ACTION,
		max_age_reached_at: str | None = None,
		notify: bool = True,
	) -> ExternalOverrideRecord | None:
		"""Re-adopt a persisted override without resetting its age.

		``set_override`` stamps "now", which would restart the max-age safeguard
		on every Home Assistant restart and let a hold outlive its budget
		indefinitely. Here the original creation time is replayed and the
		monotonic anchor is back-dated by the elapsed wall-clock time, so the
		safeguard measures the true age of the hold.
		"""
		if policy == EXTERNAL_POLICY_IGNORE:
			return None

		now_dt = dt_util.utcnow()
		created_dt = now_dt
		elapsed = 0.0
		if created_at:
			parsed = _parse_iso_datetime(created_at)
			if parsed is not None:
				created_dt = parsed
				# A backwards clock jump, a restored backup, or a future
				# timestamp must never make a hold look older than it is; clamp
				# to zero so the safeguard errs toward keeping the hold.
				elapsed = max(0.0, (now_dt - parsed).total_seconds())
			else:
				_LOGGER.warning(
					"Could not parse persisted override timestamp %r for %s; "
					"restarting its max-age budget",
					created_at,
					entity_id,
				)

		record = ExternalOverrideRecord(
			entity_id=entity_id,
			policy=policy,
			source=source,
			reason=reason,
			created_dt=created_dt,
			created_monotonic=self._now() - elapsed,
			batch_id=batch_id,
			batch_size=batch_size,
			rearm_latched=rearm_latched,
			rearm_latched_at=rearm_latched_at,
			rearm_armed_by=rearm_armed_by,
			max_age_seconds=max_age_seconds,
			max_age_action=max_age_action,
			max_age_reached_at=max_age_reached_at,
		)
		self._overrides[entity_id] = record
		_LOGGER.debug(
			"External override restored for %s: policy=%s age=%.1fs latched=%s",
			entity_id,
			policy,
			elapsed,
			rearm_latched,
		)
		if notify:
			self._notify(entity_id)
		return record

	def arm_rearm_latch(self, entity_id: str, reason: str = "vacancy observed") -> bool:
		"""Arm the rearm latch on a quieted hold. Never turns anything on."""
		record = self._overrides.get(entity_id)
		if record is None or not record.is_quieted or record.rearm_latched:
			return False
		self._overrides[entity_id] = replace(
			record,
			rearm_latched=True,
			rearm_latched_at=dt_util.utcnow().isoformat(),
			rearm_armed_by=reason,
		)
		_LOGGER.debug("Rearm latch armed for %s (%s)", entity_id, reason)
		self._notify(entity_id)
		return True

	def upsert_confirmed_batch(
		self,
		batch_id: str,
		entity_id: str,
		policy: str,
		*,
		source: str,
		reason: str,
		batch_size: int,
		rearm_latched: bool = False,
		rearm_armed_by: str | None = None,
		max_age_seconds: float | None = None,
		max_age_action: str = DEFAULT_QUIETED_MAX_AGE_ACTION,
	) -> bool:
		"""Create or promote the entity's hold for a confirmed bulk command."""
		record = self._overrides.get(entity_id)
		if (
			record is not None
			and record.is_paused
			and record.batch_id not in (batch_id,)
			and record.source not in {SOURCE_HOMEKIT_BATCH, SOURCE_HOMEKIT_SINGLE, SOURCE_UNKNOWN}
			and policy == EXTERNAL_POLICY_REARM_AFTER_CLEAR
		):
			return False
		if record is not None and record.batch_id == batch_id and record.rearm_latched:
			rearm_latched = True
			rearm_armed_by = record.rearm_armed_by
		self.set_override(
			entity_id,
			policy,
			source=source,
			reason=reason,
			batch_id=batch_id,
			batch_size=batch_size,
			rearm_latched=rearm_latched,
			rearm_latched_at=dt_util.utcnow().isoformat() if rearm_latched else None,
			rearm_armed_by=rearm_armed_by,
			max_age_seconds=max_age_seconds,
			max_age_action=max_age_action,
		)
		return True

	def update_confirmed_batch_size(self, batch_id: str, batch_size: int) -> None:
		"""Refresh retained batch diagnostics without re-entering entity states."""
		for entity_id, record in list(self._overrides.items()):
			if record.batch_id != batch_id or record.batch_size == batch_size:
				continue
			self._overrides[entity_id] = replace(record, batch_size=batch_size)

	def apply_max_age_safeguard(self) -> list[tuple[str, str]]:
		"""Apply each stale quieted hold's configured one-shot action."""
		now = self._now()
		changed: list[tuple[str, str]] = []
		for entity_id, record in list(self._overrides.items()):
			if not record.is_quieted or record.max_age_reached_at:
				continue
			if record.is_past_max_age(now):
				reached_at = dt_util.utcnow().isoformat()
				action = record.max_age_action or DEFAULT_QUIETED_MAX_AGE_ACTION
				if action == QUIETED_MAX_AGE_ACTION_ARM:
					self._overrides[entity_id] = replace(
						record,
						rearm_latched=True,
						rearm_latched_at=reached_at,
						rearm_armed_by="max age",
						max_age_reached_at=reached_at,
					)
				elif action == QUIETED_MAX_AGE_ACTION_PAUSE:
					self._overrides[entity_id] = replace(
						record,
						policy=EXTERNAL_POLICY_PAUSE,
						rearm_latched=False,
						rearm_latched_at=None,
						rearm_armed_by=None,
						max_age_reached_at=reached_at,
						reason=f"{record.reason}; quieted max age reached",
					)
				else:
					action = QUIETED_MAX_AGE_ACTION_DIAGNOSTIC
					self._overrides[entity_id] = replace(
						record,
						max_age_reached_at=reached_at,
					)
				_LOGGER.info(
					"Quieted hold max age reached for %s; action=%s",
					entity_id,
					action,
				)
				self._notify(entity_id)
				changed.append((entity_id, action))
		return changed

	# ------------------------------------------------------------------

	def _notify(self, entity_id: str) -> None:
		for listener in list(self._listeners.get(entity_id, {}).values()):
			try:
				listener(entity_id)
			except Exception as err:  # pragma: no cover - defensive
				_LOGGER.exception("Error notifying override listener for %s: %s", entity_id, err)


def get_external_override_manager(hass: HomeAssistant) -> ExternalOverrideManager:
	"""Return the domain-wide external override manager."""
	domain_data = hass.data.setdefault(DOMAIN, {})
	manager = domain_data.get(MANAGER_KEY)
	if not isinstance(manager, ExternalOverrideManager):
		manager = ExternalOverrideManager()
		domain_data[MANAGER_KEY] = manager
	return manager


__all__ = [
	"ExternalOverrideManager",
	"ExternalOverrideRecord",
	"get_external_override_manager",
]
