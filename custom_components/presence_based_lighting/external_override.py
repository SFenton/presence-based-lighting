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
	DOMAIN,
	EXTERNAL_POLICY_IGNORE,
	EXTERNAL_POLICY_PAUSE,
	EXTERNAL_POLICY_REARM_AFTER_CLEAR,
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
	max_age_seconds: float | None = None

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
		"""Return the ISO timestamp at which the rearm latch arms defensively."""
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

	# ------------------------------------------------------------------
	# Registration
	# ------------------------------------------------------------------

	def register_entity(
		self,
		entry_id: str,
		entity_id: str,
		listener: Callable[[str], None] | None = None,
	) -> None:
		"""Register that a config entry controls this entity."""
		self._entities.setdefault(entity_id, set()).add(entry_id)
		if listener is not None:
			self._listeners.setdefault(entity_id, {})[entry_id] = listener

	def unregister_entry(self, entry_id: str) -> None:
		"""Drop registrations owned by an unloaded config entry."""
		for entity_id in list(self._entities):
			self._entities[entity_id].discard(entry_id)
			if not self._entities[entity_id]:
				self._entities.pop(entity_id, None)
		for entity_id in list(self._listeners):
			self._listeners[entity_id].pop(entry_id, None)
			if not self._listeners[entity_id]:
				self._listeners.pop(entity_id, None)

	def entries_for(self, entity_id: str) -> set[str]:
		"""Return every config entry controlling this entity."""
		return set(self._entities.get(entity_id, set()))

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
		max_age_seconds: float | None = None,
		notify: bool = True,
	) -> ExternalOverrideRecord | None:
		"""Record an entity-scoped override and notify every controlling entry."""
		if policy == EXTERNAL_POLICY_IGNORE:
			return None
		record = ExternalOverrideRecord(
			entity_id=entity_id,
			policy=policy,
			source=source,
			reason=reason,
			created_dt=dt_util.utcnow(),
			created_monotonic=self._now(),
			batch_id=batch_id,
			batch_size=batch_size,
			max_age_seconds=max_age_seconds,
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
		batch_id: str | None = None,
		batch_size: int = 0,
		max_age_seconds: float | None = None,
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
			max_age_seconds=max_age_seconds,
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
		)
		_LOGGER.debug("Rearm latch armed for %s (%s)", entity_id, reason)
		self._notify(entity_id)
		return True

	def upgrade_batch(
		self,
		batch_id: str,
		context_ids: set[str],
		entity_ids: set[str],
		policy: str,
		*,
		source: str,
		reason: str,
		max_age_seconds: float | None = None,
	) -> list[str]:
		"""Promote already-recorded overrides belonging to a confirmed batch.

		A burst is only recognised once enough of its commands have been seen, so
		the first few entities of an all-lights-off may already have been recorded
		under the fallback policy. Re-classify them rather than deferring every
		decision behind a timer.
		"""
		upgraded: list[str] = []
		for entity_id in entity_ids:
			record = self._overrides.get(entity_id)
			if record is None or record.policy == policy:
				continue
			if record.batch_id not in (None, batch_id):
				continue
			self._overrides[entity_id] = replace(
				record,
				policy=policy,
				source=source,
				reason=reason,
				batch_id=batch_id,
				batch_size=len(entity_ids),
				rearm_latched=False,
				rearm_latched_at=None,
				max_age_seconds=max_age_seconds,
			)
			upgraded.append(entity_id)
			self._notify(entity_id)
		if upgraded:
			_LOGGER.info(
				"Upgraded %d override(s) to %s for batch %s: %s",
				len(upgraded),
				policy,
				batch_id,
				sorted(upgraded),
			)
		return upgraded

	def apply_max_age_safeguard(self) -> list[str]:
		"""Arm the rearm latch on quieted holds that outlived their max age.

		This only arms the latch; it never reconciles and never turns a light on,
		so a stuck occupancy sensor cannot produce a surprise 02:48 relight.
		"""
		now = self._now()
		armed: list[str] = []
		for entity_id, record in list(self._overrides.items()):
			if not record.is_quieted or record.rearm_latched:
				continue
			if record.is_past_max_age(now):
				if self.arm_rearm_latch(entity_id, reason="max age safeguard"):
					armed.append(entity_id)
		return armed

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
