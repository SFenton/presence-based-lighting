"""Regression tests for bulk ("all lights off") command handling.

These reproduce the 2026-08-01 incident: a native HomeKit "turn off all the
lights" delivered 16 individually-contexted ``turn_off`` commands inside
25.39 ms, and the Master Bathroom fallback profile treated its member command
as manual control and paused indefinitely.
"""

from __future__ import annotations

import datetime as _dt
import json
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.util import dt as dt_util

from custom_components.presence_based_lighting import (
    EntityAutomationState,
    PresenceBasedLightingCoordinator,
    async_migrate_entry,
)
from custom_components.presence_based_lighting.batch_observer import (
    CommandBatchObserver,
)
from custom_components.presence_based_lighting.command_context import (
    CommandOrigin,
    PresenceCommandContextRegistry,
)
from custom_components.presence_based_lighting.const import (
    BATCH_MODE_ENFORCE,
    BATCH_MODE_OBSERVE,
    BATCH_MODE_OFF,
    CONF_ACTIVATION_CONDITIONS,
    CONF_BATCH_MIN_DISTINCT_ENTITIES,
    CONF_BATCH_RETAIN_SECONDS,
    CONF_BATCH_WINDOW_MS,
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_HOMEKIT_BATCH_MODE,
    CONF_HONOR_EXTERNAL_OVERRIDE,
    CONF_INITIAL_PRESENCE_ALLOWED,
    CONF_MANUAL_DISABLE_STATES,
    CONF_OFF_DELAY,
    CONF_PRESENCE_CLEARED_SERVICE,
    CONF_PRESENCE_CLEARED_STATE,
    CONF_PRESENCE_DETECTED_SERVICE,
    CONF_PRESENCE_DETECTED_STATE,
    CONF_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE,
    CONF_PRESENCE_SENSORS,
    CONF_QUIETED_MAX_AGE,
    CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
    CONF_REQUIRE_VACANCY_FOR_CLEARED,
    CONF_RESPECTS_PRESENCE_ALLOWED,
    CONF_ROOM_NAME,
    CONF_UNKNOWN_SOURCE_POLICY,
    DEFAULT_BATCH_MIN_DISTINCT_ENTITIES,
    DEFAULT_HOMEKIT_BATCH_MODE,
    DEFAULT_HONOR_EXTERNAL_OVERRIDE,
    DEFAULT_QUIETED_MAX_AGE,
    DEFAULT_UNKNOWN_SOURCE_POLICY,
    DOMAIN,
    EVENT_COMMAND_INTENT,
    EVENT_HOMEKIT_STATE_CHANGE,
    EXTERNAL_POLICY_PAUSE,
    EXTERNAL_POLICY_REARM_AFTER_CLEAR,
)
from custom_components.presence_based_lighting.external_override import (
    ExternalOverrideManager,
)
from tests.conftest import MockContext

LIGHT = "light.master_bathroom_dimmer_switch"
LOCAL_SENSOR = "binary_sensor.master_bathroom_presence"
BEDROOM_ON = "input_boolean.is_master_bedroom_presence_sensor_on"
BEDROOM_OFF = "input_boolean.is_master_bedroom_presence_sensor_off"

# The 16 entities HomeKit turned off at 2026-08-01T22:48:17, in the observed
# order, spanning 25.39 ms end to end.
INCIDENT_ENTITIES = [
    "switch.upper_entryway_light_switch_top",
    "light.music_room",
    "light.downstairs_hallway_light",
    "light.office_light",
    "light.guest_room",
    "light.hallway_lights",
    "light.theater_room",
    "light.master_bedroom",
    "light.garage_camera_floodlight",
    "light.living_room",
    LIGHT,
    "light.kitchen",
    "light.guest_bathroom_dimmer_switch",
    "light.back_deck",
    "light.gym_light",
    "light.dining_room_dimmer_switch",
]
INCIDENT_OFFSETS_MS = [
    0.0, 11.855, 12.988, 13.263, 13.958, 14.301, 14.5, 14.773,
    22.686, 23.096, 23.408, 24.581, 24.741, 24.923, 25.2, 25.387,
]


class FakeClock:
    """Deterministic monotonic clock for window arithmetic."""

    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance_ms(self, milliseconds: float) -> None:
        self.value += milliseconds / 1000.0


def _entry(
    *,
    entry_id: str,
    room_name: str,
    activation_condition: str | None = None,
    presence_lock: bool = False,
    controlled_entity: str = LIGHT,
    presence_sensors: list[str] | None = None,
    honor_external_override: bool = DEFAULT_HONOR_EXTERNAL_OVERRIDE,
    batch_mode: str = BATCH_MODE_ENFORCE,
    batch_min_entities: int = 8,
    version: int = 11,
):
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.entry_id = entry_id
    entry.unique_id = room_name
    entry.version = version
    entry.data = {
        CONF_ROOM_NAME: room_name,
        CONF_PRESENCE_SENSORS: presence_sensors or [LOCAL_SENSOR],
        CONF_OFF_DELAY: 30,
        CONF_HOMEKIT_BATCH_MODE: batch_mode,
        CONF_BATCH_WINDOW_MS: 250,
        CONF_BATCH_RETAIN_SECONDS: 10.0,
        CONF_BATCH_MIN_DISTINCT_ENTITIES: batch_min_entities,
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: controlled_entity,
                CONF_PRESENCE_DETECTED_SERVICE: "turn_on",
                CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
                CONF_PRESENCE_DETECTED_STATE: STATE_ON,
                CONF_PRESENCE_CLEARED_STATE: STATE_OFF,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: presence_lock,
                CONF_REQUIRE_VACANCY_FOR_CLEARED: presence_lock,
                CONF_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE: False,
                CONF_MANUAL_DISABLE_STATES: [STATE_OFF],
                CONF_INITIAL_PRESENCE_ALLOWED: True,
                CONF_HONOR_EXTERNAL_OVERRIDE: honor_external_override,
                CONF_UNKNOWN_SOURCE_POLICY: DEFAULT_UNKNOWN_SOURCE_POLICY,
                CONF_QUIETED_MAX_AGE: DEFAULT_QUIETED_MAX_AGE,
            }
        ],
    }
    if activation_condition is not None:
        entry.data[CONF_ACTIVATION_CONDITIONS] = [activation_condition]
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock()
    return entry


def _state_event(entity_id, old_state, new_state, context, attributes=None):
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": type(
                    "State",
                    (),
                    {"state": old_state, "attributes": {}, "context": MockContext("old")},
                )(),
                "new_state": type(
                    "State",
                    (),
                    {
                        "state": new_state,
                        "attributes": attributes or {},
                        "context": context,
                    },
                )(),
            }
        },
    )()


def _sensor_event(entity_id, old_state, new_state):
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": type(
                    "State", (), {"state": old_state, "attributes": {}}
                )(),
                "new_state": type(
                    "State", (), {"state": new_state, "attributes": {}}
                )(),
            }
        },
    )()


def _homekit_event(entity_id, service, context):
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "display_name": entity_id,
                "service": service,
                "value": "Set state to False",
            },
            "context": context,
        },
    )()


def _install_clock(mock_hass, clock):
    """Point the shared batch observer at a deterministic clock."""
    observer = CommandBatchObserver(mock_hass, time_source=clock)
    mock_hass.data.setdefault(DOMAIN, {})["_command_batch_observer"] = observer
    manager = ExternalOverrideManager(time_source=clock)
    mock_hass.data[DOMAIN]["_external_override_manager"] = manager
    return observer, manager


def _configure_storage(mock_hass, tmp_path):
    """Give the coordinator a writable .storage path (pause persistence)."""
    storage = tmp_path.joinpath(".storage")
    storage.mkdir(exist_ok=True)
    mock_hass.config = MagicMock()
    mock_hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))

    async def run_sync(func, *args):
        return func(*args)

    mock_hass.async_add_executor_job = run_sync


async def _replay_incident_burst(mock_hass, observer, clock, entities=None):
    """Replay the observed HomeKit burst, returning context per entity."""
    entities = entities or INCIDENT_ENTITIES
    contexts = {}
    listeners = mock_hass.bus.listeners_for(EVENT_HOMEKIT_STATE_CHANGE)
    start = clock.value
    for index, entity_id in enumerate(entities):
        offset = INCIDENT_OFFSETS_MS[index] if index < len(INCIDENT_OFFSETS_MS) else index
        clock.value = start + offset / 1000.0
        context = MockContext(f"homekit-{index}")
        contexts[entity_id] = context
        event = _homekit_event(entity_id, "turn_off", context)
        for listener in listeners:
            await listener(event)
    return contexts


# ---------------------------------------------------------------------------
# 1 / 2: incident replay and singleton behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incident_replay_quiets_instead_of_pausing(mock_hass, tmp_path):
    """16 unique-context HomeKit turn_offs in 25.39 ms must quiet, not pause."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    entry = _entry(entry_id="fallback", room_name="Master Bathroom Fallback")
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    contexts = await _replay_incident_burst(mock_hass, observer, clock)

    decision = observer.classify(contexts[LIGHT].id)
    assert decision is not None
    assert decision.confirmed is True
    assert decision.size >= 8

    # The Zigbee2MQTT state echo landed 758 ms after the command.
    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=contexts[LIGHT])
    await coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, contexts[LIGHT])
    )

    assert coordinator.get_quieted(LIGHT) is True
    assert coordinator.get_automation_paused(LIGHT) is False
    assert coordinator._entity_states[LIGHT]["state"] == EntityAutomationState.QUIETED

    override = coordinator._override_manager.get(LIGHT)
    assert override is not None
    assert override.policy == EXTERNAL_POLICY_REARM_AFTER_CLEAR

    intents = mock_hass.bus.fired_events(EVENT_COMMAND_INTENT)
    assert any(event["data"]["entity_id"] == LIGHT for event in intents)

    coordinator.async_stop()


@pytest.mark.asyncio
async def test_single_homekit_off_still_pauses(mock_hass, tmp_path):
    """A one-accessory HomeKit off is genuine manual control and must pause."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    entry = _entry(entry_id="fallback", room_name="Master Bathroom Fallback")
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    context = MockContext("homekit-solo")
    for listener in mock_hass.bus.listeners_for(EVENT_HOMEKIT_STATE_CHANGE):
        await listener(_homekit_event(LIGHT, "turn_off", context))

    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=context)
    await coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, context)
    )

    assert coordinator.get_automation_paused(LIGHT) is True
    assert coordinator.get_quieted(LIGHT) is False
    override = coordinator._override_manager.get(LIGHT)
    assert override is not None and override.policy == EXTERNAL_POLICY_PAUSE

    coordinator.async_stop()


# ---------------------------------------------------------------------------
# 3 / 4: no bounce while occupied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quieted_entity_is_not_bounced_by_reconciliation(mock_hass, tmp_path):
    """Reconciliation must not relight a quieted entity in an occupied room."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    entry = _entry(entry_id="fallback", room_name="Master Bathroom Fallback")
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    contexts = await _replay_incident_burst(mock_hass, observer, clock)
    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=contexts[LIGHT])
    await coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, contexts[LIGHT])
    )
    assert coordinator.get_quieted(LIGHT) is True

    mock_hass.services.clear()
    # Room is still occupied and the activation gate is open.
    assert coordinator._is_any_occupied() is True
    await coordinator._reconcile_entity(LIGHT, coordinator._entity_states[LIGHT])
    await coordinator._periodic_reconciliation(None)

    assert [c for c in mock_hass.services.calls if c["service"] == "turn_on"] == []
    assert coordinator.get_quieted(LIGHT) is True

    coordinator.async_stop()


@pytest.mark.asyncio
async def test_presence_lock_cannot_force_quieted_entity_on(mock_hass, tmp_path):
    """Presence Lock must not revert a bulk off while the room is occupied."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    entry = _entry(
        entry_id="primary",
        room_name="Master Bathroom",
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    contexts = await _replay_incident_burst(mock_hass, observer, clock)
    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=contexts[LIGHT])
    await coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, contexts[LIGHT])
    )
    assert coordinator.get_quieted(LIGHT) is True

    mock_hass.services.clear()
    reverted = await coordinator._check_and_apply_presence_lock(
        coordinator._entity_states[LIGHT], STATE_OFF
    )
    assert reverted is False
    assert [c for c in mock_hass.services.calls if c["service"] == "turn_on"] == []
    assert coordinator._entity_may_enforce_presence_lock(LIGHT) is False

    coordinator.async_stop()


# ---------------------------------------------------------------------------
# 5: vacancy arms, rising presence releases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vacancy_arms_latch_and_rising_presence_releases(mock_hass, tmp_path):
    """Vacancy alone stays dark; the next rising presence edge resumes."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    entry = _entry(entry_id="fallback", room_name="Master Bathroom Fallback")
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    contexts = await _replay_incident_burst(mock_hass, observer, clock)
    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=contexts[LIGHT])
    await coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, contexts[LIGHT])
    )
    assert coordinator.get_quieted(LIGHT) is True

    # Room becomes vacant: this only arms the latch, it must not turn anything on.
    mock_hass.services.clear()
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    await coordinator._handle_presence_change(
        _sensor_event(LOCAL_SENSOR, STATE_ON, STATE_OFF)
    )
    assert [c for c in mock_hass.services.calls if c["service"] == "turn_on"] == []
    assert coordinator.get_quieted(LIGHT) is True
    assert coordinator._override_manager.get(LIGHT).rearm_latched is True

    # Fresh rising presence after vacancy releases the hold and turns on.
    mock_hass.services.clear()
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    await coordinator._handle_presence_change(
        _sensor_event(LOCAL_SENSOR, STATE_OFF, STATE_ON)
    )
    assert coordinator.get_quieted(LIGHT) is False
    assert coordinator._override_manager.get(LIGHT) is None
    assert [c for c in mock_hass.services.calls if c["service"] == "turn_on"]

    coordinator.async_stop()


@pytest.mark.asyncio
async def test_rising_presence_without_vacancy_does_not_release(mock_hass, tmp_path):
    """Presence flapping without real vacancy must not resurrect the light."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    entry = _entry(entry_id="fallback", room_name="Master Bathroom Fallback")
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    contexts = await _replay_incident_burst(mock_hass, observer, clock)
    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=contexts[LIGHT])
    await coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, contexts[LIGHT])
    )

    mock_hass.services.clear()
    await coordinator._handle_presence_change(
        _sensor_event(LOCAL_SENSOR, STATE_OFF, STATE_ON)
    )
    assert coordinator.get_quieted(LIGHT) is True
    assert [c for c in mock_hass.services.calls if c["service"] == "turn_on"] == []

    coordinator.async_stop()


@pytest.mark.asyncio
async def test_max_age_safeguard_only_arms_and_never_relights(mock_hass, tmp_path):
    """The max-age safeguard arms the latch; it must never reconcile a relight."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    entry = _entry(entry_id="fallback", room_name="Master Bathroom Fallback")
    entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_QUIETED_MAX_AGE] = 60
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    contexts = await _replay_incident_burst(mock_hass, observer, clock)
    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=contexts[LIGHT])
    await coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, contexts[LIGHT])
    )
    assert coordinator._override_manager.get(LIGHT).rearm_latched is False

    # Occupancy is stuck on (classic mmWave failure); vacancy never arrives.
    mock_hass.services.clear()
    clock.advance_ms(61_000)
    await coordinator._periodic_reconciliation(None)

    assert coordinator.get_quieted(LIGHT) is True
    assert coordinator._override_manager.get(LIGHT).rearm_latched is True
    assert [c for c in mock_hass.services.calls if c["service"] == "turn_on"] == []

    coordinator.async_stop()


# ---------------------------------------------------------------------------
# 6: paired entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paired_entries_share_override_and_gate_flip_cannot_resurrect(mock_hass, tmp_path):
    """Both Master Bathroom profiles honour the override across a gate flip."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    primary = _entry(
        entry_id="primary",
        room_name="Master Bathroom",
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    fallback = _entry(
        entry_id="fallback",
        room_name="Master Bathroom (Master Bedroom Lights Off)",
        activation_condition=BEDROOM_OFF,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    # Fallback profile is the active one at command time.
    mock_hass.states.set(BEDROOM_ON, STATE_OFF)
    mock_hass.states.set(BEDROOM_OFF, STATE_ON)

    primary_coordinator = PresenceBasedLightingCoordinator(mock_hass, primary)
    fallback_coordinator = PresenceBasedLightingCoordinator(mock_hass, fallback)
    mock_hass.data[DOMAIN][primary.entry_id] = primary_coordinator
    mock_hass.data[DOMAIN][fallback.entry_id] = fallback_coordinator
    await primary_coordinator.async_start()
    await fallback_coordinator.async_start()

    contexts = await _replay_incident_burst(mock_hass, observer, clock)
    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=contexts[LIGHT])
    state_event = _state_event(LIGHT, STATE_ON, STATE_OFF, contexts[LIGHT])
    await primary_coordinator._handle_controlled_entity_change(state_event)
    await fallback_coordinator._handle_controlled_entity_change(state_event)

    # The entity-scoped override reaches BOTH profiles, including the one whose
    # activation gate was closed when the command landed.
    assert fallback_coordinator.get_quieted(LIGHT) is True
    assert primary_coordinator.get_quieted(LIGHT) is True

    # Now flip the gate: primary becomes active while the room is still occupied.
    mock_hass.services.clear()
    mock_hass.states.set(BEDROOM_ON, STATE_ON)
    mock_hass.states.set(BEDROOM_OFF, STATE_OFF)
    await primary_coordinator._handle_activation_condition_change(
        _sensor_event(BEDROOM_ON, STATE_OFF, STATE_ON)
    )
    await fallback_coordinator._handle_activation_condition_change(
        _sensor_event(BEDROOM_OFF, STATE_ON, STATE_OFF)
    )
    await primary_coordinator._periodic_reconciliation(None)

    assert [c for c in mock_hass.services.calls if c["service"] == "turn_on"] == []
    assert primary_coordinator.get_quieted(LIGHT) is True

    primary_coordinator.async_stop()
    fallback_coordinator.async_stop()


@pytest.mark.asyncio
async def test_honor_external_override_false_keeps_entry_local_behaviour(mock_hass, tmp_path):
    """The compatibility escape hatch opts an entry out of shared overrides."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    _install_clock(mock_hass, clock)

    entry = _entry(
        entry_id="opted_out",
        room_name="Opted Out",
        honor_external_override=False,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    coordinator._override_manager.set_override(
        LIGHT,
        EXTERNAL_POLICY_REARM_AFTER_CLEAR,
        reason="sibling bulk off",
    )

    assert coordinator.get_quieted(LIGHT) is False

    coordinator.async_stop()


# ---------------------------------------------------------------------------
# 7 / 8: context registry and batch cardinality
# ---------------------------------------------------------------------------


def test_one_context_covers_many_entities():
    """A single context registered for several entities classifies each one."""
    registry = PresenceCommandContextRegistry()
    entities = ["light.a", "light.b", "light.c"]
    for entity_id in entities:
        registry.register("shared-ctx", "entry_a", entity_id, STATE_OFF)

    for entity_id in entities:
        assert registry.classify(
            "entry_a", entity_id, MockContext("shared-ctx"), include_parent=False
        ) == CommandOrigin.OWN
        assert registry.classify(
            "entry_b", entity_id, MockContext("shared-ctx"), include_parent=False
        ) == CommandOrigin.SIBLING

    assert registry.entities_for_context("shared-ctx") == set(entities)
    assert registry.classify(
        "entry_a", "light.unrelated", MockContext("shared-ctx"), include_parent=False
    ) == CommandOrigin.EXTERNAL


def test_shared_entity_in_two_entries_does_not_inflate_batch_cardinality(mock_hass):
    """Batch size counts distinct entities, never config entries."""
    clock = FakeClock()
    observer = CommandBatchObserver(mock_hass, time_source=clock)
    observer.configure_entry("entry", min_distinct_entities=2, window_ms=250)
    # One controlled entity belonging to two paired profiles.
    observer.register_managed_entity("primary", LIGHT)
    observer.register_managed_entity("fallback", LIGHT)

    batch = observer.note_command(LIGHT, "turn_off", "ctx-1")
    assert batch.size == 1
    assert batch.confirmed is False

    observer.register_managed_entity("kitchen", "light.kitchen")
    clock.advance_ms(5)
    batch = observer.note_command("light.kitchen", "turn_off", "ctx-2")
    assert batch.size == 2
    assert batch.confirmed is True


def test_batch_window_and_retention(mock_hass):
    """Commands outside the window form separate batches; lookups are retained."""
    clock = FakeClock()
    observer = CommandBatchObserver(mock_hass, time_source=clock)
    observer.configure_entry("entry", min_distinct_entities=2, window_ms=250, retain_seconds=10.0)
    observer.register_managed_entity("e", "light.a")
    observer.register_managed_entity("e", "light.b")

    first = observer.note_command("light.a", "turn_off", "ctx-a")
    clock.advance_ms(251)
    second = observer.note_command("light.b", "turn_off", "ctx-b")
    assert first.batch_id != second.batch_id
    assert second.confirmed is False

    # A different service verb never joins an in-flight batch.
    clock.advance_ms(1)
    other = observer.note_command("light.a", "turn_on", "ctx-c")
    assert other.batch_id not in (first.batch_id, second.batch_id)

    # Retention keeps context resolution alive for the late state echo.
    clock.advance_ms(5000)
    assert observer.classify("ctx-b") is not None
    clock.advance_ms(6000)
    assert observer.classify("ctx-b") is None


# ---------------------------------------------------------------------------
# 9: nested group expansion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expansion_follows_ha_and_z2m_groups_with_dedupe(mock_hass, tmp_path):
    """Expansion follows entity_id and group_entities, recursively, deduped."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    _install_clock(mock_hass, clock)

    entry = _entry(entry_id="e", room_name="Room", controlled_entity=LIGHT)
    entry.data[CONF_CONTROLLED_ENTITIES].append(
        {
            **entry.data[CONF_CONTROLLED_ENTITIES][0],
            CONF_ENTITY_ID: "light.master_bedroom_closet_light",
        }
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set("light.master_bedroom_closet_light", STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    # HA light group exposes members via attributes.entity_id and includes both
    # the Z2M group and one of its members directly (as light.lights does).
    mock_hass.states.set(
        "light.lights",
        STATE_ON,
        attributes={
            "entity_id": ["light.master_bedroom", "light.master_bedroom_closet_light", LIGHT]
        },
    )
    # Zigbee2MQTT group exposes members via attributes.group_entities.
    mock_hass.states.set(
        "light.master_bedroom",
        STATE_ON,
        attributes={
            "group_entities": [
                "light.master_bedroom_window_light",
                "light.master_bedroom_closet_light",
            ]
        },
    )

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    matched = coordinator._expand_target_entities("light.lights")
    assert sorted(matched) == sorted([LIGHT, "light.master_bedroom_closet_light"])
    assert len(matched) == len(set(matched))

    coordinator.async_stop()


# ---------------------------------------------------------------------------
# 10: redundant off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redundant_turn_off_of_already_off_entity_does_not_pause(mock_hass, tmp_path):
    """An already-off entity told to turn off must not be paused."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    _install_clock(mock_hass, clock)

    entry = _entry(entry_id="e", room_name="Room")
    mock_hass.states.set(LIGHT, STATE_OFF)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    await coordinator._handle_external_action(LIGHT, "turn_off", MockContext("bulk"))

    assert coordinator.get_automation_paused(LIGHT) is False
    assert coordinator._override_manager.get(LIGHT) is None

    coordinator.async_stop()


# ---------------------------------------------------------------------------
# 11: kill switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expect_quieted,expect_paused",
    [
        (BATCH_MODE_ENFORCE, True, False),
        (BATCH_MODE_OBSERVE, False, True),
        (BATCH_MODE_OFF, False, True),
    ],
)
async def test_batch_mode_kill_switch(mock_hass, tmp_path, mode, expect_quieted, expect_paused):
    """observe and off keep legacy PAUSE; only enforce changes behaviour."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    entry = _entry(entry_id="e", room_name="Room", batch_mode=mode)
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    if mode == BATCH_MODE_OFF:
        assert mock_hass.bus.listeners_for(EVENT_HOMEKIT_STATE_CHANGE) == []
        context = MockContext("homekit-x")
    else:
        contexts = await _replay_incident_burst(mock_hass, observer, clock)
        context = contexts[LIGHT]

    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=context)
    await coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, context)
    )

    assert coordinator.get_quieted(LIGHT) is expect_quieted
    assert coordinator.get_automation_paused(LIGHT) is expect_paused

    coordinator.async_stop()


# ---------------------------------------------------------------------------
# 12: migration / options round trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_v10_to_v11_backfills_new_settings():
    """v10 entries gain the new keys with behaviour-preserving defaults."""
    hass = MagicMock()
    entry = _entry(entry_id="legacy", room_name="Legacy", version=10)
    for key in (
        CONF_HOMEKIT_BATCH_MODE,
        CONF_BATCH_WINDOW_MS,
        CONF_BATCH_RETAIN_SECONDS,
        CONF_BATCH_MIN_DISTINCT_ENTITIES,
    ):
        entry.data.pop(key, None)
    for key in (
        CONF_HONOR_EXTERNAL_OVERRIDE,
        CONF_UNKNOWN_SOURCE_POLICY,
        CONF_QUIETED_MAX_AGE,
    ):
        entry.data[CONF_CONTROLLED_ENTITIES][0].pop(key, None)

    def track_update(e, data=None, version=None):
        if data is not None:
            e.data = data
        if version is not None:
            e.version = version

    hass.config_entries.async_update_entry = track_update

    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 11

    assert entry.data[CONF_HOMEKIT_BATCH_MODE] == DEFAULT_HOMEKIT_BATCH_MODE
    assert (
        entry.data[CONF_BATCH_MIN_DISTINCT_ENTITIES]
        == DEFAULT_BATCH_MIN_DISTINCT_ENTITIES
    )

    entity_config = entry.data[CONF_CONTROLLED_ENTITIES][0]
    # Entity-scoped overrides are coherent by default so a paired profile cannot
    # resurrect a light another profile just released.
    assert entity_config[CONF_HONOR_EXTERNAL_OVERRIDE] is True
    # Unknown sources keep legacy PAUSE semantics for wall switches.
    assert entity_config[CONF_UNKNOWN_SOURCE_POLICY] == EXTERNAL_POLICY_PAUSE
    assert entity_config[CONF_QUIETED_MAX_AGE] == DEFAULT_QUIETED_MAX_AGE


@pytest.mark.asyncio
async def test_migration_is_idempotent_and_preserves_explicit_values():
    """Re-running migration must not clobber explicitly configured values."""
    hass = MagicMock()
    entry = _entry(entry_id="legacy", room_name="Legacy", version=10)
    entry.data[CONF_HOMEKIT_BATCH_MODE] = BATCH_MODE_OBSERVE
    entry.data[CONF_BATCH_MIN_DISTINCT_ENTITIES] = 12
    entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_HONOR_EXTERNAL_OVERRIDE] = False

    def track_update(e, data=None, version=None):
        if data is not None:
            e.data = data
        if version is not None:
            e.version = version

    hass.config_entries.async_update_entry = track_update

    assert await async_migrate_entry(hass, entry) is True
    assert entry.data[CONF_HOMEKIT_BATCH_MODE] == BATCH_MODE_OBSERVE
    assert entry.data[CONF_BATCH_MIN_DISTINCT_ENTITIES] == 12
    assert entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_HONOR_EXTERNAL_OVERRIDE] is False

    # Already at v11: a second pass is a no-op.
    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 11


@pytest.mark.asyncio
async def test_options_flow_round_trip_preserves_new_entity_settings():
    """Editing an entity must not drop settings without a dedicated UI field."""
    from custom_components.presence_based_lighting.config_flow import (
        _carry_forward_entity_settings,
    )

    # Explicit values survive a rebuild of the entity config dict.
    existing = {
        CONF_ENTITY_ID: LIGHT,
        CONF_HONOR_EXTERNAL_OVERRIDE: False,
        CONF_UNKNOWN_SOURCE_POLICY: EXTERNAL_POLICY_PAUSE,
        CONF_QUIETED_MAX_AGE: 900,
    }
    rebuilt = _carry_forward_entity_settings({CONF_ENTITY_ID: LIGHT}, existing)
    assert rebuilt[CONF_HONOR_EXTERNAL_OVERRIDE] is False
    assert rebuilt[CONF_UNKNOWN_SOURCE_POLICY] == EXTERNAL_POLICY_PAUSE
    assert rebuilt[CONF_QUIETED_MAX_AGE] == 900

    # A brand new entity gets the behaviour-preserving defaults.
    fresh = _carry_forward_entity_settings({CONF_ENTITY_ID: LIGHT}, {})
    assert fresh[CONF_HONOR_EXTERNAL_OVERRIDE] is DEFAULT_HONOR_EXTERNAL_OVERRIDE
    assert fresh[CONF_UNKNOWN_SOURCE_POLICY] == DEFAULT_UNKNOWN_SOURCE_POLICY
    assert fresh[CONF_QUIETED_MAX_AGE] == DEFAULT_QUIETED_MAX_AGE


@pytest.mark.asyncio
async def test_early_member_is_upgraded_when_batch_is_confirmed_later(
    mock_hass, tmp_path
):
    """An entity classified before the burst is recognised gets re-classified.

    A burst is only recognisable once enough commands have arrived, so the first
    few members may already have been recorded under the fallback PAUSE policy.
    They must be promoted rather than left paused.
    """
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    entry = _entry(entry_id="fallback", room_name="Master Bathroom Fallback")
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    listeners = mock_hass.bus.listeners_for(EVENT_HOMEKIT_STATE_CHANGE)

    # Our entity is the very first command of the burst.
    context = MockContext("homekit-0")
    await listeners[0](_homekit_event(LIGHT, "turn_off", context))

    # Its state echo arrives before the rest of the burst is seen.
    mock_hass.states.set(LIGHT, STATE_OFF, context=context)
    await coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, context)
    )
    assert coordinator.get_automation_paused(LIGHT) is True
    assert coordinator.get_quieted(LIGHT) is False

    # The rest of the burst arrives and crosses the threshold.
    for index, entity_id in enumerate(INCIDENT_ENTITIES[1:], start=1):
        clock.value += INCIDENT_OFFSETS_MS[index] / 1000.0
        await listeners[0](
            _homekit_event(entity_id, "turn_off", MockContext(f"homekit-{index}"))
        )

    assert coordinator.get_quieted(LIGHT) is True
    assert coordinator.get_automation_paused(LIGHT) is False
    assert (
        coordinator._override_manager.get(LIGHT).policy
        == EXTERNAL_POLICY_REARM_AFTER_CLEAR
    )

    coordinator.async_stop()


# ---------------------------------------------------------------------------
# Observability and escape hatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_state_exposes_override_diagnostics(mock_hass, tmp_path):
    """Switch attributes must surface override, batch and latch diagnostics."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    entry = _entry(entry_id="e", room_name="Room")
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    contexts = await _replay_incident_burst(mock_hass, observer, clock)
    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=contexts[LIGHT])
    await coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, contexts[LIGHT])
    )

    attributes = coordinator.get_entity_control_state(LIGHT)
    assert attributes["quieted"] is True
    assert attributes["external_override_policy"] == EXTERNAL_POLICY_REARM_AFTER_CLEAR
    assert attributes["external_override_batch_id"]
    assert attributes["external_override_batch_size"] >= 8
    assert attributes["rearm_latched"] is False
    assert attributes["external_override_expires_at"]
    assert attributes["homekit_batch_mode"] == BATCH_MODE_ENFORCE
    assert attributes["unknown_source_count"] == 0

    coordinator.async_stop()


@pytest.mark.asyncio
async def test_resume_all_automation_clears_pause_and_quieted(mock_hass, tmp_path):
    """The escape hatch releases both pause and quieted holds everywhere."""
    from custom_components.presence_based_lighting.service_handlers import (
        SERVICE_RESUME_ALL_AUTOMATION,
        async_register_services,
    )

    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    entry = _entry(entry_id="e", room_name="Room")
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    contexts = await _replay_incident_burst(mock_hass, observer, clock)
    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=contexts[LIGHT])
    await coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, contexts[LIGHT])
    )
    assert coordinator.get_quieted(LIGHT) is True

    await async_register_services(mock_hass, PresenceBasedLightingCoordinator)
    handler = mock_hass.services._registered[(DOMAIN, SERVICE_RESUME_ALL_AUTOMATION)]
    await handler(MagicMock(data={}))

    assert coordinator.get_quieted(LIGHT) is False
    assert coordinator.get_automation_paused(LIGHT) is False
    assert coordinator._override_manager.get(LIGHT) is None

    coordinator.async_stop()


# ---------------------------------------------------------------------------
# Review fix 1: quieted max-age must survive a restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quieted_max_age_is_not_reset_by_restart(mock_hass, tmp_path):
    """A restart must not restart the max-age budget of an existing hold."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, manager = _install_clock(mock_hass, clock)

    entry = _entry(entry_id="fallback", room_name="Master Bathroom Fallback")
    entry.data[CONF_CONTROLLED_ENTITIES][0][CONF_QUIETED_MAX_AGE] = 3600
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    mock_hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_start()

    contexts = await _replay_incident_burst(mock_hass, observer, clock)
    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=contexts[LIGHT])
    await coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, contexts[LIGHT])
    )
    assert coordinator.get_quieted(LIGHT) is True

    original = coordinator._override_manager.get(LIGHT)
    assert original.rearm_latched is False
    original_created_at = original.created_at

    await coordinator._save_paused_state()
    persisted = json.loads(
        (tmp_path / ".storage" / f"pbl_paused_{entry.entry_id}.json").read_text()
    )
    assert persisted["quieted_entities"] == [LIGHT]
    assert persisted["quieted"][LIGHT]["created_at"] == original_created_at
    assert persisted["quieted"][LIGHT]["max_age_seconds"] == 3600

    # 59 wall-clock minutes pass while Home Assistant is down.
    coordinator.async_stop()
    now = _dt.datetime.now(_dt.timezone.utc)
    with patch.object(
        dt_util, "utcnow", lambda: now + _dt.timedelta(minutes=59)
    ):
        restart_clock = FakeClock(start=50_000.0)
        observer2, manager2 = _install_clock(mock_hass, restart_clock)
        restarted = PresenceBasedLightingCoordinator(mock_hass, entry)
        mock_hass.data[DOMAIN][entry.entry_id] = restarted
        # Re-stamp the persisted creation time relative to the patched clock.
        persisted["quieted"][LIGHT]["created_at"] = now.isoformat()
        (tmp_path / ".storage" / f"pbl_paused_{entry.entry_id}.json").write_text(
            json.dumps(persisted)
        )
        await restarted.async_start()

        assert restarted.get_quieted(LIGHT) is True
        restored = restarted._override_manager.get(LIGHT)
        assert restored is not None
        # The hold is 59 minutes old, not 0: its budget was not reset.
        assert restored.is_past_max_age(restart_clock.value) is False
        elapsed = restart_clock.value - restored.created_monotonic
        assert 3500 < elapsed < 3600

        # One more minute and the safeguard arms - it would never have armed if
        # the restart had reset the age.
        restart_clock.advance_ms(120_000)
        assert restored.is_past_max_age(restart_clock.value) is True
        await restarted._periodic_reconciliation(None)
        assert restarted._override_manager.get(LIGHT).rearm_latched is True
        assert restarted.get_quieted(LIGHT) is True
        assert [c for c in mock_hass.services.calls if c["service"] == "turn_on"] == []

    restarted.async_stop()


@pytest.mark.asyncio
async def test_restore_override_clamps_unusable_timestamps(mock_hass, tmp_path):
    """Future or unparseable creation times must not age a hold prematurely."""
    clock = FakeClock(start=5_000.0)
    manager = ExternalOverrideManager(time_source=clock)

    future = (dt_util.utcnow() + _dt.timedelta(hours=5)).isoformat()
    record = manager.restore_override(
        LIGHT,
        EXTERNAL_POLICY_REARM_AFTER_CLEAR,
        created_at=future,
        max_age_seconds=60,
    )
    assert record.is_past_max_age(clock.value) is False

    garbage = manager.restore_override(
        "light.other",
        EXTERNAL_POLICY_REARM_AFTER_CLEAR,
        created_at="not-a-timestamp",
        max_age_seconds=60,
    )
    assert garbage.is_past_max_age(clock.value) is False


@pytest.mark.asyncio
async def test_restored_rearm_latch_is_preserved(mock_hass, tmp_path):
    """An already-armed latch survives the restart."""
    clock = FakeClock()
    manager = ExternalOverrideManager(time_source=clock)
    record = manager.restore_override(
        LIGHT,
        EXTERNAL_POLICY_REARM_AFTER_CLEAR,
        created_at=dt_util.utcnow().isoformat(),
        rearm_latched=True,
        rearm_latched_at="2026-08-01T00:00:00+00:00",
        max_age_seconds=60,
    )
    assert record.rearm_latched is True
    assert record.rearm_latched_at == "2026-08-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Review fix 2: deterministic multi-entry observer configuration
# ---------------------------------------------------------------------------


def test_observer_config_is_order_independent(mock_hass):
    """The reduced configuration must not depend on entry setup order."""
    def build(order):
        observer = CommandBatchObserver(mock_hass)
        for entry_id, config in order:
            observer.configure_entry(entry_id, **config)
        return observer.effective_config

    configs = [
        ("a", {"mode": BATCH_MODE_ENFORCE, "window_ms": 250,
               "retain_seconds": 10.0, "min_distinct_entities": 8}),
        ("b", {"mode": BATCH_MODE_OBSERVE, "window_ms": 400,
               "retain_seconds": 30.0, "min_distinct_entities": 4}),
        ("c", {"mode": BATCH_MODE_ENFORCE, "window_ms": 150,
               "retain_seconds": 5.0, "min_distinct_entities": 12}),
    ]

    forward = build(configs)
    reverse = build(list(reversed(configs)))
    shuffled = build([configs[1], configs[2], configs[0]])

    assert forward == reverse == shuffled
    # observe beats enforce; smallest window; largest threshold; longest retention
    assert forward["mode"] == BATCH_MODE_OBSERVE
    assert forward["window_ms"] == 150
    assert forward["min_distinct_entities"] == 12
    assert forward["retain_seconds"] == 30.0


def test_observer_mode_precedence_off_beats_observe_beats_enforce(mock_hass):
    """Any off wins; otherwise any observe wins; otherwise enforce."""
    observer = CommandBatchObserver(mock_hass)
    observer.configure_entry("a", mode=BATCH_MODE_ENFORCE)
    assert observer.mode == BATCH_MODE_ENFORCE

    observer.configure_entry("b", mode=BATCH_MODE_OBSERVE)
    assert observer.mode == BATCH_MODE_OBSERVE

    observer.configure_entry("c", mode=BATCH_MODE_OFF)
    assert observer.mode == BATCH_MODE_OFF

    # Later enforce entries cannot override a stricter sibling.
    observer.configure_entry("d", mode=BATCH_MODE_ENFORCE)
    assert observer.mode == BATCH_MODE_OFF


def test_observer_recomputes_on_unregister_and_reload(mock_hass):
    """Removing an entry recomputes; re-adding it re-applies its settings."""
    observer = CommandBatchObserver(mock_hass)
    observer.configure_entry("a", mode=BATCH_MODE_ENFORCE, window_ms=250,
                             retain_seconds=10.0, min_distinct_entities=8)
    observer.configure_entry("strict", mode=BATCH_MODE_OFF, window_ms=100,
                             retain_seconds=60.0, min_distinct_entities=20)
    assert observer.mode == BATCH_MODE_OFF
    assert observer.effective_config["window_ms"] == 100
    assert observer.effective_config["min_distinct_entities"] == 20
    assert observer.effective_config["retain_seconds"] == 60.0

    observer.unregister_entry("strict")
    assert observer.mode == BATCH_MODE_ENFORCE
    assert observer.effective_config["window_ms"] == 250
    assert observer.effective_config["min_distinct_entities"] == 8
    assert observer.effective_config["retain_seconds"] == 10.0
    assert observer.effective_config["configured_entries"] == ["a"]

    # Reload restores the stricter values deterministically.
    observer.configure_entry("strict", mode=BATCH_MODE_OFF, window_ms=100,
                             retain_seconds=60.0, min_distinct_entities=20)
    assert observer.mode == BATCH_MODE_OFF


def test_observer_falls_back_to_defaults_when_no_entries(mock_hass):
    """With every entry gone the observer returns to documented defaults."""
    observer = CommandBatchObserver(mock_hass)
    observer.configure_entry("a", mode=BATCH_MODE_OFF, window_ms=999,
                             retain_seconds=99.0, min_distinct_entities=42)
    observer.unregister_entry("a")

    config = observer.effective_config
    assert config["mode"] == DEFAULT_HOMEKIT_BATCH_MODE
    assert config["window_ms"] == 250
    assert config["min_distinct_entities"] == DEFAULT_BATCH_MIN_DISTINCT_ENTITIES
    assert config["configured_entries"] == []


@pytest.mark.asyncio
async def test_conflicting_entries_use_strictest_threshold(mock_hass, tmp_path):
    """A stricter sibling threshold suppresses a batch the other would accept."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    lenient = _entry(entry_id="lenient", room_name="Lenient", batch_min_entities=4)
    strict = _entry(
        entry_id="strict",
        room_name="Strict",
        controlled_entity="light.other_room",
        batch_min_entities=20,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set("light.other_room", STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)

    lenient_coordinator = PresenceBasedLightingCoordinator(mock_hass, lenient)
    strict_coordinator = PresenceBasedLightingCoordinator(mock_hass, strict)
    mock_hass.data[DOMAIN][lenient.entry_id] = lenient_coordinator
    mock_hass.data[DOMAIN][strict.entry_id] = strict_coordinator
    await lenient_coordinator.async_start()
    await strict_coordinator.async_start()

    assert observer.effective_config["min_distinct_entities"] == 20

    contexts = await _replay_incident_burst(mock_hass, observer, clock)
    # 16 entities is below the strictest threshold of 20, so no batch.
    assert observer.classify(contexts[LIGHT].id).confirmed is False

    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=contexts[LIGHT])
    await lenient_coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, contexts[LIGHT])
    )
    assert lenient_coordinator.get_quieted(LIGHT) is False
    assert lenient_coordinator.get_automation_paused(LIGHT) is True

    lenient_coordinator.async_stop()
    strict_coordinator.async_stop()


# ---------------------------------------------------------------------------
# Review fix 3: domain-wide listener lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listener_lifecycle_across_entry_setup_and_unload(mock_hass, tmp_path):
    """One listener for many entries; detached only when the last one unloads."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    def listener_count():
        return len(mock_hass.bus.listeners_for(EVENT_HOMEKIT_STATE_CHANGE))

    assert listener_count() == 0
    assert observer.is_listening is False

    first = _entry(entry_id="first", room_name="First")
    second = _entry(
        entry_id="second", room_name="Second", controlled_entity="light.second"
    )
    third = _entry(
        entry_id="third", room_name="Third", controlled_entity="light.third"
    )
    for eid in (LIGHT, "light.second", "light.third"):
        mock_hass.states.set(eid, STATE_OFF)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)

    coordinators = {}
    for entry in (first, second, third):
        coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
        mock_hass.data[DOMAIN][entry.entry_id] = coordinator
        coordinators[entry.entry_id] = coordinator
        await coordinator.async_start()

    # First setup attaches exactly one listener; later entries reuse it.
    assert listener_count() == 1
    assert observer.is_listening is True
    assert observer.effective_config["listening_entries"] == [
        "first", "second", "third"
    ]

    # Partial unload keeps the listener alive for the remaining entries.
    coordinators["second"].async_stop()
    assert listener_count() == 1
    assert observer.is_listening is True
    assert observer.effective_config["listening_entries"] == ["first", "third"]
    assert observer.effective_config["configured_entries"] == ["first", "third"]

    # Batches still work for the survivors.
    assert observer.note_command(LIGHT, "turn_off", "ctx-live") is not None

    coordinators["first"].async_stop()
    assert listener_count() == 1
    assert observer.is_listening is True

    # Last unload detaches the shared listener.
    coordinators["third"].async_stop()
    assert listener_count() == 0
    assert observer.is_listening is False
    assert observer.effective_config["listening_entries"] == []
    assert observer.effective_config["configured_entries"] == []

    # A subsequent reload re-attaches cleanly.
    reloaded = PresenceBasedLightingCoordinator(mock_hass, first)
    mock_hass.data[DOMAIN][first.entry_id] = reloaded
    await reloaded.async_start()
    assert listener_count() == 1
    assert observer.is_listening is True

    reloaded.async_stop()
    assert listener_count() == 0


@pytest.mark.asyncio
async def test_batch_mode_off_entry_detaches_shared_listener(mock_hass, tmp_path):
    """An 'off' entry detaches the listener regardless of setup order."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    def listener_count():
        return len(mock_hass.bus.listeners_for(EVENT_HOMEKIT_STATE_CHANGE))

    enforcing = _entry(entry_id="enforcing", room_name="Enforcing")
    disabled = _entry(
        entry_id="disabled",
        room_name="Disabled",
        controlled_entity="light.disabled",
        batch_mode=BATCH_MODE_OFF,
    )
    mock_hass.states.set(LIGHT, STATE_OFF)
    mock_hass.states.set("light.disabled", STATE_OFF)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)

    enforcing_coordinator = PresenceBasedLightingCoordinator(mock_hass, enforcing)
    mock_hass.data[DOMAIN][enforcing.entry_id] = enforcing_coordinator
    await enforcing_coordinator.async_start()
    assert listener_count() == 1

    # The 'off' entry loads second and must still win, detaching the listener.
    disabled_coordinator = PresenceBasedLightingCoordinator(mock_hass, disabled)
    mock_hass.data[DOMAIN][disabled.entry_id] = disabled_coordinator
    await disabled_coordinator.async_start()
    assert observer.mode == BATCH_MODE_OFF
    assert listener_count() == 0

    # Unloading it re-enables detection for the remaining entry.
    disabled_coordinator.async_stop()
    assert observer.mode == BATCH_MODE_ENFORCE
    assert listener_count() == 1

    enforcing_coordinator.async_stop()
    assert listener_count() == 0


@pytest.mark.asyncio
async def test_partial_unload_does_not_drop_sibling_batch_callbacks(mock_hass, tmp_path):
    """Unloading one entry must not disable bulk handling for its siblings."""
    clock = FakeClock()
    _configure_storage(mock_hass, tmp_path)
    observer, _manager = _install_clock(mock_hass, clock)

    keep = _entry(entry_id="keep", room_name="Keep", batch_min_entities=8)
    drop = _entry(
        entry_id="drop", room_name="Drop", controlled_entity="light.drop",
        batch_min_entities=8,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set("light.drop", STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)

    keep_coordinator = PresenceBasedLightingCoordinator(mock_hass, keep)
    drop_coordinator = PresenceBasedLightingCoordinator(mock_hass, drop)
    mock_hass.data[DOMAIN][keep.entry_id] = keep_coordinator
    mock_hass.data[DOMAIN][drop.entry_id] = drop_coordinator
    await keep_coordinator.async_start()
    await drop_coordinator.async_start()

    drop_coordinator.async_stop()

    contexts = await _replay_incident_burst(mock_hass, observer, clock)
    clock.advance_ms(758)
    mock_hass.states.set(LIGHT, STATE_OFF, context=contexts[LIGHT])
    await keep_coordinator._handle_controlled_entity_change(
        _state_event(LIGHT, STATE_ON, STATE_OFF, contexts[LIGHT])
    )

    assert keep_coordinator.get_quieted(LIGHT) is True
    assert keep_coordinator.get_automation_paused(LIGHT) is False

    keep_coordinator.async_stop()
