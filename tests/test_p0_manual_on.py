"""Opt-in Presence Lock MANUAL_ON behavior."""

# @covers custom_components/presence_based_lighting/__init__.py

from types import SimpleNamespace

import pytest

from custom_components.presence_based_lighting import (
    EntityAutomationState,
    PresenceBasedLightingCoordinator,
)
from custom_components.presence_based_lighting.command_context import (
    PresenceCommandContextRegistry,
)
from custom_components.presence_based_lighting.const import (
    CONF_AUTOMATION_MODE,
    CONF_CONTROLLED_ENTITIES,
    CONF_PRESENCE_LOCK_MANUAL_ON_OVERRIDE_ENABLED,
    EXTERNAL_POLICY_PAUSE,
    EXTERNAL_POLICY_REARM_AFTER_CLEAR,
    MANUAL_ON_BOUNDARY_AWAIT_CLEAR,
    MANUAL_ON_BOUNDARY_AWAIT_OCCUPANCY,
    MANUAL_ON_INTENT,
)
from custom_components.presence_based_lighting.external_override import (
    ExternalOverrideManager,
)
from homeassistant.const import STATE_OFF, STATE_ON
from tests.conftest import setup_entity_states


def _opt_in(entry):
    entity = entry.data[CONF_CONTROLLED_ENTITIES][0]
    entity[CONF_AUTOMATION_MODE] = "presence_lock"
    entity[CONF_PRESENCE_LOCK_MANUAL_ON_OVERRIDE_ENABLED] = True
    return entry


def _context(*, user_id=None, parent_id=None):
    return SimpleNamespace(id="manual-context", user_id=user_id, parent_id=parent_id)


def _sensor_event(hass, entity_id, old_state, new_state):
    hass.states.set(entity_id, new_state)
    return SimpleNamespace(
        data={
            "entity_id": entity_id,
            "old_state": SimpleNamespace(state=old_state, attributes={}),
            "new_state": SimpleNamespace(state=new_state, attributes={}),
        }
    )


def test_manual_on_manager_is_durable_and_old_records_remain_pause():
    manager = ExternalOverrideManager(time_source=lambda: 10.0)
    manager.register_entity("entry", "light.room", manual_on_enabled=True)
    record = manager.set_override(
        "light.room",
        EXTERNAL_POLICY_PAUSE,
        intent=MANUAL_ON_INTENT,
        intent_version=1,
        owner_entry_id="entry",
        boundary_phase=MANUAL_ON_BOUNDARY_AWAIT_CLEAR,
        notify=False,
    )
    assert record.is_manual_on
    assert record.policy == EXTERNAL_POLICY_PAUSE
    assert manager.manual_on_owner("light.room", "entry") == "entry"

    legacy = manager.restore_override(
        "light.old",
        EXTERNAL_POLICY_PAUSE,
        created_at=record.created_at,
        notify=False,
    )
    assert legacy is not None
    assert not legacy.is_manual_on


def test_manual_on_conflicting_owners_fail_closed():
    manager = ExternalOverrideManager()
    manager.register_entity("entry-a", "light.room", manual_on_enabled=True)
    manager.register_entity("entry-b", "light.room", manual_on_enabled=True)
    assert manager.manual_on_owners("light.room") == {"entry-a", "entry-b"}
    assert manager.manual_on_owner("light.room", "entry-a") is None


@pytest.mark.asyncio
async def test_default_off_preserves_legacy_behavior(mock_hass, mock_config_entry):
    setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
    await coordinator.async_start()
    accepted = await coordinator._accept_manual_control(
        "light.living_room",
        "turn_on",
        _context(user_id="user"),
        source="manual_app",
        light_data={},
    )
    assert accepted is False
    assert coordinator._override_manager.get("light.living_room") is None


@pytest.mark.asyncio
async def test_manual_on_requires_post_command_occupancy_then_clear(
    mock_hass, mock_config_entry
):
    setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, _opt_in(mock_config_entry))
    await coordinator.async_start()
    entity_state = coordinator._entity_states["light.living_room"]
    assert await coordinator._accept_manual_control(
        "light.living_room",
        "turn_on",
        _context(user_id="user"),
        source="manual_app",
        light_data={},
    )
    record = coordinator._override_manager.get("light.living_room")
    assert record.boundary_phase == MANUAL_ON_BOUNDARY_AWAIT_OCCUPANCY
    assert entity_state["state"] == EntityAutomationState.PAUSED

    await coordinator._handle_presence_change(
        _sensor_event(
            mock_hass,
            "binary_sensor.living_room_motion",
            STATE_OFF,
            STATE_ON,
        )
    )
    record = coordinator._override_manager.get("light.living_room")
    assert record.boundary_phase == MANUAL_ON_BOUNDARY_AWAIT_CLEAR

    await coordinator._handle_presence_change(
        _sensor_event(
            mock_hass,
            "binary_sensor.living_room_motion",
            STATE_ON,
            STATE_OFF,
        )
    )
    await entity_state["manual_on_timer"]
    assert coordinator._override_manager.get("light.living_room") is None


@pytest.mark.asyncio
async def test_manual_on_occupied_at_acceptance_awaits_clear(
    mock_hass, mock_config_entry
):
    setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, _opt_in(mock_config_entry))
    await coordinator.async_start()
    assert await coordinator._accept_manual_control(
        "light.living_room",
        "turn_on",
        _context(user_id="user"),
        source="manual_app",
        light_data={},
    )
    assert (
        coordinator._override_manager.get("light.living_room").boundary_phase
        == MANUAL_ON_BOUNDARY_AWAIT_CLEAR
    )


@pytest.mark.asyncio
async def test_newer_manual_off_supersedes_manual_on_with_quieted_hold(
    mock_hass, mock_config_entry
):
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, _opt_in(mock_config_entry))
    await coordinator.async_start()
    context = _context(user_id="user")
    assert await coordinator._accept_manual_control(
        "light.living_room", "turn_on", context, source="manual_app", light_data={}
    )
    assert await coordinator._accept_manual_control(
        "light.living_room", "turn_off", context, source="manual_app", light_data={}
    )
    record = coordinator._override_manager.get("light.living_room")
    assert record.policy == EXTERNAL_POLICY_REARM_AFTER_CLEAR
    assert not record.is_manual_on


def test_direct_user_provenance_excludes_automation_and_broad_targets(mock_hass, mock_config_entry):
    coordinator = PresenceBasedLightingCoordinator(mock_hass, _opt_in(mock_config_entry))
    assert coordinator._manual_command_is_direct_user(
        "light.living_room", "light.living_room", _context(user_id="user")
    )
    assert not coordinator._manual_command_is_direct_user(
        "light.living_room",
        ["light.living_room", "light.other"],
        _context(user_id="user"),
    )
    assert not coordinator._manual_command_is_direct_user(
        "light.living_room",
        "light.living_room",
        _context(user_id="user", parent_id="automation"),
    )


def test_manual_authority_is_one_use():
    registry = PresenceCommandContextRegistry()
    registry.register_manual_authority("ctx", "entry", "light.room", "turn_on")
    context = SimpleNamespace(id="ctx", parent_id=None)
    assert registry.claim_manual_authority("entry", "light.room", context) == "turn_on"
    assert registry.claim_manual_authority("entry", "light.room", context) is None
