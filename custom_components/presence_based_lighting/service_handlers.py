"""Service registration and routing for Presence Based Lighting."""
from __future__ import annotations

import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.core import HomeAssistant

from .const import CONF_ROOM_NAME, DOMAIN
from .entity_targeting import as_entity_list, legacy_room_switch_entity_id

_LOGGER = logging.getLogger(__package__)

SERVICE_RESUME_AUTOMATION = "resume_automation"
SERVICE_PAUSE_AUTOMATION = "pause_automation"

SERVICE_SCHEMA = vol.Schema({
	vol.Optional("entity_id"): vol.Any(cv.entity_id, [cv.entity_id]),
})


def _target_switches_from_call(call: Any) -> list[str]:
	target_switches = []
	if hasattr(call, "target") and call.target:
		target_switches = as_entity_list(call.target.get("entity_id"))
	if target_switches:
		return target_switches

	data_entities = as_entity_list(call.data.get("entity_id"))
	return [entity_id for entity_id in data_entities if entity_id.startswith("switch.")]


def _controlled_entities_from_call(call: Any, target_switches: list[str]) -> set[str] | None:
	data_entities = set(as_entity_list(call.data.get("entity_id")))
	controlled_entities = {entity_id for entity_id in data_entities if not entity_id.startswith("switch.")}
	if controlled_entities:
		return controlled_entities
	if not target_switches and data_entities:
		return data_entities
	return None


def _fallback_service_target_entities(coordinator: Any, target_switches: list[str]) -> list[str]:
	room_name = coordinator.entry.data.get(CONF_ROOM_NAME, "")
	legacy_switch = legacy_room_switch_entity_id(room_name)
	if "*" in target_switches or legacy_switch in target_switches:
		return list(coordinator._entity_states)
	return []


async def async_register_services(hass: HomeAssistant, coordinator_type: type) -> None:
	"""Register pause/resume automation services."""

	async def _apply_to_service_targets(call: Any, paused: bool) -> None:
		target_switches = _target_switches_from_call(call)
		target_entity_ids = _controlled_entities_from_call(call, target_switches)

		if not target_switches and target_entity_ids:
			target_switches = ["*"]

		if not target_switches:
			_LOGGER.warning(
				"%s_automation called without target switch",
				"pause" if paused else "resume",
			)
			return

		for _entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
			if not isinstance(coordinator, coordinator_type):
				continue

			matched_entities = coordinator.resolve_service_target_entities(target_switches)
			if not isinstance(matched_entities, (list, tuple, set)):
				matched_entities = _fallback_service_target_entities(coordinator, target_switches)
			if not matched_entities:
				continue

			for entity_id in matched_entities:
				if target_entity_ids is not None and entity_id not in target_entity_ids:
					continue
				_LOGGER.debug(
					"%s automation for %s",
					"Pausing" if paused else "Resuming",
					entity_id,
				)
				coordinator.set_automation_paused(entity_id, paused)
				if not paused:
					entity_state = coordinator._entity_states[entity_id]
					await coordinator._reconcile_entity(entity_id, entity_state)

	async def handle_resume_automation(call: Any) -> None:
		"""Handle the resume_automation service call."""
		await _apply_to_service_targets(call, paused=False)

	async def handle_pause_automation(call: Any) -> None:
		"""Handle the pause_automation service call."""
		await _apply_to_service_targets(call, paused=True)

	hass.services.async_register(
		DOMAIN, SERVICE_RESUME_AUTOMATION, handle_resume_automation, schema=SERVICE_SCHEMA
	)
	hass.services.async_register(
		DOMAIN, SERVICE_PAUSE_AUTOMATION, handle_pause_automation, schema=SERVICE_SCHEMA
	)
	_LOGGER.debug("Registered %s and %s services", SERVICE_RESUME_AUTOMATION, SERVICE_PAUSE_AUTOMATION)