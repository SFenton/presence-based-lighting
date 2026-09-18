"""Helpers for normalizing and matching entity targets."""
from __future__ import annotations

import inspect
from typing import Any
from typing import TYPE_CHECKING

from homeassistant.helpers import service as service_helpers

if TYPE_CHECKING:
    from homeassistant.core import ServiceCall


def as_entity_list(value: Any) -> list[str]:
    """Normalize a service target field to a list of entity ids."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if isinstance(item, str)]
    return []


async def async_extract_service_target_entity_ids(
    service_call: ServiceCall,
) -> set[str]:
    """Resolve every canonical Home Assistant service target selector."""
    result = service_helpers.async_extract_entity_ids(service_call, True)
    if inspect.isawaitable(result):
        result = await result
    return set(result)


def slugify_entity_id(value: str) -> str:
    """Small local slugifier matching the switch entity naming style."""
    return "_".join(value.lower().replace(".", "_").replace("-", "_").split())


def expand_structural_targets(hass: Any, entity_ids: Any) -> set[str]:
    """Include nested HA/Z2M group members without depending on an aggregate OFF edge."""
    pending = list(entity_ids)
    expanded: set[str] = set()
    while pending:
        entity_id = pending.pop()
        if not isinstance(entity_id, str) or entity_id in expanded:
            continue
        expanded.add(entity_id)
        state = hass.states.get(entity_id)
        attributes = getattr(state, "attributes", {}) or {}
        for key in ("entity_id", "group_entities"):
            pending.extend(as_entity_list(attributes.get(key)))
    return expanded


def legacy_room_switch_entity_id(room_name: str) -> str:
    """Return the legacy room-level automation switch entity id."""
    return f"switch.{slugify_entity_id(room_name)}_presence_lighting"
