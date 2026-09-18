"""Critical owner-safe control lease tests."""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest.mock import MagicMock

import pytest
from custom_components.presence_based_lighting import ActuationStatus
from custom_components.presence_based_lighting import async_migrate_entry
from custom_components.presence_based_lighting import async_setup
from custom_components.presence_based_lighting import EntityAutomationState
from custom_components.presence_based_lighting import IntentReason
from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.command_context import CommandOrigin
from custom_components.presence_based_lighting.const import BATCH_MODE_OBSERVE
from custom_components.presence_based_lighting.const import (
    CONF_CONTROL_LEASE_BLOCKERS,
)
from custom_components.presence_based_lighting.const import (
    CONF_CONTROL_LEASE_CORRECT_LATE_ON,
)
from custom_components.presence_based_lighting.const import CONF_CONTROL_LEASE_MODE
from custom_components.presence_based_lighting.const import (
    CONF_CONTROLLED_ENTITIES,
)
from custom_components.presence_based_lighting.const import CONF_HOMEKIT_BATCH_MODE
from custom_components.presence_based_lighting.const import (
    CONF_PRESENCE_CLEARED_SERVICE,
)
from custom_components.presence_based_lighting.const import (
    CONF_PRESENCE_DETECTED_SERVICE,
)
from custom_components.presence_based_lighting.const import (
    CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
)
from custom_components.presence_based_lighting.const import (
    CONTROL_LEASE_MODE_ENFORCE,
)
from custom_components.presence_based_lighting.const import (
    CONTROL_LEASE_MODE_OBSERVE,
)
from custom_components.presence_based_lighting.const import DOMAIN
from custom_components.presence_based_lighting.const import EVENT_COMMAND_INTENT
from custom_components.presence_based_lighting.const import (
    EVENT_CONTROL_LEASE_REVOKED,
)
from custom_components.presence_based_lighting.const import EVENT_CONTROL_TRANSITION
from custom_components.presence_based_lighting.const import EXTERNAL_POLICY_PAUSE
from custom_components.presence_based_lighting.const import SOURCE_ADMIN
from custom_components.presence_based_lighting.const import SOURCE_HOMEKIT_BATCH
from custom_components.presence_based_lighting.const import SOURCE_HOMEKIT_SINGLE
from custom_components.presence_based_lighting.const import SOURCE_UNKNOWN
from custom_components.presence_based_lighting.control_lease import (
    ControlLeaseManager,
)
from custom_components.presence_based_lighting.control_lease import (
    get_control_lease_manager,
)
from custom_components.presence_based_lighting.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.presence_based_lighting.interceptor import (
    ControlLeaseInterceptor,
)
from custom_components.presence_based_lighting.interceptor import (
    LightNormalizationPolicy,
)
from custom_components.presence_based_lighting.interceptor import LightTurnOnNormalizer
from custom_components.presence_based_lighting.interceptor import (
    PresenceLockInterceptor,
)
from homeassistant.core import ServiceCall
from homeassistant.core import SupportsResponse
from tests.conftest import MockContext
from tests.conftest import MockState
from tests.conftest import MockStore
from tests.conftest import setup_entity_states

ROOT = "light.living_room"
LEAF_A = "light.living_room_left"
LEAF_B = "light.living_room_right"
ROOT_2 = "light.kitchen"
LEAF_C = "light.kitchen_left"
LEAF_D = "light.kitchen_right"


class FakeClock:
    """Controllable wall and monotonic clock."""

    def __init__(self) -> None:
        self.monotonic = 1000.0
        self.utcnow = datetime(2030, 1, 1, tzinfo=timezone.utc)

    def advance(self, seconds: float) -> None:
        self.monotonic += seconds
        self.utcnow += timedelta(seconds=seconds)


def _configure_root(mock_hass, *, state="off") -> None:
    setup_entity_states(mock_hass, lights_state=state, occupancy_state="off")
    mock_hass.states.set(
        ROOT,
        state,
        attributes={"group_entities": [LEAF_A, LEAF_B]},
    )
    mock_hass.states.set(LEAF_A, state)
    mock_hass.states.set(LEAF_B, state)


def _enable_entry(
    entry,
    *,
    mode=CONTROL_LEASE_MODE_ENFORCE,
    blockers=None,
    correct_late_on=False,
):
    config = entry.data[CONF_CONTROLLED_ENTITIES][0]
    config[CONF_CONTROL_LEASE_MODE] = mode
    config[CONF_CONTROL_LEASE_BLOCKERS] = blockers or []
    config[CONF_CONTROL_LEASE_CORRECT_LATE_ON] = correct_late_on
    return entry


def _set_external_pause(coordinator, source=SOURCE_HOMEKIT_BATCH):
    coordinator._override_manager.set_override(
        ROOT,
        EXTERNAL_POLICY_PAUSE,
        source=source,
        reason="qualified asleep manual off",
    )
    assert coordinator.get_automation_paused(ROOT) is True
    assert coordinator._entity_states[ROOT]["pause"]["source"] == "external_override"
    return coordinator._override_manager.get(ROOT)


async def _coordinator(mock_hass, entry, *, enforcement=True):
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await coordinator.async_start()
    coordinator._schedule_paused_state_save = lambda: None
    coordinator._control_lease_manager.set_enforcement_available(enforcement)
    return coordinator


async def _acquire(manager, **overrides):
    payload = {
        "root_entity_id": ROOT,
        "lease_id": "lease-1",
        "controller_id": "wake-master-bedroom",
        "request_id": "request-1",
        "owner": "wake_light",
        "occurrence_ids": ["occurrence-1"],
        "ttl_seconds": 300,
        "target_entity_ids": [LEAF_A, LEAF_B],
    }
    payload.update(overrides)
    return await manager.async_acquire(**payload)


async def _release(manager, **overrides):
    current = manager.get(ROOT)
    payload = {
        "root_entity_id": ROOT,
        "lease_id": "lease-1",
        "controller_id": "wake-master-bedroom",
        "expected_generation": current.generation if current else 1,
        "request_id": "release-1",
        "owner": "wake_light",
        "outcome": "completed",
        "cause": "hold_complete",
    }
    payload.update(overrides)
    return await manager.async_release(**payload)


def _assert_revocation_generation(mock_hass, active_generation, cause):
    transitions = [
        event["data"]
        for event in mock_hass.bus.fired_events(EVENT_CONTROL_TRANSITION)
        if event["data"]["action"] == "revoked" and event["data"]["cause"] == cause
    ]
    revoked = [
        event["data"]
        for event in mock_hass.bus.fired_events(EVENT_CONTROL_LEASE_REVOKED)
        if event["data"]["cause"] == cause
    ]
    assert transitions
    assert revoked
    for payload in (transitions[-1], revoked[-1]):
        assert payload["previous_generation"] == active_generation
        assert payload["generation"] == active_generation + 1
    return transitions[-1]


def _patch_intercept_result(monkeypatch):
    import custom_components.presence_based_lighting.interceptor as interceptor_module

    class Result:
        ALLOW = "allow"
        BLOCK = "block"

    monkeypatch.setattr(
        interceptor_module,
        "InterceptResult",
        Result,
        raising=False,
    )
    return Result


def _turn_on_service_event(context, target_data, service_data):
    turn_on_data = dict(target_data)
    turn_on_data.update(service_data)
    return type(
        "Event",
        (),
        {
            "data": {
                "domain": "light",
                "service": "turn_on",
                "service_data": turn_on_data,
            },
            "context": context,
        },
    )()


def _manual_brightness_service_event(context, target=LEAF_A, service_data=None):
    turn_on_data = (
        {"brightness_pct": 22} if service_data is None else dict(service_data)
    )
    return _turn_on_service_event(
        context,
        {"entity_id": target},
        turn_on_data,
    )


def _interceptor_turn_on_data(target_data, service_data):
    return {**target_data, **service_data}


def _interceptor_service_call(mock_hass, context, data):
    return ServiceCall(
        mock_hass,
        "light",
        "turn_on",
        data,
        context,
    )


def _map_service_target(mock_hass, selector, selector_id, entity_ids):
    mock_hass._service_target_entities[selector][selector_id] = set(entity_ids)


def _controlled_state_event(context, old_state, new_state):
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": ROOT,
                "old_state": MockState(ROOT, old_state),
                "new_state": MockState(ROOT, new_state, context=context),
            }
        },
    )()


@pytest.mark.asyncio
async def test_default_off_and_observe_modes_do_not_suppress(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, mock_config_entry)
    manager = coordinator._control_lease_manager

    disabled = await _acquire(manager)
    assert disabled["outcome"] == "denied"
    assert "control_lease_mode_off" in disabled["blockers"]
    assert coordinator.get_entity_control_state(ROOT)["automation_suppressed"] is False

    coordinator.async_stop()
    mock_hass.data[DOMAIN].pop(mock_config_entry.entry_id)
    observed_entry = _enable_entry(
        mock_config_entry,
        mode=CONTROL_LEASE_MODE_OBSERVE,
    )
    coordinator = await _coordinator(mock_hass, observed_entry)
    observed = await _acquire(coordinator._control_lease_manager)

    assert observed["outcome"] == "would_grant"
    state = coordinator.get_entity_control_state(ROOT)
    assert state["automation_suppressed"] is False
    assert state["control_lease_state"] == "observing"

    assert await coordinator._control_lease_manager.async_break(
        ROOT,
        cause="manual_group_off",
    )
    events = mock_hass.bus.fired_events(EVENT_CONTROL_TRANSITION)
    assert any(event["data"]["action"] == "would_break" for event in events)
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_observe_mode_record_expires_and_terminalizes(mock_hass):
    _configure_root(mock_hass)
    clock = FakeClock()
    manager = ControlLeaseManager(
        mock_hass,
        monotonic_source=lambda: clock.monotonic,
        utcnow_source=lambda: clock.utcnow,
        store=MockStore(mock_hass, 1, "lease-observe-expiry"),
    )
    manager.register_entity("entry", ROOT, mode=CONTROL_LEASE_MODE_OBSERVE)
    observed = await _acquire(manager, ttl_seconds=10)
    assert observed["outcome"] == "would_grant"
    assert manager.attributes_for(ROOT)["control_lease_state"] == "observing"

    clock.advance(11)
    assert await manager.async_expire_due(ROOT) == 1

    attributes = manager.attributes_for(ROOT)
    assert attributes["control_lease_state"] == "inactive"
    assert attributes["control_lease_last_transition"] == "observe_expired"
    terminal = manager.diagnostics_for_entry("entry")["terminal"]
    assert terminal[-1]["action"] == "observe_expired"
    assert terminal[-1]["outcome"] == "expired"


@pytest.mark.asyncio
async def test_v12_migration_adds_default_off_safety_settings(
    mock_hass,
    mock_config_entry,
):
    mock_config_entry.version = 12

    assert await async_migrate_entry(mock_hass, mock_config_entry)

    config = mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0]
    assert mock_config_entry.version == 14
    assert config[CONF_CONTROL_LEASE_MODE] == "off"
    assert config[CONF_CONTROL_LEASE_BLOCKERS] == []
    assert config[CONF_CONTROL_LEASE_CORRECT_LATE_ON] is False


@pytest.mark.asyncio
async def test_enforce_acquire_is_persisted_and_applies_overlay(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    entry = _enable_entry(mock_config_entry)
    coordinator = await _coordinator(mock_hass, entry)
    entity_state = coordinator._entity_states[ROOT]
    entity_state["actuation"]["status"] = ActuationStatus.PENDING
    entity_state["actuation"]["target_state"] = "on"
    result = await _acquire(coordinator._control_lease_manager)

    assert result["outcome"] == "acquired"
    assert result["expires_at"]
    state = coordinator.get_entity_control_state(ROOT)
    assert coordinator.get_entity_automation_state(ROOT) == "idle"
    assert state["automation_suppressed"] is True
    assert state["suppression_kind"] == "leased"
    assert state["control_lease_active_count"] == 1
    assert entity_state["actuation"]["status"] == ActuationStatus.CANCELED
    assert "presence_based_lighting.control_leases" in mock_hass._storage

    await _release(coordinator._control_lease_manager)
    released_state = coordinator.get_entity_control_state(ROOT)
    assert released_state["control_lease_active_count"] == 0
    assert released_state["control_lease_id"] != "lease-1"
    assert len(released_state["control_lease_id"]) == 12
    assert released_state["control_lease_last_transition"] == "released"
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_qualified_asleep_pause_acquires_dispatches_and_compare_clears(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    admitted_override = _set_external_pause(coordinator, SOURCE_HOMEKIT_BATCH)

    acquired = await _acquire(manager)

    assert acquired["outcome"] == "acquired"
    assert acquired["baseline_admitted"] is True
    fingerprint = manager.get(ROOT).baseline_suppressions[0]
    assert fingerprint.override_source == SOURCE_HOMEKIT_BATCH
    assert fingerprint.override_policy == EXTERNAL_POLICY_PAUSE
    assert fingerprint.override_created_at == admitted_override.created_at
    assert fingerprint.pause_source == "external_override"
    assert fingerprint.pause_paused_at
    assert coordinator.get_entity_control_state(ROOT)["suppression_kind"] == "leased"
    dispatched = await manager.async_call_with_control_lease(
        root_entity_id=ROOT,
        lease_id="lease-1",
        controller_id="wake-master-bedroom",
        expected_generation=acquired["generation"],
        command_id="baseline-step",
        target_entity_ids=[LEAF_A, LEAF_B],
        service_data={"brightness_pct": 40, "transition": 1},
    )
    assert dispatched["outcome"] == "dispatched"

    mock_hass.states.set("binary_sensor.living_room_motion", "on")
    mock_hass.services.clear()
    released = await _release(manager)
    await asyncio.sleep(0)

    assert released["baseline_outcome"] == "baseline_compare_cleared"
    assert coordinator._override_manager.get(ROOT) is None
    assert coordinator.get_automation_paused(ROOT) is False
    assert admitted_override.created_at
    assert any(
        call["domain"] == "light"
        and call["service"] == "turn_on"
        and call["service_data"].get("entity_id") == ROOT
        for call in mock_hass.services.calls
    )
    state = coordinator.get_entity_control_state(ROOT)
    assert state["control_lease_baseline_outcome"] == "baseline_compare_cleared"
    release_event = mock_hass.bus.fired_events(EVENT_CONTROL_TRANSITION)[-1]["data"]
    assert release_event["baseline_admitted"] is True
    assert release_event["baseline_outcome"] == "baseline_compare_cleared"
    coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "admin_kind",
    ["service_pause", "source_admin", "quieted", "presence_off"],
)
async def test_admin_suppression_remains_an_acquire_blocker(
    mock_hass,
    mock_config_entry,
    admin_kind,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    if admin_kind == "service_pause":
        coordinator.set_automation_paused(
            ROOT,
            True,
            reason="explicit admin pause",
            source="service",
        )
    elif admin_kind == "source_admin":
        coordinator._override_manager.set_override(
            ROOT,
            EXTERNAL_POLICY_PAUSE,
            source=SOURCE_ADMIN,
            reason="admin selected paused",
        )
    elif admin_kind == "quieted":
        await coordinator.async_set_automation_control_state(ROOT, "quieted")
    else:
        await coordinator.async_set_presence_allowed(ROOT, False)

    result = await _acquire(coordinator._control_lease_manager)

    assert result["outcome"] == "denied"
    expected = {
        "quieted": "automation_quieted",
        "presence_off": "presence_allowed_off",
    }.get(admin_kind, "automation_paused")
    assert expected in result["blockers"]
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_replaced_asleep_override_is_preserved_from_stale_clean_release(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    original = _set_external_pause(coordinator, SOURCE_HOMEKIT_BATCH)
    acquired = await _acquire(manager)

    coordinator._override_manager.set_override(
        ROOT,
        EXTERNAL_POLICY_PAUSE,
        source=SOURCE_HOMEKIT_SINGLE,
        reason="new manual off during wake",
    )
    replacement = coordinator._override_manager.get(ROOT)
    assert replacement is not None
    assert replacement.created_at != original.created_at
    assert manager.get(ROOT) is None
    revoke_event = mock_hass.bus.fired_events(EVENT_CONTROL_TRANSITION)[-1]["data"]
    assert revoke_event["baseline_outcome"] == "baseline_preserved_revoke"

    stale = await _release(
        manager,
        expected_generation=acquired["generation"],
    )

    assert stale["outcome"] == "already_terminal"
    assert coordinator._override_manager.get(ROOT) is replacement
    assert coordinator.get_automation_paused(ROOT) is True
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_cancelled_release_preserves_admitted_asleep_baseline(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    admitted = _set_external_pause(coordinator)
    await _acquire(manager)

    released = await _release(
        manager,
        outcome="cancelled",
        cause="occurrence_cancelled",
    )

    assert released["baseline_outcome"] == "preserved_nonclean_release"
    assert coordinator._override_manager.get(ROOT) is admitted
    assert coordinator.get_automation_paused(ROOT) is True
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_lease_suppresses_strict_presence_lock_corrections(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass, state="on")
    mock_hass.states.set("binary_sensor.living_room_motion", "on")
    config = mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0]
    config["require_vacancy_for_cleared"] = True
    config["presence_lock_respects_manual_override"] = False
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    await _acquire(coordinator._control_lease_manager)
    mock_hass.services.clear()

    reverted = await coordinator._check_and_apply_presence_lock(
        coordinator._entity_states[ROOT],
        "off",
        force_fallback=True,
    )

    assert reverted is False
    assert mock_hass.services.calls == []
    await _release(coordinator._control_lease_manager)
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_enforce_acquire_fails_without_context_enforcement(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(
        mock_hass,
        _enable_entry(mock_config_entry),
        enforcement=False,
    )

    result = await _acquire(coordinator._control_lease_manager)

    assert result["outcome"] == "denied"
    assert "context_enforcement_unavailable" in result["blockers"]
    assert coordinator.get_entity_control_state(ROOT)["automation_suppressed"] is False
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_duplicate_acquire_replaces_occurrences_and_targets_without_extending(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    first = await _acquire(manager, ttl_seconds=60)

    updated = await _acquire(
        manager,
        request_id="request-2",
        occurrence_ids=["occurrence-1", "occurrence-2"],
        target_entity_ids=[LEAF_A],
        ttl_seconds=600,
    )

    assert updated["outcome"] == "updated"
    assert updated["expires_at"] == first["expires_at"]
    assert updated["occurrence_ids"] == ["occurrence-1", "occurrence-2"]
    assert updated["target_entity_ids"] == [LEAF_A]
    assert updated["released_entity_ids"] == [LEAF_B]

    stale_command = await manager.async_call_with_control_lease(
        root_entity_id=ROOT,
        lease_id="lease-1",
        controller_id="wake-master-bedroom",
        expected_generation=first["generation"],
        command_id="stale-step",
        target_entity_ids=[LEAF_A, LEAF_B],
        service_data={"brightness_pct": 20, "transition": 1},
    )
    assert stale_command["outcome"] == "blocked"

    stale_release = await _release(
        manager,
        expected_generation=first["generation"],
    )
    assert stale_release["outcome"] == "token_mismatch"
    assert manager.get(ROOT) is not None

    duplicate = await _acquire(
        manager,
        request_id="request-3",
        occurrence_ids=["occurrence-1", "occurrence-2"],
        target_entity_ids=[LEAF_A],
        ttl_seconds=1200,
    )
    assert duplicate["outcome"] == "duplicate"
    assert duplicate["expires_at"] == first["expires_at"]

    await _release(manager)
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_conflicting_lease_is_denied_and_stale_release_cannot_clear_pause(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    await _acquire(manager)

    conflict = await _acquire(
        manager,
        lease_id="lease-2",
        controller_id="other-controller",
    )
    assert conflict["outcome"] == "conflict"
    assert conflict["blockers"] == ["lease_conflict"]

    await coordinator.async_set_automation_control_state(ROOT, "paused")
    override = coordinator._override_manager.get(ROOT)
    assert override is not None and override.source == "admin"
    assert coordinator.get_automation_paused(ROOT) is True

    stale = await _release(manager)
    assert stale["outcome"] == "already_terminal"
    assert coordinator._override_manager.get(ROOT) is override
    assert coordinator.get_automation_paused(ROOT) is True
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_manual_group_off_breaks_before_existing_pause_and_stale_release(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass, state="on")
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)

    event = type(
        "Event",
        (),
        {
            "data": {
                "domain": "light",
                "service": "turn_off",
                "service_data": {"entity_id": ROOT},
            },
            "context": MockContext("manual-off", user_id="local-user"),
        },
    )()
    await coordinator._handle_service_call(event)

    assert manager.get(ROOT) is None
    _assert_revocation_generation(
        mock_hass,
        acquired["generation"],
        "manual_group_off",
    )
    assert coordinator.get_automation_paused(ROOT) is True
    override = coordinator._override_manager.get(ROOT)
    assert override is not None

    stale = await _release(manager, request_id="late-release")
    assert stale["outcome"] == "already_terminal"
    assert coordinator.get_automation_paused(ROOT) is True
    assert coordinator._override_manager.get(ROOT) is override
    coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "service_data"),
    [
        (ROOT, {"brightness_pct": 18}),
        (LEAF_A, {"brightness": 72}),
        (LEAF_B, {"brightness_step_pct": -10}),
        (ROOT, {"profile": "relax"}),
        (LEAF_A, {"white": 128}),
    ],
    ids=[
        "root-absolute",
        "leaf-absolute",
        "leaf-step",
        "root-profile",
        "leaf-white",
    ],
)
async def test_foreign_brightness_turn_on_pre_revokes_room_and_is_allowed(
    mock_hass,
    mock_config_entry,
    monkeypatch,
    target,
    service_data,
):
    _configure_root(mock_hass, state="on")
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)
    result_type = _patch_intercept_result(monkeypatch)
    interceptor = ControlLeaseInterceptor(mock_hass, manager)
    context = MockContext(
        f"manual-brightness-{target}",
        user_id="local-user",
    )
    data = {
        "entity_id": [target],
        "params": dict(service_data),
    }
    call = _interceptor_service_call(mock_hass, context, data)

    await coordinator._handle_service_call(
        _manual_brightness_service_event(
            context,
            target,
            service_data,
        )
    )
    result = await interceptor._handle_turn_on(call, data)

    assert result == result_type.ALLOW
    assert manager.get(ROOT) is None
    assert coordinator.get_automation_paused(ROOT) is True
    assert coordinator._override_manager.get(ROOT) is not None
    transition = _assert_revocation_generation(
        mock_hass,
        acquired["generation"],
        "manual_brightness_control",
    )
    assert set(transition["target_entity_ids"]) == {LEAF_A, LEAF_B}
    assert (
        coordinator._classify_interceptor_command(ROOT, context, "on")
        == CommandOrigin.SIBLING
    )
    assert manager.is_manual_authority_context(LEAF_A, context)
    assert manager.is_manual_authority_context(LEAF_B, context)
    assert (
        coordinator._classify_command_context(
            ROOT,
            context,
            include_parent=True,
            expected_target_state="on",
        )
        == CommandOrigin.SIBLING
    )
    coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_kind", "expected_source"),
    [
        ("user", SOURCE_UNKNOWN),
        ("homekit", SOURCE_HOMEKIT_SINGLE),
    ],
)
@pytest.mark.parametrize("target", [ROOT, LEAF_A], ids=["root", "leased-leaf"])
async def test_manual_brightness_service_path_applies_external_policy_once(
    mock_hass,
    mock_config_entry,
    monkeypatch,
    source_kind,
    expected_source,
    target,
):
    _configure_root(mock_hass)
    entry = _enable_entry(mock_config_entry)
    if source_kind == "homekit":
        entry.data[CONF_HOMEKIT_BATCH_MODE] = BATCH_MODE_OBSERVE
    coordinator = await _coordinator(mock_hass, entry)
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)
    result_type = _patch_intercept_result(monkeypatch)
    context = (
        MockContext("user-brightness", user_id="local-user")
        if source_kind == "user"
        else MockContext("homekit-brightness")
    )
    if source_kind == "homekit":
        coordinator._batch_observer.note_command(
            target,
            "turn_on",
            context.id,
        )
    data = {
        "entity_id": [target],
        "params": {"brightness_pct": 22},
    }
    call = _interceptor_service_call(mock_hass, context, data)

    service_event = _manual_brightness_service_event(context, target)
    await coordinator._handle_service_call(service_event)
    override = coordinator._override_manager.get(ROOT)

    assert manager.get(ROOT) is None
    _assert_revocation_generation(
        mock_hass,
        acquired["generation"],
        "manual_brightness_control",
    )
    assert override is not None
    assert override.policy == EXTERNAL_POLICY_PAUSE
    assert override.source == expected_source
    assert coordinator.get_automation_paused(ROOT) is True

    assert (
        await ControlLeaseInterceptor(mock_hass, manager)._handle_turn_on(call, data)
        == result_type.ALLOW
    )
    assert coordinator._override_manager.get(ROOT) is override
    assert coordinator.get_automation_paused(ROOT) is True
    assert (
        coordinator._classify_command_context(
            ROOT,
            context,
            include_parent=True,
            expected_target_state="on",
        )
        == CommandOrigin.SIBLING
    )
    intent_events = mock_hass.bus.fired_events(EVENT_COMMAND_INTENT)
    assert len(intent_events) == 1

    await coordinator._handle_service_call(service_event)
    assert coordinator._override_manager.get(ROOT) is override
    assert len(mock_hass.bus.fired_events(EVENT_COMMAND_INTENT)) == 1

    mock_hass.states.set(
        ROOT,
        "on",
        context=context,
        attributes={"group_entities": [LEAF_A, LEAF_B]},
    )
    await coordinator._handle_controlled_entity_change(
        _controlled_state_event(context, "off", "on")
    )
    assert coordinator._override_manager.get(ROOT) is override
    assert coordinator.get_automation_paused(ROOT) is True

    mock_hass.services.clear()
    await coordinator._reconcile_entity(ROOT, coordinator._entity_states[ROOT])
    assert mock_hass.services.calls == []
    assert coordinator.get_automation_paused(ROOT) is True
    coordinator.async_stop()


def test_target_expansion_imports_ha_2026_8_service_helper():
    from homeassistant.helpers import service as service_helpers

    assert callable(service_helpers.async_extract_entity_ids)
    assert (
        inspect.iscoroutinefunction(service_helpers.async_extract_entity_ids) is False
    )
    assert not hasattr(service_helpers, "async_extract_referenced_entity_ids")


@pytest.mark.asyncio
async def test_interceptor_expands_the_original_service_call(
    mock_hass,
    mock_config_entry,
    monkeypatch,
):
    _configure_root(mock_hass)
    _map_service_target(mock_hass, "area_id", "original-call-area", [LEAF_A])
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    await _acquire(manager)
    result_type = _patch_intercept_result(monkeypatch)
    context = MockContext("original-intercepted-call", user_id="local-user")
    interceptor_data = {
        "area_id": "original-call-area",
        "brightness_pct": 26,
    }
    intercepted_call = _interceptor_service_call(
        mock_hass,
        context,
        interceptor_data,
    )

    result = await ControlLeaseInterceptor(mock_hass, manager)._handle_turn_on(
        intercepted_call,
        interceptor_data,
    )

    assert result == result_type.ALLOW
    assert mock_hass._target_expansion_calls[-1] is intercepted_call
    assert manager.get(ROOT) is None
    coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector",
    ["area_id", "device_id", "label_id", "floor_id"],
)
async def test_canonical_non_entity_targets_revoke_before_interceptor(
    mock_hass,
    mock_config_entry,
    monkeypatch,
    selector,
):
    _configure_root(mock_hass)
    selector_id = f"{selector}-living-room"
    _map_service_target(mock_hass, selector, selector_id, [LEAF_A])
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)
    result_type = _patch_intercept_result(monkeypatch)
    context = MockContext(f"manual-{selector}", user_id="local-user")
    target_data = {selector: selector_id}
    authority_data = {"brightness_pct": 24}
    service_event = _turn_on_service_event(
        context,
        target_data,
        authority_data,
    )

    await coordinator._handle_service_call(service_event)
    override = coordinator._override_manager.get(ROOT)
    event_service_call = mock_hass._target_expansion_calls[0]

    assert manager.get(ROOT) is None
    assert isinstance(event_service_call, ServiceCall)
    assert event_service_call.hass is mock_hass
    assert event_service_call.context is context
    assert event_service_call.data[selector] == selector_id
    _assert_revocation_generation(
        mock_hass,
        acquired["generation"],
        "manual_brightness_control",
    )
    assert override is not None
    assert override.policy == EXTERNAL_POLICY_PAUSE
    assert coordinator.get_automation_paused(ROOT) is True
    interceptor_data = _interceptor_turn_on_data(target_data, authority_data)
    intercepted_call = _interceptor_service_call(
        mock_hass,
        context,
        interceptor_data,
    )
    assert (
        await ControlLeaseInterceptor(mock_hass, manager)._handle_turn_on(
            intercepted_call,
            interceptor_data,
        )
        == result_type.ALLOW
    )
    assert coordinator._override_manager.get(ROOT) is override
    assert len(mock_hass.bus.fired_events(EVENT_COMMAND_INTENT)) == 1

    mock_hass.states.set(
        ROOT,
        "on",
        context=context,
        attributes={"group_entities": [LEAF_A, LEAF_B]},
    )
    await coordinator._handle_controlled_entity_change(
        _controlled_state_event(context, "off", "on")
    )
    mock_hass.services.clear()
    await coordinator._reconcile_entity(ROOT, coordinator._entity_states[ROOT])
    assert mock_hass.services.calls == []
    assert coordinator._override_manager.get(ROOT) is override
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_mixed_target_selectors_revoke_each_room_once(
    mock_hass,
    mock_config_entry,
    monkeypatch,
):
    _configure_root(mock_hass)
    mock_hass.states.set(
        ROOT_2,
        "off",
        attributes={"group_entities": [LEAF_C, LEAF_D]},
    )
    mock_hass.states.set(LEAF_C, "off")
    mock_hass.states.set(LEAF_D, "off")

    first_entry = _enable_entry(mock_config_entry)
    second_entry = MagicMock()
    second_entry.domain = DOMAIN
    second_entry.entry_id = "kitchen-entry"
    second_entry.unique_id = "Kitchen"
    second_entry.version = 13
    second_config = dict(first_entry.data[CONF_CONTROLLED_ENTITIES][0])
    second_config["entity_id"] = ROOT_2
    second_entry.data = {
        **first_entry.data,
        CONF_CONTROLLED_ENTITIES: [second_config],
    }
    second_entry.async_on_unload = MagicMock()
    second_entry.add_update_listener = MagicMock()

    first = await _coordinator(mock_hass, first_entry)
    second = await _coordinator(mock_hass, second_entry)
    manager = first._control_lease_manager
    first_acquired = await _acquire(manager)
    second_acquired = await _acquire(
        manager,
        root_entity_id=ROOT_2,
        lease_id="lease-2",
        controller_id="wake-kitchen",
        request_id="request-2",
        target_entity_ids=[LEAF_C, LEAF_D],
    )
    _map_service_target(mock_hass, "area_id", "kitchen-area", [LEAF_C])
    _map_service_target(mock_hass, "device_id", "living-device", [LEAF_B])
    _map_service_target(mock_hass, "label_id", "kitchen-label", [LEAF_D])
    _map_service_target(
        mock_hass,
        "floor_id",
        "shared-floor",
        [LEAF_A, LEAF_C],
    )
    context = MockContext("mixed-brightness-targets", user_id="local-user")
    target_data = {
        "entity_id": [LEAF_A],
        "area_id": ["kitchen-area"],
        "device_id": "living-device",
        "label_id": "kitchen-label",
        "floor_id": "shared-floor",
    }
    authority_data = {"profile": "relax"}
    service_event = _turn_on_service_event(
        context,
        target_data,
        authority_data,
    )

    await first._handle_service_call(service_event)
    await second._handle_service_call(service_event)
    first_override = first._override_manager.get(ROOT)
    second_override = second._override_manager.get(ROOT_2)

    assert manager.get(ROOT) is None
    assert manager.get(ROOT_2) is None
    revoked = [
        event["data"]
        for event in mock_hass.bus.fired_events(EVENT_CONTROL_LEASE_REVOKED)
        if event["data"]["cause"] == "manual_brightness_control"
    ]
    assert {event["root_entity_id"] for event in revoked} == {ROOT, ROOT_2}
    assert len(revoked) == 2
    revoked_by_root = {event["root_entity_id"]: event for event in revoked}
    for root_entity_id, acquired in (
        (ROOT, first_acquired),
        (ROOT_2, second_acquired),
    ):
        assert (
            revoked_by_root[root_entity_id]["previous_generation"]
            == acquired["generation"]
        )
        assert (
            revoked_by_root[root_entity_id]["generation"] == acquired["generation"] + 1
        )
    assert first_override is not None
    assert second_override is not None
    assert first.get_automation_paused(ROOT) is True
    assert second.get_automation_paused(ROOT_2) is True

    result_type = _patch_intercept_result(monkeypatch)
    interceptor_data = _interceptor_turn_on_data(target_data, authority_data)
    assert (
        await ControlLeaseInterceptor(mock_hass, manager)._handle_turn_on(
            _interceptor_service_call(mock_hass, context, interceptor_data),
            interceptor_data,
        )
        == result_type.ALLOW
    )
    assert len(mock_hass.bus.fired_events(EVENT_COMMAND_INTENT)) == 2

    await first._handle_service_call(service_event)
    await second._handle_service_call(service_event)
    assert first._override_manager.get(ROOT) is first_override
    assert second._override_manager.get(ROOT_2) is second_override
    assert len(mock_hass.bus.fired_events(EVENT_COMMAND_INTENT)) == 2
    first.async_stop()
    second.async_stop()


@pytest.mark.asyncio
async def test_target_expansion_failure_blocks_without_unsafe_revoke(
    mock_hass,
    mock_config_entry,
    monkeypatch,
    caplog,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)
    result_type = _patch_intercept_result(monkeypatch)
    context = MockContext("failed-target-expansion", user_id="local-user")
    target_data = {"area_id": "broken-area"}
    mock_hass._target_expansion_error = RuntimeError("target registry unavailable")
    interceptor = ControlLeaseInterceptor(mock_hass, manager)

    plain_event = _turn_on_service_event(context, target_data, {})
    await coordinator._handle_service_call(plain_event)
    plain_data = _interceptor_turn_on_data(target_data, {})
    plain_result = await interceptor._handle_turn_on(
        _interceptor_service_call(mock_hass, context, plain_data),
        plain_data,
    )

    assert plain_result == result_type.ALLOW
    assert manager.get(ROOT) is not None
    assert manager.get(ROOT).generation == acquired["generation"]

    caplog.set_level("ERROR")
    authority_data = {"brightness_pct": 30}
    authority_event = _turn_on_service_event(
        context,
        target_data,
        authority_data,
    )
    await coordinator._handle_service_call(authority_event)
    authority_interceptor_data = _interceptor_turn_on_data(
        target_data,
        authority_data,
    )
    blocked_result = await interceptor._handle_turn_on(
        _interceptor_service_call(
            mock_hass,
            context,
            authority_interceptor_data,
        ),
        authority_interceptor_data,
    )

    assert blocked_result == result_type.BLOCK
    assert manager.get(ROOT) is not None
    assert manager.get(ROOT).generation == acquired["generation"]
    assert mock_hass.bus.fired_events(EVENT_CONTROL_LEASE_REVOKED) == []
    assert coordinator._override_manager.get(ROOT) is None
    assert coordinator.get_automation_paused(ROOT) is False
    assert any(
        "Error handling service call event" in message for message in caplog.messages
    )
    assert any(
        "Failed to resolve explicit-brightness light targets" in message
        for message in caplog.messages
    )
    await _release(manager)
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_foreign_brightness_revoke_preserves_admitted_baseline(
    mock_hass,
    mock_config_entry,
    monkeypatch,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    admitted_override = _set_external_pause(coordinator)
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)
    result_type = _patch_intercept_result(monkeypatch)
    interceptor = ControlLeaseInterceptor(mock_hass, manager)
    context = MockContext("manual-baseline-brightness", user_id="local-user")
    _map_service_target(mock_hass, "floor_id", "baseline-floor", [LEAF_A])
    target_data = {"floor_id": "baseline-floor"}
    authority_data = {"brightness_pct": 12}

    service_event = _turn_on_service_event(
        context,
        target_data,
        authority_data,
    )
    await coordinator._handle_service_call(service_event)
    interceptor_data = _interceptor_turn_on_data(target_data, authority_data)
    result = await interceptor._handle_turn_on(
        _interceptor_service_call(mock_hass, context, interceptor_data),
        interceptor_data,
    )
    await coordinator._handle_service_call(service_event)

    assert result == result_type.ALLOW
    assert manager.get(ROOT) is None
    assert coordinator._override_manager.get(ROOT) is admitted_override
    assert coordinator.get_automation_paused(ROOT) is True
    assert mock_hass.bus.fired_events(EVENT_COMMAND_INTENT) == []
    assert (
        coordinator._classify_command_context(
            ROOT,
            context,
            include_parent=True,
            expected_target_state="on",
        )
        == CommandOrigin.SIBLING
    )
    transition = _assert_revocation_generation(
        mock_hass,
        acquired["generation"],
        "manual_brightness_control",
    )
    assert transition["baseline_admitted"] is True
    assert transition["baseline_outcome"] == "baseline_preserved_revoke"

    mock_hass.states.set(
        ROOT,
        "on",
        context=context,
        attributes={"group_entities": [LEAF_A, LEAF_B]},
    )
    await coordinator._handle_controlled_entity_change(
        _controlled_state_event(context, "off", "on")
    )
    assert coordinator._override_manager.get(ROOT) is admitted_override
    assert coordinator.get_automation_paused(ROOT) is True

    mock_hass.services.clear()
    await coordinator._reconcile_entity(ROOT, coordinator._entity_states[ROOT])
    assert mock_hass.services.calls == []
    assert coordinator._override_manager.get(ROOT) is admitted_override
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_homekit_brightness_revoke_bypasses_downstream_presence_lock(
    mock_hass,
    mock_config_entry,
    monkeypatch,
):
    import custom_components.presence_based_lighting.interceptor as interceptor_module

    _configure_root(mock_hass)
    entry = _enable_entry(mock_config_entry)
    entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_REQUIRE_OCCUPANCY_FOR_DETECTED] = True
    coordinator = await _coordinator(mock_hass, entry)
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)
    result_type = _patch_intercept_result(monkeypatch)
    registrations = []

    def register(_hass, **kwargs):
        registrations.append(kwargs)
        return lambda: None

    monkeypatch.setattr(interceptor_module, "HAS_INTERCEPTOR", True)
    monkeypatch.setattr(
        interceptor_module,
        "register_interceptor",
        register,
        raising=False,
    )
    presence_lock = PresenceLockInterceptor(
        mock_hass,
        entry,
        lambda: False,
        coordinator._entity_may_enforce_presence_lock,
        entry_is_active_func=coordinator._entry_is_active,
        classify_command_context_func=coordinator._classify_interceptor_command,
    )
    assert presence_lock.setup()
    handler = next(
        registration["handler"]
        for registration in registrations
        if registration["integration"] == DOMAIN
        and registration["service"] == "turn_on"
    )
    context = MockContext("homekit-brightness", parent_id="homekit-bridge")
    data = {
        "entity_id": [ROOT],
        "params": {"brightness_pct": 22},
    }
    call = _interceptor_service_call(mock_hass, context, data)

    await coordinator._handle_service_call(
        _manual_brightness_service_event(context, ROOT)
    )
    lease_result = await ControlLeaseInterceptor(
        mock_hass,
        manager,
    )._handle_turn_on(call, data)
    presence_result = await handler(call, data)

    assert lease_result == result_type.ALLOW
    assert presence_result == result_type.ALLOW
    assert data["entity_id"] == [ROOT]
    assert manager.get(ROOT) is None
    assert coordinator.get_automation_paused(ROOT) is True
    assert coordinator._override_manager.get(ROOT) is not None
    _assert_revocation_generation(
        mock_hass,
        acquired["generation"],
        "manual_brightness_control",
    )
    presence_lock.teardown()
    coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "service_data",
    [
        {},
        {"transition": 1},
        {"rgb_color": [255, 120, 40]},
        {"color_temp_kelvin": 2700},
    ],
    ids=["plain", "transition-only", "rgb-only", "temperature-only"],
)
async def test_plain_and_color_only_turn_on_coexist_with_active_lease(
    mock_hass,
    mock_config_entry,
    monkeypatch,
    service_data,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)
    result_type = _patch_intercept_result(monkeypatch)
    interceptor = ControlLeaseInterceptor(mock_hass, manager)
    context = MockContext("non-brightness-on", user_id="local-user")
    _map_service_target(mock_hass, "area_id", "living-area", [LEAF_A])
    target_data = {"area_id": "living-area"}

    await coordinator._handle_service_call(
        _turn_on_service_event(
            context,
            target_data,
            service_data,
        )
    )
    interceptor_data = _interceptor_turn_on_data(target_data, service_data)
    result = await interceptor._handle_turn_on(
        _interceptor_service_call(mock_hass, context, interceptor_data),
        interceptor_data,
    )

    assert result == result_type.ALLOW
    assert manager.get(ROOT) is not None
    assert manager.get(ROOT).generation == acquired["generation"]
    assert mock_hass.bus.fired_events(EVENT_CONTROL_LEASE_REVOKED) == []
    assert coordinator._override_manager.get(ROOT) is None
    await _release(manager)
    coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [ROOT, LEAF_A], ids=["root", "leased-leaf"])
async def test_plain_turn_on_is_not_normalized_while_lease_owns_brightness(
    mock_hass,
    mock_config_entry,
    monkeypatch,
    target,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    await _acquire(manager)
    result_type = _patch_intercept_result(monkeypatch)
    normalizer = LightTurnOnNormalizer(mock_hass)
    normalizer._policies[target] = {
        "entry": LightNormalizationPolicy(
            entry_id="entry",
            entity_id=target,
            brightness_pct=55,
            transition=2,
            is_active=lambda: True,
        )
    }
    call = type("Call", (), {"context": MockContext("plain-on")})()
    leased_data = {"entity_id": [target], "params": {}}

    assert await normalizer._normalize(call, leased_data) == result_type.ALLOW
    assert leased_data["params"] == {}
    assert manager.get(ROOT) is not None

    await _release(manager)
    unleased_data = {"entity_id": [target], "params": {}}
    assert await normalizer._normalize(call, unleased_data) == result_type.ALLOW
    assert unleased_data["params"]["brightness"] == round(255 * 0.55)
    assert unleased_data["params"]["transition"] == 2
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_owner_and_pbl_brightness_contexts_do_not_revoke(
    mock_hass,
    mock_config_entry,
    monkeypatch,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)
    result_type = _patch_intercept_result(monkeypatch)
    interceptor = ControlLeaseInterceptor(mock_hass, manager)

    pbl_context = MockContext("pbl-owned-brightness")
    coordinator._register_command_context(ROOT, pbl_context, "on")
    _map_service_target(mock_hass, "area_id", "pbl-area", [LEAF_A])
    pbl_target_data = {"area_id": "pbl-area"}
    pbl_authority_data = {"brightness_pct": 60}
    pbl_data = _interceptor_turn_on_data(
        pbl_target_data,
        pbl_authority_data,
    )
    await coordinator._handle_service_call(
        _turn_on_service_event(
            pbl_context,
            pbl_target_data,
            pbl_authority_data,
        )
    )
    assert (
        await interceptor._handle_turn_on(
            _interceptor_service_call(mock_hass, pbl_context, pbl_data),
            pbl_data,
        )
        == result_type.ALLOW
    )
    assert manager.get(ROOT) is not None

    dispatched = await manager.async_call_with_control_lease(
        root_entity_id=ROOT,
        lease_id="lease-1",
        controller_id="wake-master-bedroom",
        expected_generation=acquired["generation"],
        command_id="owner-context",
        target_entity_ids=[LEAF_A, LEAF_B],
        service_data={"brightness_pct": 30, "transition": 1},
    )
    assert dispatched["outcome"] == "dispatched"
    owner_call = mock_hass.services.calls[-1]
    owner_authority_data = {
        key: value
        for key, value in owner_call["service_data"].items()
        if key != "entity_id"
    }
    owner_entity_data = _interceptor_turn_on_data(
        {"entity_id": owner_call["service_data"]["entity_id"]},
        owner_authority_data,
    )
    mock_hass._target_expansion_error = RuntimeError("target registry unavailable")
    await coordinator._handle_service_call(
        _turn_on_service_event(
            owner_call["context"],
            {"entity_id": owner_call["service_data"]["entity_id"]},
            owner_authority_data,
        )
    )
    assert (
        await interceptor._handle_turn_on(
            _interceptor_service_call(
                mock_hass,
                owner_call["context"],
                owner_entity_data,
            ),
            owner_entity_data,
        )
        == result_type.ALLOW
    )
    mock_hass._target_expansion_error = None
    assert owner_entity_data["entity_id"] == [LEAF_A, LEAF_B]
    assert manager.get(ROOT) is not None

    _map_service_target(mock_hass, "device_id", "wake-device", [LEAF_A])
    owner_target_data = {"device_id": "wake-device"}
    owner_data = _interceptor_turn_on_data(
        owner_target_data,
        owner_authority_data,
    )
    await coordinator._handle_service_call(
        _turn_on_service_event(
            owner_call["context"],
            owner_target_data,
            owner_authority_data,
        )
    )
    assert (
        await interceptor._handle_turn_on(
            _interceptor_service_call(
                mock_hass,
                owner_call["context"],
                owner_data,
            ),
            owner_data,
        )
        == result_type.ALLOW
    )
    assert owner_data["entity_id"] == [LEAF_A]
    assert "device_id" not in owner_data
    assert manager.get(ROOT) is not None
    assert mock_hass.bus.fired_events(EVENT_CONTROL_LEASE_REVOKED) == []
    assert coordinator._override_manager.get(ROOT) is None
    await _release(manager)
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_intercepted_turn_off_still_revokes_before_allow(
    mock_hass,
    mock_config_entry,
    monkeypatch,
):
    _configure_root(mock_hass, state="on")
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)
    result_type = _patch_intercept_result(monkeypatch)
    interceptor = ControlLeaseInterceptor(mock_hass, manager)

    result = await interceptor._handle_turn_off(
        type(
            "Call",
            (),
            {"context": MockContext("intercepted-off", user_id="local-user")},
        )(),
        {"entity_id": [ROOT]},
    )

    assert result == result_type.ALLOW
    assert manager.get(ROOT) is None
    _assert_revocation_generation(
        mock_hass,
        acquired["generation"],
        "manual_group_off",
    )
    coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("target_state", ["on", "off"])
async def test_acquire_blocks_dispatched_actuation_until_retry_safe_settlement(
    mock_hass,
    mock_config_entry,
    target_state,
):
    initial_state = "off" if target_state == "on" else "on"
    occupancy_state = "on" if target_state == "on" else "off"
    _configure_root(mock_hass, state=initial_state)
    mock_hass.states.set("binary_sensor.living_room_motion", occupancy_state)
    entry = _enable_entry(mock_config_entry)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    manager = coordinator._control_lease_manager
    manager.set_enforcement_available(True)
    entity_state = coordinator._entity_states[ROOT]
    service_key = (
        CONF_PRESENCE_DETECTED_SERVICE
        if target_state == "on"
        else CONF_PRESENCE_CLEARED_SERVICE
    )
    reason = IntentReason.PRESENCE if target_state == "on" else IntentReason.CLEARING
    entity_state["state"] = (
        EntityAutomationState.IDLE
        if target_state == "on"
        else EntityAutomationState.OCCUPIED
    )
    started = asyncio.Event()
    finish = asyncio.Event()

    async def delayed_call(
        domain,
        service,
        service_data=None,
        blocking=False,
        context=None,
    ):
        started.set()
        await finish.wait()

    mock_hass.services.async_call = delayed_call
    actuation_task = asyncio.create_task(
        coordinator._apply_service_intent(
            ROOT,
            entity_state,
            service_key,
            reason,
        )
    )
    await started.wait()
    actuation = entity_state["actuation"]
    dispatch_token = actuation["dispatching"]
    assert dispatch_token

    blocked = await _acquire(manager)

    assert blocked["outcome"] == "denied"
    assert "actuation_in_flight" in blocked["blockers"]
    assert manager.get(ROOT) is None
    assert actuation["status"] == ActuationStatus.PENDING
    assert actuation["target_state"] == target_state
    assert actuation["dispatching"] is dispatch_token
    assert actuation_task.done() is False

    finish.set()
    assert await actuation_task is True
    pending_timer = actuation["timer"]
    assert pending_timer is not None
    assert actuation["status"] == ActuationStatus.PENDING
    pending = await _acquire(manager, request_id="retry-while-pending")
    assert pending["outcome"] == "denied"
    assert "actuation_in_flight" in pending["blockers"]
    assert actuation["timer"] is pending_timer
    assert pending_timer.cancelled() is False

    coordinator._confirm_entity_actuation(ROOT, entity_state, target_state)
    acquired = await _acquire(manager, request_id="retry-after-actuation")

    assert acquired["outcome"] == "acquired"
    assert "actuation_in_flight" not in acquired["blockers"]
    await _release(manager)
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_multi_entry_fanout_suppresses_every_shared_coordinator(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    first_entry = _enable_entry(mock_config_entry)
    second_entry = MagicMock()
    second_entry.domain = DOMAIN
    second_entry.entry_id = "second-entry"
    second_entry.unique_id = "Second"
    second_entry.version = 13
    second_entry.data = {
        **first_entry.data,
        CONF_CONTROLLED_ENTITIES: [dict(first_entry.data[CONF_CONTROLLED_ENTITIES][0])],
    }
    second_entry.async_on_unload = MagicMock()
    second_entry.add_update_listener = MagicMock()

    first = await _coordinator(mock_hass, first_entry)
    second = await _coordinator(mock_hass, second_entry)
    result = await _acquire(first._control_lease_manager)

    assert result["outcome"] == "acquired"
    assert first.get_entity_control_state(ROOT)["suppression_kind"] == "leased"
    assert second.get_entity_control_state(ROOT)["suppression_kind"] == "leased"

    await _release(first._control_lease_manager)
    assert first.get_entity_control_state(ROOT)["automation_suppressed"] is False
    assert second.get_entity_control_state(ROOT)["automation_suppressed"] is False
    first.async_stop()
    second.async_stop()


@pytest.mark.asyncio
async def test_initial_persistence_failure_returns_no_usable_lease(
    mock_hass,
):
    _configure_root(mock_hass)
    store = MockStore(mock_hass, 1, "lease-test")
    store.fail_save = True
    manager = ControlLeaseManager(mock_hass, store=store)
    manager.register_entity("entry", ROOT, mode=CONTROL_LEASE_MODE_ENFORCE)
    manager.set_enforcement_available(True)

    result = await _acquire(manager)

    assert result["outcome"] == "persistence_error"
    assert result["lease_id"] is None
    assert manager.get(ROOT) is None


@pytest.mark.asyncio
async def test_central_store_restores_recovering_and_reattaches_without_extension(
    mock_hass,
):
    _configure_root(mock_hass)
    clock = FakeClock()
    store = MockStore(mock_hass, 1, "lease-recovery")
    first = ControlLeaseManager(
        mock_hass,
        monotonic_source=lambda: clock.monotonic,
        utcnow_source=lambda: clock.utcnow,
        store=store,
    )
    first.register_entity("entry", ROOT, mode=CONTROL_LEASE_MODE_ENFORCE)
    first.set_enforcement_available(True)
    acquired = await _acquire(first, ttl_seconds=300)
    first_correlation = first.attributes_for(ROOT)["control_lease_id"]
    first._cancel_expiry(ROOT)

    clock.advance(30)
    restarted = ControlLeaseManager(
        mock_hass,
        monotonic_source=lambda: clock.monotonic,
        utcnow_source=lambda: clock.utcnow,
        store=store,
    )
    restarted.register_entity("entry", ROOT, mode=CONTROL_LEASE_MODE_ENFORCE)
    restarted.set_enforcement_available(True)
    await restarted.async_initialize()

    restored = restarted.get(ROOT)
    assert restored is not None and restored.status == "recovering"
    assert restarted.attributes_for(ROOT)["control_lease_id"] == first_correlation
    assert restarted.attributes_for(ROOT)["control_lease_watchdog_state"] == (
        "recovery_grace"
    )
    recovered = await _acquire(restarted, request_id="reattach", ttl_seconds=1000)
    assert recovered["outcome"] == "recovered"
    assert recovered["expires_at"] == acquired["expires_at"]

    await _release(restarted)


@pytest.mark.asyncio
async def test_unconfirmed_restart_recovery_is_bounded_to_120_seconds(
    mock_hass,
):
    _configure_root(mock_hass)
    clock = FakeClock()
    store = MockStore(mock_hass, 1, "lease-recovery-timeout")
    first = ControlLeaseManager(
        mock_hass,
        monotonic_source=lambda: clock.monotonic,
        utcnow_source=lambda: clock.utcnow,
        store=store,
    )
    first.register_entity("entry", ROOT, mode=CONTROL_LEASE_MODE_ENFORCE)
    first.set_enforcement_available(True)
    acquired = await _acquire(first, ttl_seconds=600)
    first._cancel_expiry(ROOT)

    restarted = ControlLeaseManager(
        mock_hass,
        monotonic_source=lambda: clock.monotonic,
        utcnow_source=lambda: clock.utcnow,
        store=store,
    )
    restarted.register_entity("entry", ROOT, mode=CONTROL_LEASE_MODE_ENFORCE)
    restarted.set_enforcement_available(True)
    await restarted.async_initialize()
    restored = restarted.get(ROOT)
    assert restored is not None
    assert (restored.recovery_deadline_dt - clock.utcnow).total_seconds() == 120

    clock.advance(121)
    assert await restarted.async_expire_due(ROOT) == 1
    assert restarted.get(ROOT) is None
    _assert_revocation_generation(
        mock_hass,
        acquired["generation"],
        "recovery_timeout",
    )


@pytest.mark.asyncio
async def test_expiry_breaks_lease_and_emits_revoked_event(mock_hass):
    _configure_root(mock_hass)
    clock = FakeClock()
    manager = ControlLeaseManager(
        mock_hass,
        monotonic_source=lambda: clock.monotonic,
        utcnow_source=lambda: clock.utcnow,
        store=MockStore(mock_hass, 1, "lease-expiry"),
    )
    manager.register_entity("entry", ROOT, mode=CONTROL_LEASE_MODE_ENFORCE)
    manager.set_enforcement_available(True)
    acquired = await _acquire(manager, ttl_seconds=10)

    clock.advance(11)
    assert await manager.async_expire_due(ROOT) == 1
    assert manager.get(ROOT) is None
    revoked = mock_hass.bus.fired_events(EVENT_CONTROL_LEASE_REVOKED)
    assert revoked[-1]["data"]["cause"] == "expiry"
    _assert_revocation_generation(
        mock_hass,
        acquired["generation"],
        "expiry",
    )


@pytest.mark.asyncio
async def test_bare_on_is_rejected_and_removed_targets_never_dispatch(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    await _acquire(manager, target_entity_ids=[LEAF_A])

    bare = await manager.async_call_with_control_lease(
        root_entity_id=ROOT,
        lease_id="lease-1",
        controller_id="wake-master-bedroom",
        expected_generation=manager.get(ROOT).generation,
        command_id="step-1",
        target_entity_ids=[LEAF_A, LEAF_B],
        service_data={"transition": 1},
    )
    assert bare["outcome"] == "blocked"
    assert mock_hass.services.calls == []

    invalid = await manager.async_call_with_control_lease(
        root_entity_id=ROOT,
        lease_id="lease-1",
        controller_id="wake-master-bedroom",
        expected_generation=manager.get(ROOT).generation,
        command_id="step-invalid-data",
        target_entity_ids=[LEAF_A],
        service_data={"brightness_pct": 25, "effect": "wake"},
    )
    assert invalid["outcome"] == "blocked"
    assert invalid["blockers"] == ["invalid_service_data"]

    sent = await manager.async_call_with_control_lease(
        root_entity_id=ROOT,
        lease_id="lease-1",
        controller_id="wake-master-bedroom",
        expected_generation=manager.get(ROOT).generation,
        command_id="step-2",
        target_entity_ids=[LEAF_A, LEAF_B],
        service_data={"brightness_pct": 25, "transition": 1},
    )
    assert sent["outcome"] == "dispatched"
    assert mock_hass.services.calls[-1]["service_data"]["entity_id"] == [LEAF_A]

    await _release(manager)
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_target_unavailability_breaks_before_dispatch_and_fails_dark(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)
    mock_hass.states.set(LEAF_B, "unavailable")

    result = await manager.async_call_with_control_lease(
        root_entity_id=ROOT,
        lease_id="lease-1",
        controller_id="wake-master-bedroom",
        expected_generation=manager.get(ROOT).generation,
        command_id="step-unavailable",
        target_entity_ids=[LEAF_A, LEAF_B],
        service_data={"brightness_pct": 25, "transition": 1},
    )

    assert result["outcome"] == "blocked"
    assert result["blockers"] == ["target_unavailable"]
    assert manager.get(ROOT) is None
    _assert_revocation_generation(
        mock_hass,
        acquired["generation"],
        "target_unavailable",
    )
    assert coordinator.get_automation_paused(ROOT) is True
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_membership_change_breaks_before_dispatch(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)
    mock_hass.states.set(
        ROOT,
        "off",
        attributes={"group_entities": [LEAF_A]},
    )

    result = await manager.async_call_with_control_lease(
        root_entity_id=ROOT,
        lease_id="lease-1",
        controller_id="wake-master-bedroom",
        expected_generation=acquired["generation"],
        command_id="step-membership-change",
        target_entity_ids=[LEAF_A, LEAF_B],
        service_data={"brightness_pct": 25, "transition": 1},
    )

    assert result["outcome"] == "blocked"
    assert result["blockers"] == ["target_membership_mismatch"]
    assert manager.get(ROOT) is None
    _assert_revocation_generation(
        mock_hass,
        acquired["generation"],
        "target_membership_mismatch",
    )
    assert coordinator.get_automation_paused(ROOT) is True
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_lost_context_enforcement_revokes_before_next_command(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    await _acquire(manager)
    manager.set_enforcement_available(False)

    result = await manager.async_call_with_control_lease(
        root_entity_id=ROOT,
        lease_id="lease-1",
        controller_id="wake-master-bedroom",
        expected_generation=manager.get(ROOT).generation,
        command_id="step-no-guard",
        target_entity_ids=[LEAF_A, LEAF_B],
        service_data={"brightness_pct": 25, "transition": 1},
    )

    assert result["outcome"] == "blocked"
    assert result["blockers"] == ["context_enforcement_unavailable"]
    assert manager.get(ROOT) is None
    assert coordinator.get_automation_paused(ROOT) is True
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_generation_guard_blocks_context_revoked_while_dispatch_in_flight(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    await _acquire(manager)
    started = asyncio.Event()
    finish = asyncio.Event()
    captured = {}

    async def delayed_call(
        domain, service, service_data=None, blocking=False, context=None
    ):
        captured.update(
            {
                "domain": domain,
                "service": service,
                "service_data": service_data,
                "context": context,
            }
        )
        started.set()
        await finish.wait()

    mock_hass.services.async_call = delayed_call
    dispatch = asyncio.create_task(
        manager.async_call_with_control_lease(
            root_entity_id=ROOT,
            lease_id="lease-1",
            controller_id="wake-master-bedroom",
            expected_generation=manager.get(ROOT).generation,
            command_id="step-race",
            target_entity_ids=[LEAF_A, LEAF_B],
            service_data={"brightness_pct": 40, "transition": 1},
        )
    )
    await started.wait()
    await manager.async_break(
        ROOT,
        cause="manual_group_off",
        direction="off",
        context_classification="user",
    )

    decision = manager.guard_control_lease_command(
        captured["context"],
        service="turn_on",
        target_entity_ids=captured["service_data"]["entity_id"],
        service_data=captured["service_data"],
    )
    assert decision is not None
    assert decision.allowed is False
    assert decision.reason == "stale_control_lease_context"

    finish.set()
    assert (await dispatch)["outcome"] == "revoked_in_flight"
    manager.note_controlled_state(ROOT, "on", captured["context"])
    late_events = [
        event
        for event in mock_hass.bus.fired_events(EVENT_CONTROL_TRANSITION)
        if event["data"]["action"] == "late_owner_echo"
    ]
    assert len(late_events) == 1
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_corrective_off_requires_all_gates_and_runs_once(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(
        mock_hass,
        _enable_entry(mock_config_entry, correct_late_on=True),
    )
    manager = coordinator._control_lease_manager
    await _acquire(manager)
    await manager.async_call_with_control_lease(
        root_entity_id=ROOT,
        lease_id="lease-1",
        controller_id="wake-master-bedroom",
        expected_generation=manager.get(ROOT).generation,
        command_id="step-correct",
        target_entity_ids=[LEAF_A, LEAF_B],
        service_data={"brightness_pct": 40, "transition": 1},
    )
    owner_context = mock_hass.services.calls[-1]["context"]
    await manager.async_break(
        ROOT,
        cause="manual_group_off",
        direction="off",
        context_classification="user",
    )
    mock_hass.states.set(LEAF_A, "on")
    mock_hass.states.set(LEAF_B, "on")
    mock_hass.services.clear()

    manager.note_controlled_state(ROOT, "on", owner_context)
    manager.note_controlled_state(ROOT, "on", owner_context)
    await asyncio.sleep(0)

    correction_calls = [
        call
        for call in mock_hass.services.calls
        if call["domain"] == "light" and call["service"] == "turn_off"
    ]
    assert len(correction_calls) == 1
    assert set(correction_calls[0]["service_data"]["entity_id"]) == {LEAF_A, LEAF_B}
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_external_blocker_and_target_membership_fail_closed(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    blocker = "switch.adaptive_lighting_room"
    mock_hass.states.set(blocker, "on")
    coordinator = await _coordinator(
        mock_hass,
        _enable_entry(mock_config_entry, blockers=[blocker]),
    )
    manager = coordinator._control_lease_manager

    blocked = await _acquire(manager)
    assert f"external_blocker:{blocker}" in blocked["blockers"]

    mock_hass.states.set(blocker, "off")
    invalid_member = await _acquire(
        manager,
        target_entity_ids=["light.not_in_group"],
    )
    assert "target_membership_mismatch" in invalid_member["blockers"]
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_external_blocker_activating_mid_lease_breaks_next_dispatch(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    blocker = "switch.adaptive_lighting_room"
    mock_hass.states.set(blocker, "off")
    coordinator = await _coordinator(
        mock_hass,
        _enable_entry(mock_config_entry, blockers=[blocker]),
    )
    manager = coordinator._control_lease_manager
    acquired = await _acquire(manager)
    mock_hass.states.set(blocker, "on")

    result = await manager.async_call_with_control_lease(
        root_entity_id=ROOT,
        lease_id="lease-1",
        controller_id="wake-master-bedroom",
        expected_generation=acquired["generation"],
        command_id="step-blocked-controller",
        target_entity_ids=[LEAF_A, LEAF_B],
        service_data={"brightness_pct": 25, "transition": 1},
    )

    assert result["outcome"] == "blocked"
    assert result["blockers"] == ["external_blocker_active"]
    assert manager.get(ROOT) is None
    _assert_revocation_generation(
        mock_hass,
        acquired["generation"],
        "external_blocker_active",
    )
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_transition_payload_is_bounded_and_redacted(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    await _acquire(manager)
    await manager.async_break(
        ROOT,
        cause="manual_group_off",
        context_classification="user",
    )

    event = mock_hass.bus.fired_events(EVENT_CONTROL_TRANSITION)[-1]["data"]
    assert event["schema_version"] == 1
    assert event["lease_ref"] != "lease-1"
    assert len(event["lease_ref"]) == 12
    assert event["occurrence_refs"] != ["occurrence-1"]
    assert "user_id" not in event
    assert "alarm_label" not in event
    assert "wake_time" not in event
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_caller_ids_are_hashed_on_every_public_surface(
    mock_hass,
    mock_config_entry,
    caplog,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = coordinator._control_lease_manager
    raw_values = {
        "lease-Stephen-2030-06-10T0630",
        "person-stephen-bedroom",
        "wake-request-2030-06-10T0630",
        "stephen-alarm-0630",
    }

    caplog.set_level("DEBUG")
    acquired = await _acquire(
        manager,
        lease_id="lease-Stephen-2030-06-10T0630",
        controller_id="person-stephen-bedroom",
        request_id="wake-request-2030-06-10T0630",
        occurrence_ids=["stephen-alarm-0630"],
    )
    assert acquired["lease_id"] in raw_values
    attributes = coordinator.get_entity_control_state(ROOT)
    await manager.async_break(
        ROOT,
        cause="manual_group_off",
        direction="off",
        context_classification="user",
    )
    await asyncio.sleep(0)
    events = mock_hass.bus.fired_events(EVENT_CONTROL_TRANSITION)
    revoked_events = mock_hass.bus.fired_events(EVENT_CONTROL_LEASE_REVOKED)
    diagnostics = manager.diagnostics_for_entry(mock_config_entry.entry_id)
    terminal = mock_hass._storage["presence_based_lighting.control_leases"]["terminal"]
    public_text = repr(
        {
            "attributes": attributes,
            "events": events,
            "revoked_events": revoked_events,
            "terminal": terminal,
            "diagnostics": diagnostics,
            "logs": caplog.messages,
        }
    )

    for raw_value in raw_values:
        assert raw_value not in public_text
    assert len(attributes["control_lease_id"]) == 12
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_terminal_history_is_capped_at_twenty(mock_hass):
    _configure_root(mock_hass)
    manager = ControlLeaseManager(
        mock_hass,
        store=MockStore(mock_hass, 1, "lease-terminal-ring"),
    )
    manager.register_entity("entry", ROOT, mode=CONTROL_LEASE_MODE_ENFORCE)
    manager.set_enforcement_available(True)

    for index in range(22):
        await _acquire(
            manager,
            lease_id=f"lease-{index}",
            request_id=f"request-{index}",
        )
        await _release(
            manager,
            lease_id=f"lease-{index}",
            request_id=f"release-{index}",
        )

    diagnostics = manager.diagnostics_for_entry("entry")
    assert len(diagnostics["terminal"]) == 20
    assert diagnostics["terminal"][0]["lease_ref"] == manager._correlation_ref(
        "lease-2"
    )
    assert diagnostics["terminal"][-1]["lease_ref"] == manager._correlation_ref(
        "lease-21"
    )


@pytest.mark.asyncio
async def test_control_lease_services_accept_target_and_require_response_support(
    mock_hass,
):
    await async_setup(mock_hass, {})

    acquire = mock_hass.services._registered_metadata[(DOMAIN, "acquire_control")]
    dispatch = mock_hass.services._registered_metadata[(DOMAIN, "dispatch_control")]
    release = mock_hass.services._registered_metadata[(DOMAIN, "release_control")]
    assert acquire["supports_response"] == SupportsResponse.ONLY
    assert dispatch["supports_response"] == SupportsResponse.ONLY
    assert release["supports_response"] == SupportsResponse.ONLY

    target = {"entity_id": "switch.living_room_presence_allowed"}
    acquire["schema"](
        {
            **target,
            "controlled_entity_id": ROOT,
            "lease_id": "lease-1",
            "controller_id": "wake-living-room",
            "request_id": "acquire-1",
            "owner": "wake_light",
            "occurrence_ids": ["occurrence-1"],
            "ttl_seconds": 300,
            "target_entity_ids": [LEAF_A, LEAF_B],
        }
    )
    dispatch["schema"](
        {
            **target,
            "controlled_entity_id": ROOT,
            "lease_id": "lease-1",
            "controller_id": "wake-living-room",
            "expected_generation": 1,
            "command_id": "dispatch-1",
            "owner": "wake_light",
            "target_entity_ids": [LEAF_A, LEAF_B],
            "service_data": {"brightness_pct": 20, "transition": 1},
        }
    )
    release["schema"](
        {
            **target,
            "controlled_entity_id": ROOT,
            "lease_id": "lease-1",
            "controller_id": "wake-living-room",
            "expected_generation": 1,
            "request_id": "release-1",
            "owner": "wake_light",
            "outcome": "cancelled",
            "cause": "owner_shutdown",
        }
    )


def test_control_lease_interceptor_registers_both_required_handlers(
    mock_hass,
    monkeypatch,
):
    import custom_components.presence_based_lighting.interceptor as interceptor_module

    registrations = []

    class Result:
        ALLOW = "allow"
        BLOCK = "block"

    def register(_hass, **kwargs):
        registrations.append(kwargs)
        return lambda: None

    monkeypatch.setattr(interceptor_module, "HAS_INTERCEPTOR", True)
    monkeypatch.setattr(
        interceptor_module,
        "InterceptResult",
        Result,
        raising=False,
    )
    monkeypatch.setattr(
        interceptor_module,
        "register_interceptor",
        register,
        raising=False,
    )
    manager = get_control_lease_manager(mock_hass)

    assert manager.attach_enforcement_guard(ControlLeaseInterceptor(mock_hass, manager))
    assert {(item["domain"], item["service"]) for item in registrations} == {
        ("light", "turn_on"),
        ("light", "turn_off"),
    }


@pytest.mark.asyncio
async def test_acquire_release_services_return_manager_outcomes(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    await async_setup(mock_hass, {})
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    manager = get_control_lease_manager(mock_hass)
    manager.set_enforcement_available(True)
    switch_entity = "switch.living_room_presence_lighting"

    acquire_call = MagicMock()
    acquire_call.target = {"entity_id": switch_entity}
    acquire_call.data = {
        "controlled_entity_id": ROOT,
        "lease_id": "service-lease",
        "controller_id": "wake-master-bedroom",
        "request_id": "service-acquire",
        "owner": "wake_light",
        "occurrence_ids": ["occurrence-service"],
        "ttl_seconds": 300,
        "target_entity_ids": [LEAF_A, LEAF_B],
    }
    acquired = await mock_hass.services._registered[(DOMAIN, "acquire_control")](
        acquire_call
    )
    assert acquired["outcome"] == "acquired"
    assert acquired["lease_id"] == "service-lease"

    dispatch_call = MagicMock()
    dispatch_call.target = {"entity_id": switch_entity}
    dispatch_call.data = {
        "controlled_entity_id": ROOT,
        "lease_id": "service-lease",
        "controller_id": "wake-master-bedroom",
        "expected_generation": acquired["generation"],
        "command_id": "service-command",
        "owner": "wake_light",
        "target_entity_ids": [LEAF_A, LEAF_B],
        "service_data": {"brightness_pct": 25, "transition": 1},
    }
    dispatched = await mock_hass.services._registered[(DOMAIN, "dispatch_control")](
        dispatch_call
    )
    assert dispatched["outcome"] == "dispatched"
    assert mock_hass.services.calls[-1]["service"] == "turn_on"

    release_call = MagicMock()
    release_call.target = {"entity_id": switch_entity}
    release_call.data = {
        "controlled_entity_id": ROOT,
        "lease_id": "service-lease",
        "controller_id": "wake-master-bedroom",
        "expected_generation": acquired["generation"],
        "request_id": "service-release",
        "owner": "wake_light",
        "outcome": "completed",
        "cause": "hold_complete",
    }
    released = await mock_hass.services._registered[(DOMAIN, "release_control")](
        release_call
    )
    assert released["outcome"] == "released"
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_diagnostics_are_bounded_and_omit_raw_contexts(
    mock_hass,
    mock_config_entry,
):
    _configure_root(mock_hass)
    coordinator = await _coordinator(mock_hass, _enable_entry(mock_config_entry))
    await _acquire(coordinator._control_lease_manager)

    diagnostics = await async_get_config_entry_diagnostics(
        mock_hass,
        mock_config_entry,
    )

    payload = diagnostics["control_leases"]
    assert payload["schema_version"] == 1
    assert ROOT in payload["roots"]
    assert "context_id" not in repr(payload)
    assert "user_id" not in repr(payload)
    await _release(coordinator._control_lease_manager)
    coordinator.async_stop()
