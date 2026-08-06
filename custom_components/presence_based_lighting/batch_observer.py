"""Domain-wide detection of whole-home ("all lights off") command bursts.

Home Assistant's HomeKit bridge creates a *fresh* ``Context`` for every
accessory command (``components/homekit/accessories.py``), fires
``homekit_state_change`` with that context, and then issues the service call
with the same context. There is therefore no native batch identifier: a Siri
"turn off all the lights" arrives as N unrelated single-entity commands.

Measured on this deployment, a native all-lights-off produced 15 commands in
21.46 ms and 16 commands in 25.39 ms, with a widest intra-burst gap of 7.9 ms,
while a single-room HomeKit off is exactly one command. Grouping same-service
commands inside a short window therefore separates the two cases with a very
large margin.

Cardinality is counted in **distinct managed target entities**, never in config
entries: one controlled entity may belong to several paired room profiles, so
counting entries would inflate a singleton into a false batch.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING, Callable

from .const import (
	BATCH_MODE_ENFORCE,
	BATCH_MODE_OBSERVE,
	BATCH_MODE_OFF,
	DEFAULT_BATCH_MIN_DISTINCT_ENTITIES,
	DEFAULT_BATCH_RETAIN_SECONDS,
	DEFAULT_BATCH_WINDOW_MS,
	DEFAULT_HOMEKIT_BATCH_MODE,
	DOMAIN,
	EVENT_HOMEKIT_STATE_CHANGE,
)

if TYPE_CHECKING:
	from homeassistant.core import HomeAssistant

MANAGER_KEY = "_command_batch_observer"

_LOGGER = logging.getLogger(__package__)


@dataclass
class CommandBatch:
	"""A group of same-service commands observed close together in time."""

	batch_id: str
	service: str
	started_at: float
	last_seen_at: float
	context_ids: set[str] = field(default_factory=set)
	entity_ids: set[str] = field(default_factory=set)
	managed_entity_ids: set[str] = field(default_factory=set)
	confirmed: bool = False

	@property
	def size(self) -> int:
		"""Return the batch cardinality used for classification.

		Distinct *target* entity ids, not config entries and not only the subset
		PBL happens to manage: the burst itself is the evidence of whole-home
		intent, and a house with few PBL rooms must still be able to recognise
		one. Keying on entity id also means a controlled entity shared by paired
		room profiles counts exactly once.
		"""
		return len(self.entity_ids)


@dataclass(frozen=True)
class BatchDecision:
	"""Result of resolving a context id against observed command batches."""

	batch_id: str
	service: str
	size: int
	confirmed: bool
	enforcing: bool


class CommandBatchObserver:
	"""Group HomeKit commands into batches and expose context -> batch lookup."""

	def __init__(self, hass: HomeAssistant, time_source: Callable[[], float] | None = None) -> None:
		self.hass = hass
		self._now = time_source or monotonic
		self._mode = DEFAULT_HOMEKIT_BATCH_MODE
		self._window_seconds = DEFAULT_BATCH_WINDOW_MS / 1000.0
		self._retain_seconds = DEFAULT_BATCH_RETAIN_SECONDS
		self._min_distinct_entities = DEFAULT_BATCH_MIN_DISTINCT_ENTITIES
		self._batches: dict[str, CommandBatch] = {}
		self._context_to_batch: dict[str, str] = {}
		self._open_batch_by_service: dict[str, str] = {}
		self._managed_entities: dict[str, set[str]] = {}
		self._entry_configs: dict[str, dict] = {}
		self._listening_entries: set[str] = set()
		self._batch_counter = 0
		self._unsub: Callable[[], None] | None = None
		self._confirm_callbacks: list[
			Callable[[CommandBatch, str | None], None]
		] = []

	# ------------------------------------------------------------------
	# Configuration and lifecycle
	# ------------------------------------------------------------------

	def configure_entry(
		self,
		entry_id: str,
		*,
		mode: str | None = None,
		window_ms: int | None = None,
		retain_seconds: float | None = None,
		min_distinct_entities: int | None = None,
	) -> None:
		"""Record one entry's configuration and recompute effective settings.

		The observer is domain-wide but every config entry carries its own
		settings, so storing the last writer's values made behaviour depend on
		entry setup order. Configuration is kept per entry and reduced
		deterministically instead.
		"""
		self._entry_configs[entry_id] = {
			"mode": mode,
			"window_ms": window_ms,
			"retain_seconds": retain_seconds,
			"min_distinct_entities": min_distinct_entities,
		}
		self._recompute_effective_config()

	def _recompute_effective_config(self) -> None:
		"""Reduce every entry's configuration to one effective setting set.

		Safety rule, applied so the result is independent of entry order:

		* mode: any ``off`` wins, otherwise any ``observe`` wins, otherwise
		  ``enforce``. The least behaviour-changing mode always wins, so one
		  room opting out can never be silently overridden by another.
		* window: the smallest configured window, because a narrower grouping
		  window produces fewer false batches.
		* min distinct entities: the largest configured threshold, because a
		  higher bar produces fewer false batches.
		* retention: the longest configured duration, so a late state echo can
		  still be resolved for whichever entry needs it.
		"""
		modes = [
			config["mode"]
			for config in self._entry_configs.values()
			if config.get("mode") is not None
		]
		if not modes:
			self._mode = DEFAULT_HOMEKIT_BATCH_MODE
		elif BATCH_MODE_OFF in modes:
			self._mode = BATCH_MODE_OFF
		elif BATCH_MODE_OBSERVE in modes:
			self._mode = BATCH_MODE_OBSERVE
		else:
			self._mode = BATCH_MODE_ENFORCE

		windows = [
			max(0.0, float(config["window_ms"]) / 1000.0)
			for config in self._entry_configs.values()
			if config.get("window_ms") is not None
		]
		self._window_seconds = (
			min(windows) if windows else DEFAULT_BATCH_WINDOW_MS / 1000.0
		)

		thresholds = [
			max(1, int(config["min_distinct_entities"]))
			for config in self._entry_configs.values()
			if config.get("min_distinct_entities") is not None
		]
		self._min_distinct_entities = (
			max(thresholds) if thresholds else DEFAULT_BATCH_MIN_DISTINCT_ENTITIES
		)

		retentions = [
			max(0.0, float(config["retain_seconds"]))
			for config in self._entry_configs.values()
			if config.get("retain_seconds") is not None
		]
		self._retain_seconds = (
			max(retentions) if retentions else DEFAULT_BATCH_RETAIN_SECONDS
		)

		self._sync_listener()

	@property
	def effective_config(self) -> dict:
		"""Return the reduced settings, for diagnostics and tests."""
		return {
			"mode": self._mode,
			"window_ms": round(self._window_seconds * 1000.0),
			"retain_seconds": self._retain_seconds,
			"min_distinct_entities": self._min_distinct_entities,
			"configured_entries": sorted(self._entry_configs),
			"listening_entries": sorted(self._listening_entries),
		}

	@property
	def mode(self) -> str:
		"""Return the effective kill-switch mode."""
		return self._mode

	@property
	def enforcing(self) -> bool:
		"""Return whether batch decisions may change automation behaviour."""
		return self._mode == BATCH_MODE_ENFORCE

	@property
	def is_listening(self) -> bool:
		"""Return whether the domain-wide listener is currently attached."""
		return self._unsub is not None

	def register_managed_entity(self, entry_id: str, entity_id: str) -> None:
		"""Record that a config entry controls this entity."""
		self._managed_entities.setdefault(entity_id, set()).add(entry_id)

	def unregister_entry(self, entry_id: str) -> None:
		"""Drop everything owned by an unloaded config entry.

		Also releases the entry's claim on the shared listener; the listener is
		only detached once the last entry is gone, so unloading one room never
		disturbs the others.
		"""
		for entity_id in list(self._managed_entities):
			self._managed_entities[entity_id].discard(entry_id)
			if not self._managed_entities[entity_id]:
				self._managed_entities.pop(entity_id, None)
		self._entry_configs.pop(entry_id, None)
		self._recompute_effective_config()
		self.async_release(entry_id)
	def is_managed(self, entity_id: str) -> bool:
		"""Return whether any config entry controls this entity."""
		return bool(self._managed_entities.get(entity_id))

	def subscribe_confirmed(
		self,
		callback: Callable[[CommandBatch, str | None], None],
	) -> Callable[[], None]:
		"""Subscribe to batch confirmation, returning an unsubscribe callable."""
		self._confirm_callbacks.append(callback)

		def _unsubscribe() -> None:
			if callback in self._confirm_callbacks:
				self._confirm_callbacks.remove(callback)

		return _unsubscribe

	def async_start(self, entry_id: str) -> None:
		"""Claim the shared listener for one entry, attaching it if needed."""
		self._listening_entries.add(entry_id)
		self._sync_listener()

	def async_release(self, entry_id: str) -> None:
		"""Release one entry's claim, tearing down only when the last one goes."""
		if entry_id not in self._listening_entries:
			return
		self._listening_entries.discard(entry_id)
		if self._listening_entries:
			_LOGGER.debug(
				"Entry %s released the %s listener; %d entr(ies) still using it",
				entry_id,
				EVENT_HOMEKIT_STATE_CHANGE,
				len(self._listening_entries),
			)
			self._sync_listener()
			return
		_LOGGER.debug(
			"Last entry (%s) released the %s listener; detaching",
			entry_id,
			EVENT_HOMEKIT_STATE_CHANGE,
		)
		self.async_stop()

	def _sync_listener(self) -> None:
		"""Attach or detach the listener from claims and effective mode.

		Doing this centrally keeps the result independent of the order in which
		config entries happen to be set up: an entry that loads before a sibling
		configures ``off`` still ends up detached, and unloading that sibling
		re-attaches it.
		"""
		should_listen = bool(self._listening_entries) and self._mode != BATCH_MODE_OFF
		if should_listen:
			self._attach_listener()
		else:
			self._detach_listener()

	def _attach_listener(self) -> None:
		if self._unsub is not None:
			return
		try:
			self._unsub = self.hass.bus.async_listen(
				EVENT_HOMEKIT_STATE_CHANGE,
				self._handle_homekit_event,
			)
			_LOGGER.debug(
				"Listening for %s events (claimed by %s)",
				EVENT_HOMEKIT_STATE_CHANGE,
				sorted(self._listening_entries),
			)
		except Exception as err:  # pragma: no cover - defensive
			_LOGGER.warning("Could not listen for %s: %s", EVENT_HOMEKIT_STATE_CHANGE, err)

	def _detach_listener(self) -> None:
		if self._unsub is None:
			return
		try:
			self._unsub()
		except Exception as err:  # pragma: no cover - defensive
			_LOGGER.debug("Error removing %s listener: %s", EVENT_HOMEKIT_STATE_CHANGE, err)
		self._unsub = None

	def async_stop(self) -> None:
		"""Detach the listener and drop all retained state and claims."""
		self._detach_listener()
		self._listening_entries.clear()
		self._batches.clear()
		self._context_to_batch.clear()
		self._open_batch_by_service.clear()
		self._confirm_callbacks.clear()

	# ------------------------------------------------------------------
	# Observation
	# ------------------------------------------------------------------

	async def _handle_homekit_event(self, event) -> None:
		"""Record one HomeKit accessory command."""
		try:
			data = getattr(event, "data", None) or {}
			entity_id = data.get("entity_id")
			service = data.get("service")
			context = getattr(event, "context", None)
			context_id = getattr(context, "id", None)
			if not entity_id or not service or not context_id:
				return
			self.note_command(entity_id, service, context_id)
		except Exception as err:  # pragma: no cover - defensive
			_LOGGER.exception("Error handling %s event: %s", EVENT_HOMEKIT_STATE_CHANGE, err)

	def note_command(self, entity_id: str, service: str, context_id: str) -> CommandBatch | None:
		"""Record a command and return the batch it joined."""
		if self._mode == BATCH_MODE_OFF:
			return None

		now = self._now()
		self._purge(now)

		batch = self._open_batch_for(service, now)
		entity_was_new = entity_id not in batch.entity_ids
		batch.last_seen_at = now
		batch.context_ids.add(context_id)
		batch.entity_ids.add(entity_id)
		if self.is_managed(entity_id):
			batch.managed_entity_ids.add(entity_id)
		self._context_to_batch[context_id] = batch.batch_id

		newly_confirmed = False
		if not batch.confirmed and batch.size >= self._min_distinct_entities:
			batch.confirmed = True
			newly_confirmed = True
			_LOGGER.info(
				"Bulk command detected: %s across %d distinct entities (%d managed) "
				"in %.1f ms (batch %s, mode=%s)",
				service,
				batch.size,
				len(batch.managed_entity_ids),
				(batch.last_seen_at - batch.started_at) * 1000.0,
				batch.batch_id,
				self._mode,
			)
		if newly_confirmed or (batch.confirmed and entity_was_new):
			new_target = None if newly_confirmed else entity_id
			for callback in list(self._confirm_callbacks):
				try:
					callback(batch, new_target)
				except Exception as err:  # pragma: no cover - defensive
					_LOGGER.exception("Error in batch confirmation callback: %s", err)
		return batch

	def _open_batch_for(self, service: str, now: float) -> CommandBatch:
		"""Return the batch a same-service command at ``now`` belongs to."""
		open_id = self._open_batch_by_service.get(service)
		if open_id is not None:
			existing = self._batches.get(open_id)
			if existing is not None and (now - existing.started_at) <= self._window_seconds:
				return existing

		self._batch_counter += 1
		batch = CommandBatch(
			batch_id=f"pblbatch-{self._batch_counter}",
			service=service,
			started_at=now,
			last_seen_at=now,
		)
		self._batches[batch.batch_id] = batch
		self._open_batch_by_service[service] = batch.batch_id
		return batch

	# ------------------------------------------------------------------
	# Resolution
	# ------------------------------------------------------------------

	def classify(self, context_id: str | None) -> BatchDecision | None:
		"""Resolve a context id to its batch, if it was a HomeKit command."""
		if not context_id or self._mode == BATCH_MODE_OFF:
			return None
		self._purge(self._now())
		batch_id = self._context_to_batch.get(context_id)
		if batch_id is None:
			return None
		batch = self._batches.get(batch_id)
		if batch is None:
			return None
		return BatchDecision(
			batch_id=batch.batch_id,
			service=batch.service,
			size=batch.size,
			confirmed=batch.confirmed,
			enforcing=self.enforcing,
		)

	def context_ids_for_batch(self, batch_id: str) -> set[str]:
		"""Return every context id observed for one batch."""
		batch = self._batches.get(batch_id)
		return set(batch.context_ids) if batch else set()

	def _purge(self, now: float) -> None:
		"""Drop batches past the retention horizon."""
		cutoff = now - self._retain_seconds
		for batch_id, batch in list(self._batches.items()):
			if batch.last_seen_at >= cutoff:
				continue
			self._batches.pop(batch_id, None)
			if self._open_batch_by_service.get(batch.service) == batch_id:
				self._open_batch_by_service.pop(batch.service, None)
			for context_id in batch.context_ids:
				if self._context_to_batch.get(context_id) == batch_id:
					self._context_to_batch.pop(context_id, None)


def get_batch_observer(hass: HomeAssistant) -> CommandBatchObserver:
	"""Return the domain-wide command batch observer."""
	domain_data = hass.data.setdefault(DOMAIN, {})
	observer = domain_data.get(MANAGER_KEY)
	if not isinstance(observer, CommandBatchObserver):
		observer = CommandBatchObserver(hass)
		domain_data[MANAGER_KEY] = observer
	return observer


__all__ = [
	"BATCH_MODE_ENFORCE",
	"BATCH_MODE_OBSERVE",
	"BATCH_MODE_OFF",
	"BatchDecision",
	"CommandBatch",
	"CommandBatchObserver",
	"get_batch_observer",
]
