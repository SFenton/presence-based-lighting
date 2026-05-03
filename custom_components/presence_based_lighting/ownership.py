"""Shared controlled-entity ownership tracking."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN

MANAGER_KEY = "_ownership_manager"


class PresenceEntityOwnershipManager:
	"""Track which config entries still want shared controlled entities on."""

	def __init__(self) -> None:
		self._desired_on: dict[str, dict[str, bool]] = {}

	def register_entity(self, entry_id: str, entity_id: str) -> None:
		self._desired_on.setdefault(entity_id, {})[entry_id] = False

	def unregister_entry(self, entry_id: str) -> None:
		for entity_id in list(self._desired_on):
			self._desired_on[entity_id].pop(entry_id, None)
			if not self._desired_on[entity_id]:
				self._desired_on.pop(entity_id, None)

	def set_desired_on(self, entry_id: str, entity_id: str, desired_on: bool) -> None:
		self._desired_on.setdefault(entity_id, {})[entry_id] = desired_on

	def other_entry_wants_on(self, entry_id: str, entity_id: str) -> bool:
		return any(
			other_entry_id != entry_id and desired_on
			for other_entry_id, desired_on in self._desired_on.get(entity_id, {}).items()
		)


def get_ownership_manager(hass: HomeAssistant) -> PresenceEntityOwnershipManager:
	"""Return the domain-wide ownership manager."""
	domain_data = hass.data.setdefault(DOMAIN, {})
	manager = domain_data.get(MANAGER_KEY)
	if not isinstance(manager, PresenceEntityOwnershipManager):
		manager = PresenceEntityOwnershipManager()
		domain_data[MANAGER_KEY] = manager
	return manager