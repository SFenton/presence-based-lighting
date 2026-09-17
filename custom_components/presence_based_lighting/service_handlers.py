"""Service registration and routing for Presence Based Lighting."""

from __future__ import annotations

import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.core import SupportsResponse

from .const import AUTOMATION_CONTROL_STATES
from .const import CONF_ROOM_NAME
from .const import CONTROL_LEASE_ALLOWED_OWNERS
from .const import CONTROL_LEASE_MAX_OCCURRENCE_IDS
from .const import CONTROL_LEASE_MAX_TARGET_ENTITY_IDS
from .const import CONTROL_LEASE_MAX_TTL_SECONDS
from .const import CONTROL_LEASE_MIN_TTL_SECONDS
from .const import CONTROL_LEASE_RELEASE_CAUSES
from .const import DOMAIN
from .control_lease import get_control_lease_manager
from .entity_targeting import as_entity_list
from .entity_targeting import legacy_room_switch_entity_id

_LOGGER = logging.getLogger(__package__)

SERVICE_RESUME_AUTOMATION = "resume_automation"
SERVICE_PAUSE_AUTOMATION = "pause_automation"
SERVICE_RESUME_ALL_AUTOMATION = "resume_all_automation"
SERVICE_SET_AUTOMATION_STATE = "set_automation_state"
SERVICE_ACQUIRE_CONTROL = "acquire_control"
SERVICE_DISPATCH_CONTROL = "dispatch_control"
SERVICE_RELEASE_CONTROL = "release_control"
SERVICE_MANUAL_CONTROL = "manual_control"

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): vol.Any(cv.entity_id, [cv.entity_id]),
    }
)

RESUME_ALL_SCHEMA = vol.Schema({})
SET_AUTOMATION_STATE_SCHEMA = SERVICE_SCHEMA.extend(
    {
        vol.Required("state"): vol.In(AUTOMATION_CONTROL_STATES),
    }
)
_OPAQUE_ID = vol.All(str, vol.Length(min=1, max=128))
ACQUIRE_CONTROL_SCHEMA = SERVICE_SCHEMA.extend(
    {
        vol.Optional("controlled_entity_id"): cv.entity_id,
        vol.Required("lease_id"): _OPAQUE_ID,
        vol.Required("controller_id"): _OPAQUE_ID,
        vol.Required("request_id"): _OPAQUE_ID,
        vol.Required("owner"): vol.In(CONTROL_LEASE_ALLOWED_OWNERS),
        vol.Required("occurrence_ids"): vol.All(
            [_OPAQUE_ID],
            vol.Length(min=1, max=CONTROL_LEASE_MAX_OCCURRENCE_IDS),
        ),
        vol.Required("ttl_seconds"): vol.All(
            vol.Coerce(float),
            vol.Range(
                min=CONTROL_LEASE_MIN_TTL_SECONDS,
                max=CONTROL_LEASE_MAX_TTL_SECONDS,
            ),
        ),
        vol.Required("target_entity_ids"): vol.All(
            vol.Any(cv.entity_id, [cv.entity_id]),
            lambda value: as_entity_list(value),
            vol.Length(min=1, max=CONTROL_LEASE_MAX_TARGET_ENTITY_IDS),
        ),
    }
)
RELEASE_CONTROL_SCHEMA = SERVICE_SCHEMA.extend(
    {
        vol.Optional("controlled_entity_id"): cv.entity_id,
        vol.Required("lease_id"): _OPAQUE_ID,
        vol.Required("controller_id"): _OPAQUE_ID,
        vol.Required("expected_generation"): vol.All(
            vol.Coerce(int),
            vol.Range(min=1),
        ),
        vol.Required("request_id"): _OPAQUE_ID,
        vol.Required("owner"): vol.In(CONTROL_LEASE_ALLOWED_OWNERS),
        vol.Required("outcome"): vol.In({"completed", "cancelled", "failed"}),
        vol.Required("cause"): vol.In(CONTROL_LEASE_RELEASE_CAUSES),
    }
)
DISPATCH_SERVICE_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional("brightness"): vol.All(vol.Coerce(int), vol.Range(min=1, max=255)),
        vol.Optional("brightness_pct"): vol.All(
            vol.Coerce(float),
            vol.Range(min=1, max=100),
        ),
        vol.Optional("transition"): vol.All(
            vol.Coerce(float),
            vol.Range(min=0, max=60),
        ),
        vol.Optional("color_temp_kelvin"): vol.All(
            vol.Coerce(int),
            vol.Range(min=1000, max=10000),
        ),
    },
    extra=vol.PREVENT_EXTRA,
)
DISPATCH_CONTROL_SCHEMA = SERVICE_SCHEMA.extend(
    {
        vol.Required("controlled_entity_id"): cv.entity_id,
        vol.Required("lease_id"): _OPAQUE_ID,
        vol.Required("controller_id"): _OPAQUE_ID,
        vol.Required("expected_generation"): vol.All(
            vol.Coerce(int),
            vol.Range(min=1),
        ),
        vol.Required("command_id"): _OPAQUE_ID,
        vol.Required("owner"): vol.In(CONTROL_LEASE_ALLOWED_OWNERS),
        vol.Required("target_entity_ids"): vol.All(
            vol.Any(cv.entity_id, [cv.entity_id]),
            lambda value: as_entity_list(value),
            vol.Length(min=1, max=CONTROL_LEASE_MAX_TARGET_ENTITY_IDS),
        ),
        vol.Required("service_data"): DISPATCH_SERVICE_DATA_SCHEMA,
    }
)
MANUAL_CONTROL_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional("brightness"): vol.All(vol.Coerce(int), vol.Range(min=1, max=255)),
        vol.Optional("brightness_pct"): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=100)
        ),
        vol.Optional("color_temp"): vol.All(vol.Coerce(int), vol.Range(min=1, max=100000)),
        vol.Optional("color_temp_kelvin"): vol.All(
            vol.Coerce(int), vol.Range(min=1000, max=10000)
        ),
        vol.Optional("effect"): vol.All(str, vol.Length(min=1, max=64)),
        vol.Optional("flash"): vol.In({"short", "long"}),
        vol.Optional("hs_color"): vol.All([vol.Coerce(float)], vol.Length(min=2, max=2)),
        vol.Optional("rgb_color"): vol.All([vol.Coerce(int)], vol.Length(min=3, max=3)),
        vol.Optional("transition"): vol.All(vol.Coerce(float), vol.Range(min=0, max=60)),
        vol.Optional("xy_color"): vol.All([vol.Coerce(float)], vol.Length(min=2, max=2)),
    },
    extra=vol.PREVENT_EXTRA,
)
MANUAL_CONTROL_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): _OPAQUE_ID,
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("action"): vol.In({"turn_on", "turn_off"}),
        vol.Optional("light_data", default={}): MANUAL_CONTROL_DATA_SCHEMA,
    }
)


def _target_switches_from_call(call: Any) -> list[str]:
    target_switches = []
    if hasattr(call, "target") and call.target:
        target_switches = as_entity_list(call.target.get("entity_id"))
    if target_switches:
        return target_switches

    data_entities = as_entity_list(call.data.get("entity_id"))
    return [entity_id for entity_id in data_entities if entity_id.startswith("switch.")]


def _controlled_entities_from_call(
    call: Any, target_switches: list[str]
) -> set[str] | None:
    data_entities = set(as_entity_list(call.data.get("entity_id")))
    controlled_entities = {
        entity_id for entity_id in data_entities if not entity_id.startswith("switch.")
    }
    if controlled_entities:
        return controlled_entities
    if not target_switches and data_entities:
        return data_entities
    return None


def _fallback_service_target_entities(
    coordinator: Any, target_switches: list[str]
) -> list[str]:
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
        lease_manager = get_control_lease_manager(hass)

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

            matched_entities = coordinator.resolve_service_target_entities(
                target_switches
            )
            if not isinstance(matched_entities, (list, tuple, set)):
                matched_entities = _fallback_service_target_entities(
                    coordinator, target_switches
                )
            if not matched_entities:
                continue

            for entity_id in matched_entities:
                if target_entity_ids is not None and entity_id not in target_entity_ids:
                    continue
                await lease_manager.async_break(
                    entity_id,
                    cause="pause_automation" if paused else "resume_automation",
                    direction="off" if paused else "none",
                    context_classification="admin",
                )
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

    async def handle_set_automation_state(call: Any) -> None:
        """Apply an administrative state to the targeted PBL switch entities."""
        target_switches = _target_switches_from_call(call)
        target_entity_ids = _controlled_entities_from_call(call, target_switches)
        if not target_switches and target_entity_ids:
            target_switches = ["*"]
        if not target_switches:
            _LOGGER.warning("set_automation_state called without target switch")
            return

        control_state = call.data["state"]
        for _entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
            if not isinstance(coordinator, coordinator_type):
                continue
            matched_entities = coordinator.resolve_service_target_entities(
                target_switches
            )
            if not isinstance(matched_entities, (list, tuple, set)):
                matched_entities = _fallback_service_target_entities(
                    coordinator,
                    target_switches,
                )
            for entity_id in matched_entities:
                if target_entity_ids is not None and entity_id not in target_entity_ids:
                    continue
                await coordinator.async_set_automation_control_state(
                    entity_id,
                    control_state,
                )

    async def handle_resume_all_automation(_call: Any) -> None:
        """Clear every pause and quieted hold across all config entries.

        Escape hatch: pauses are entry-local while external overrides are
        entity-scoped, so unsticking a house-wide problem otherwise means
        targeting each room switch individually.
        """
        cleared = 0
        lease_manager = get_control_lease_manager(hass)
        for _entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
            if not isinstance(coordinator, coordinator_type):
                continue
            for entity_id in list(coordinator._entity_states):
                await lease_manager.async_break(
                    entity_id,
                    cause="resume_all_automation",
                    direction="none",
                    context_classification="admin",
                )
                clear_override = getattr(coordinator, "_clear_external_override", None)
                if clear_override is not None:
                    clear_override(entity_id, "resume_all_automation")
                if coordinator.get_automation_paused(entity_id):
                    coordinator.set_automation_paused(
                        entity_id,
                        False,
                        reason="resume_all_automation",
                        source="service",
                    )
                entity_state = coordinator._entity_states[entity_id]
                await coordinator._reconcile_entity(entity_id, entity_state)
                cleared += 1
        _LOGGER.info("resume_all_automation processed %d controlled entities", cleared)

    def _lease_roots_from_call(call: Any) -> list[str]:
        target_switches = _target_switches_from_call(call)
        controlled_entity_id = call.data.get("controlled_entity_id")
        roots: set[str] = set()
        for _entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
            if not isinstance(coordinator, coordinator_type):
                continue
            matched = coordinator.resolve_service_target_entities(target_switches)
            if not isinstance(matched, (list, tuple, set)):
                matched = _fallback_service_target_entities(
                    coordinator,
                    target_switches,
                )
            for entity_id in matched:
                if controlled_entity_id and entity_id != controlled_entity_id:
                    continue
                roots.add(entity_id)
        return sorted(roots)

    async def handle_acquire_control(call: Any) -> dict[str, Any]:
        """Acquire or atomically update one root control lease."""
        roots = _lease_roots_from_call(call)
        if len(roots) != 1:
            return {
                "outcome": "ambiguous_target",
                "lease_id": None,
                "blockers": ["exactly_one_controlled_entity_required"],
                "matched_root_entity_ids": roots,
            }
        return await get_control_lease_manager(hass).async_acquire(
            root_entity_id=roots[0],
            lease_id=call.data["lease_id"],
            controller_id=call.data["controller_id"],
            request_id=call.data["request_id"],
            owner=call.data["owner"],
            occurrence_ids=call.data["occurrence_ids"],
            ttl_seconds=call.data["ttl_seconds"],
            target_entity_ids=call.data["target_entity_ids"],
        )

    async def handle_release_control(call: Any) -> dict[str, Any]:
        """Release only the exact matching root lease."""
        roots = _lease_roots_from_call(call)
        if len(roots) != 1:
            return {
                "outcome": "ambiguous_target",
                "lease_id": call.data["lease_id"],
                "matched_root_entity_ids": roots,
            }
        return await get_control_lease_manager(hass).async_release(
            root_entity_id=roots[0],
            lease_id=call.data["lease_id"],
            controller_id=call.data["controller_id"],
            expected_generation=call.data["expected_generation"],
            request_id=call.data["request_id"],
            owner=call.data["owner"],
            outcome=call.data["outcome"],
            cause=call.data["cause"],
        )

    async def handle_dispatch_control(call: Any) -> dict[str, Any]:
        """Dispatch an exact wake-owned light.turn_on through lease enforcement."""
        roots = _lease_roots_from_call(call)
        if len(roots) != 1:
            return {
                "outcome": "ambiguous_target",
                "lease_id": call.data["lease_id"],
                "matched_root_entity_ids": roots,
            }
        if call.data["owner"] not in CONTROL_LEASE_ALLOWED_OWNERS:
            return {
                "outcome": "blocked",
                "blockers": ["invalid_owner"],
            }
        return await get_control_lease_manager(hass).async_call_with_control_lease(
            root_entity_id=roots[0],
            lease_id=call.data["lease_id"],
            controller_id=call.data["controller_id"],
            expected_generation=call.data["expected_generation"],
            command_id=call.data["command_id"],
            target_entity_ids=call.data["target_entity_ids"],
            service_data=call.data["service_data"],
        )

    async def handle_manual_control(call: Any) -> None:
        """Accept and dispatch one wall-automation manual command."""
        entry_id = call.data["config_entry_id"]
        entity_id = call.data["entity_id"]
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if not isinstance(coordinator, coordinator_type):
            raise ValueError("Unknown Presence Based Lighting config entry")
        if entity_id not in coordinator._entity_states:
            raise ValueError(
                "manual_control requires exactly one configured controlled root"
            )
        await coordinator.async_manual_control(
            entity_id,
            call.data["action"],
            call.data.get("light_data") or {},
            call.context,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESUME_AUTOMATION,
        handle_resume_automation,
        schema=SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PAUSE_AUTOMATION, handle_pause_automation, schema=SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESUME_ALL_AUTOMATION,
        handle_resume_all_automation,
        schema=RESUME_ALL_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_AUTOMATION_STATE,
        handle_set_automation_state,
        schema=SET_AUTOMATION_STATE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ACQUIRE_CONTROL,
        handle_acquire_control,
        schema=ACQUIRE_CONTROL_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RELEASE_CONTROL,
        handle_release_control,
        schema=RELEASE_CONTROL_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISPATCH_CONTROL,
        handle_dispatch_control,
        schema=DISPATCH_CONTROL_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_MANUAL_CONTROL,
        handle_manual_control,
        schema=MANUAL_CONTROL_SCHEMA,
    )
    _LOGGER.debug(
        "Registered %s, %s, %s, %s, %s, %s, %s and %s services",
        SERVICE_RESUME_AUTOMATION,
        SERVICE_PAUSE_AUTOMATION,
        SERVICE_RESUME_ALL_AUTOMATION,
        SERVICE_SET_AUTOMATION_STATE,
        SERVICE_ACQUIRE_CONTROL,
        SERVICE_RELEASE_CONTROL,
        SERVICE_DISPATCH_CONTROL,
        SERVICE_MANUAL_CONTROL,
    )
