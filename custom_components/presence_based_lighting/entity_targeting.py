"""Helpers for normalizing and matching entity targets."""
from __future__ import annotations

from typing import Any


def as_entity_list(value: Any) -> list[str]:
	"""Normalize a service target field to a list of entity ids."""
	if not value:
		return []
	if isinstance(value, str):
		return [value]
	if isinstance(value, (list, tuple, set)):
		return [item for item in value if isinstance(item, str)]
	return []


def slugify_entity_id(value: str) -> str:
	"""Small local slugifier matching the switch entity naming style."""
	return "_".join(value.lower().replace(".", "_").replace("-", "_").split())


def legacy_room_switch_entity_id(room_name: str) -> str:
	"""Return the legacy room-level automation switch entity id."""
	return f"switch.{slugify_entity_id(room_name)}_presence_lighting"