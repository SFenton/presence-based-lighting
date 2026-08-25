"""Domain-wide command context tracking for shared controlled entities."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import TYPE_CHECKING

from .const import DOMAIN

if TYPE_CHECKING:
	from homeassistant.core import Context, HomeAssistant

MANAGER_KEY = "_command_context_registry"
DEFAULT_MAX_CONTEXTS = 256
DEFAULT_CONTEXT_TTL_SECONDS = 60.0


class CommandOrigin(Enum):
	"""Origin of a service or state-change context."""

	OWN = "own"
	SIBLING = "sibling"
	EXTERNAL = "external"


@dataclass(frozen=True)
class CommandContextRecord:
	"""One PBL-issued command context."""

	entry_id: str
	entity_id: str
	target_state: str | None
	registered_at: float


class PresenceCommandContextRegistry:
	"""Track recent PBL commands across every config entry.

	Keyed by ``(context_id, entity_id)``: a single Home Assistant context can
	legitimately cover several controlled entities at once (a service call with
	a multi-entity target, or an HA light group forwarding its context to every
	member). Storing one record per context id would let the last registration
	clobber its siblings, and every clobbered entity would then look externally
	controlled to its own coordinator.
	"""

	def __init__(
		self,
		max_contexts: int = DEFAULT_MAX_CONTEXTS,
		ttl_seconds: float = DEFAULT_CONTEXT_TTL_SECONDS,
	) -> None:
		self._max_contexts = max_contexts
		self._ttl_seconds = ttl_seconds
		self._contexts: OrderedDict[str, dict[str, CommandContextRecord]] = OrderedDict()

	def register(
		self,
		context_id: str | None,
		entry_id: str,
		entity_id: str,
		target_state: str | None,
	) -> None:
		"""Register a context before its Home Assistant service call is issued."""
		if not context_id:
			return
		self._purge_expired()
		records = self._contexts.pop(context_id, None) or {}
		records[entity_id] = CommandContextRecord(
			entry_id=entry_id,
			entity_id=entity_id,
			target_state=target_state,
			registered_at=monotonic(),
		)
		self._contexts[context_id] = records
		while len(self._contexts) > self._max_contexts:
			self._contexts.popitem(last=False)

	def unregister_entry(self, entry_id: str) -> None:
		"""Remove contexts owned by an unloaded config entry."""
		for context_id in list(self._contexts):
			records = self._contexts[context_id]
			for entity_id in list(records):
				if records[entity_id].entry_id == entry_id:
					records.pop(entity_id, None)
			if not records:
				self._contexts.pop(context_id, None)

	def entities_for_context(self, context_id: str | None) -> set[str]:
		"""Return every entity registered under one context id."""
		if not context_id:
			return set()
		return set(self._contexts.get(context_id, {}))

	def classify(
		self,
		entry_id: str,
		entity_id: str,
		context: Context | None,
		*,
		include_parent: bool,
		expected_target_state: str | None = None,
	) -> CommandOrigin:
		"""Classify a context relative to one coordinator."""
		if context is None:
			return CommandOrigin.EXTERNAL
		self._purge_expired()

		context_ids = [getattr(context, "id", None)]
		if include_parent:
			context_ids.append(getattr(context, "parent_id", None))

		for context_id in context_ids:
			if not context_id:
				continue
			record = self._contexts.get(context_id, {}).get(entity_id)
			if record is None:
				continue
			if (
				expected_target_state is not None
				and record.target_state != expected_target_state
			):
				continue
			if record.entry_id == entry_id:
				return CommandOrigin.OWN
			return CommandOrigin.SIBLING
		return CommandOrigin.EXTERNAL

	def _purge_expired(self) -> None:
		"""Discard old command contexts."""
		cutoff = monotonic() - self._ttl_seconds
		for context_id, records in list(self._contexts.items()):
			newest = max(
				(record.registered_at for record in records.values()),
				default=0.0,
			)
			if newest >= cutoff:
				break
			self._contexts.pop(context_id, None)


def get_command_context_registry(
	hass: HomeAssistant,
) -> PresenceCommandContextRegistry:
	"""Return the domain-wide command context registry."""
	domain_data = hass.data.setdefault(DOMAIN, {})
	registry = domain_data.get(MANAGER_KEY)
	if not isinstance(registry, PresenceCommandContextRegistry):
		registry = PresenceCommandContextRegistry()
		domain_data[MANAGER_KEY] = registry
	return registry
