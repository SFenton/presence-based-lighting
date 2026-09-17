"""Redacted diagnostics for Presence Based Lighting."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .control_lease import get_control_lease_manager


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return bounded lease diagnostics without user, alarm, or context data."""
    manager = get_control_lease_manager(hass)
    await manager.async_initialize()
    return {
        "entry_id": entry.entry_id,
        "control_leases": manager.diagnostics_for_entry(entry.entry_id),
    }
