"""Regression tests for activation-gated shared-entity arbitration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import (
    ActuationStatus,
    EntityAutomationState,
    IntentReason,
    PresenceBasedLightingCoordinator,
)
from custom_components.presence_based_lighting.const import (
    CONF_ACTIVATION_CONDITIONS,
    CONF_CLEARING_SENSORS,
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_INITIAL_PRESENCE_ALLOWED,
    CONF_MANUAL_DISABLE_STATES,
    CONF_OFF_DELAY,
    CONF_PRESENCE_CLEARED_SERVICE,
    CONF_PRESENCE_CLEARED_STATE,
    CONF_PRESENCE_DETECTED_SERVICE,
    CONF_PRESENCE_DETECTED_STATE,
    CONF_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE,
    CONF_PRESENCE_SENSORS,
    CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
    CONF_REQUIRE_VACANCY_FOR_CLEARED,
    CONF_RESPECTS_PRESENCE_ALLOWED,
    CONF_ROOM_NAME,
    DOMAIN,
    NO_ACTION,
)
from tests.conftest import MockContext


LIGHT = "light.master_bathroom"
LOCAL_SENSOR = "binary_sensor.master_bathroom_presence"
BEDROOM_SENSOR = "binary_sensor.master_bedroom_bathroom_presence"
BEDROOM_ON = "input_boolean.master_bedroom_presence_on"
BEDROOM_OFF = "input_boolean.master_bedroom_presence_off"


def _entry(
    *,
    entry_id: str,
    room_name: str,
    presence_sensors: list[str],
    activation_condition: str,
    presence_lock: bool,
    clearing_sensors: list[str] | None = None,
    off_delay: int = 30,
):
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.entry_id = entry_id
    entry.unique_id = room_name
    entry.version = 10
    entry.data = {
        CONF_ROOM_NAME: room_name,
        CONF_PRESENCE_SENSORS: presence_sensors,
        CONF_ACTIVATION_CONDITIONS: [activation_condition],
        CONF_OFF_DELAY: off_delay,
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: LIGHT,
                CONF_PRESENCE_DETECTED_SERVICE: "turn_on",
                CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
                CONF_PRESENCE_DETECTED_STATE: STATE_ON,
                CONF_PRESENCE_CLEARED_STATE: STATE_OFF,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: not presence_lock,
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: presence_lock,
                CONF_REQUIRE_VACANCY_FOR_CLEARED: presence_lock,
                CONF_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE: False,
                CONF_MANUAL_DISABLE_STATES: [STATE_OFF],
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            }
        ],
    }
    if clearing_sensors is not None:
        entry.data[CONF_CLEARING_SENSORS] = clearing_sensors
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock()
    return entry


def _condition_event(entity_id: str, old_state: str, new_state: str):
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": type("State", (), {"state": old_state})(),
                "new_state": type("State", (), {"state": new_state})(),
            }
        },
    )()


def _service_event(service: str, context: MockContext):
    return type(
        "Event",
        (),
        {
            "data": {
                "service_data": {"entity_id": LIGHT},
                "service": service,
            },
            "context": context,
        },
    )()


def _state_event(old_state: str, new_state: str, context: MockContext):
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": LIGHT,
                "old_state": type(
                    "State",
                    (),
                    {
                        "state": old_state,
                        "attributes": {},
                        "context": MockContext("old"),
                    },
                )(),
                "new_state": type(
                    "State",
                    (),
                    {
                        "state": new_state,
                        "attributes": {},
                        "context": context,
                    },
                )(),
            }
        },
    )()


def _sensor_event(entity_id: str, old_state: str, new_state: str):
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": type(
                    "State",
                    (),
                    {"state": old_state, "attributes": {}},
                )(),
                "new_state": type(
                    "State",
                    (),
                    {"state": new_state, "attributes": {}},
                )(),
            }
        },
    )()


def _turn_on_calls(mock_hass):
    return [
        call
        for call in mock_hass.services.calls
        if call["domain"] == "light"
        and call["service"] == "turn_on"
        and call["service_data"].get("entity_id") == LIGHT
    ]


def _configure_storage(mock_hass, tmp_path):
    tmp_path.joinpath(".storage").mkdir()
    mock_hass.config = MagicMock()
    mock_hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))

    async def run_sync(func, *args):
        return func(*args)

    mock_hass.async_add_executor_job = run_sync


@pytest.mark.asyncio
async def test_inactive_presence_lock_does_not_revert_external_off(mock_hass):
    """A deactivated Presence Lock entry must not force an occupied light on."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR, BEDROOM_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_OFF)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        mock_hass.services.clear()
        await coordinator._handle_external_action(LIGHT, "turn_off")

        assert _turn_on_calls(mock_hass) == []
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_strict_presence_lock_reverts_off_while_paused(mock_hass):
    """Strict Presence Lock ignores pause state when manual override is disabled."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        coordinator._set_entity_state(
            LIGHT,
            entity_state,
            EntityAutomationState.PAUSED,
            "test strict lock pause",
        )
        mock_hass.services.clear()

        await coordinator._handle_external_action(LIGHT, "turn_off")
        await coordinator._handle_controlled_entity_change(
            _state_event(STATE_ON, STATE_OFF, MockContext("external"))
        )

        assert len(_turn_on_calls(mock_hass)) == 2
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_strict_presence_lock_correction_preserves_paused_state(mock_hass):
    """Strict vacancy correction must not silently resume a paused entity."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_OFF)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        coordinator.set_automation_paused(
            LIGHT,
            True,
            reason="test user pause",
            source="service",
        )
        mock_hass.services.clear()

        await coordinator._handle_external_action(LIGHT, "turn_on")

        entity_state = coordinator._entity_states[LIGHT]
        assert [
            call
            for call in mock_hass.services.calls
            if call["service"] == "turn_off"
            and call["service_data"].get("entity_id") == LIGHT
        ]
        assert coordinator.get_automation_paused(LIGHT) is True
        coordinator._confirm_entity_actuation(LIGHT, entity_state, STATE_OFF)
        assert coordinator.get_automation_paused(LIGHT) is True
        assert entity_state["pause"]["reason"] == "test user pause"
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_strict_no_action_correction_preserves_paused_state(
    mock_hass, tmp_path
):
    """A suppressed strict correction must not clear an explicit pause."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    entry.data[CONF_CONTROLLED_ENTITIES][0][
        CONF_PRESENCE_CLEARED_SERVICE
    ] = NO_ACTION
    mock_hass.states.set(LIGHT, STATE_OFF)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)
    _configure_storage(mock_hass, tmp_path)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        coordinator.set_automation_paused(
            LIGHT,
            True,
            reason="test user pause",
            source="service",
        )
        mock_hass.services.clear()

        await coordinator._handle_external_action(LIGHT, "turn_on")

        entity_state = coordinator._entity_states[LIGHT]
        assert coordinator.get_automation_paused(LIGHT) is True
        assert entity_state["pause"]["reason"] == "test user pause"
        assert entity_state["intent"]["reason"] == IntentReason.NO_ACTION
        assert mock_hass.services.calls == []
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_strict_ownership_suppression_preserves_paused_state(
    mock_hass, tmp_path
):
    """Sibling ownership suppression must not silently resume a paused entry."""
    sensor_a = "binary_sensor.entry_a"
    sensor_b = "binary_sensor.entry_b"
    entry_a = _entry(
        entry_id="entry_a",
        room_name="Entry A",
        presence_sensors=[sensor_a],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    entry_b = _entry(
        entry_id="entry_b",
        room_name="Entry B",
        presence_sensors=[sensor_b],
        activation_condition=BEDROOM_ON,
        presence_lock=False,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(sensor_a, STATE_OFF)
    mock_hass.states.set(sensor_b, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)
    _configure_storage(mock_hass, tmp_path)

    coordinator_a = PresenceBasedLightingCoordinator(mock_hass, entry_a)
    coordinator_b = PresenceBasedLightingCoordinator(mock_hass, entry_b)
    await coordinator_a.async_start()
    await coordinator_b.async_start()

    try:
        coordinator_b._set_entity_state(
            LIGHT,
            coordinator_b._entity_states[LIGHT],
            EntityAutomationState.OCCUPIED,
            "test sibling owner",
        )
        coordinator_a.set_automation_paused(
            LIGHT,
            True,
            reason="test user pause",
            source="service",
        )
        mock_hass.services.clear()

        await coordinator_a._handle_external_action(LIGHT, "turn_on")

        entity_state = coordinator_a._entity_states[LIGHT]
        assert coordinator_a.get_automation_paused(LIGHT) is True
        assert entity_state["pause"]["reason"] == "test user pause"
        assert entity_state["intent"]["reason"] == IntentReason.OWNERSHIP
        assert mock_hass.services.calls == []
    finally:
        coordinator_a.async_stop()
        coordinator_b.async_stop()


@pytest.mark.asyncio
async def test_strict_presence_lock_reverts_off_when_toggle_is_disabled(mock_hass):
    """Strict Presence Lock ignores the per-entity toggle by configuration."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        entity_state["presence_allowed"] = False
        mock_hass.services.clear()

        await coordinator._handle_external_action(LIGHT, "turn_off")

        assert len(_turn_on_calls(mock_hass)) == 1
    finally:
        coordinator.async_stop()


def test_strict_presence_lock_enforcement_ignores_pause_but_not_gate(mock_hass):
    """Interceptor enforcement mirrors strict fallback semantics."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    entity_state = coordinator._entity_states[LIGHT]
    entity_state["state"] = EntityAutomationState.PAUSED
    entity_state["presence_allowed"] = False

    assert coordinator._entity_may_enforce_presence_lock(LIGHT) is True

    mock_hass.states.set(BEDROOM_ON, STATE_OFF)
    assert coordinator._entity_may_enforce_presence_lock(LIGHT) is False


@pytest.mark.asyncio
async def test_condition_off_releases_ownership_without_turning_light_off(mock_hass):
    """Closing an activation gate releases ownership but preserves the light."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR, BEDROOM_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        assert entity_state["state"] == EntityAutomationState.OCCUPIED

        mock_hass.services.clear()
        mock_hass.states.set(BEDROOM_ON, STATE_OFF)
        await coordinator._handle_activation_condition_change(
            _condition_event(BEDROOM_ON, STATE_ON, STATE_OFF)
        )

        assert entity_state["state"] == EntityAutomationState.PENDING_ACTIVATION
        assert coordinator._ownership_manager.other_entry_wants_on(
            "other_entry", LIGHT
        ) is False
        assert [
            call
            for call in mock_hass.services.calls
            if call["service"] == "turn_off"
        ] == []
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_inactive_pending_entry_is_not_repromoted_by_reconciliation(mock_hass):
    """A lit shared entity must not reactivate a condition-gated controller."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR, BEDROOM_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_OFF)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        coordinator._set_entity_state(
            LIGHT,
            entity_state,
            EntityAutomationState.PENDING_ACTIVATION,
            "test inactive gate",
        )

        await coordinator._reconcile_entity(LIGHT, entity_state)

        assert entity_state["state"] == EntityAutomationState.PENDING_ACTIVATION
        assert coordinator._ownership_manager.other_entry_wants_on(
            "other_entry", LIGHT
        ) is False
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_condition_off_leaves_clearing_running_but_releases_ownership(
    mock_hass,
):
    """An in-flight clear may finish without retaining shared on-ownership."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        coordinator._set_entity_state(
            LIGHT,
            entity_state,
            EntityAutomationState.CLEARING,
            "test clearing",
        )
        mock_hass.states.set(BEDROOM_ON, STATE_OFF)
        await coordinator._handle_activation_condition_change(
            _condition_event(BEDROOM_ON, STATE_ON, STATE_OFF)
        )

        assert entity_state["state"] == EntityAutomationState.CLEARING
        assert coordinator._ownership_manager.other_entry_wants_on(
            "other_entry", LIGHT
        ) is False
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_condition_off_preserves_waiting_for_clear_without_ownership(
    mock_hass,
):
    """A served vacancy delay remains served after the activation gate closes."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        coordinator._set_entity_state(
            LIGHT,
            entity_state,
            EntityAutomationState.WAITING_FOR_CLEAR,
            "test waiting",
        )
        mock_hass.states.set(BEDROOM_ON, STATE_OFF)
        await coordinator._handle_activation_condition_change(
            _condition_event(BEDROOM_ON, STATE_ON, STATE_OFF)
        )

        assert entity_state["state"] == EntityAutomationState.WAITING_FOR_CLEAR
        assert coordinator._ownership_manager.other_entry_wants_on(
            "other_entry", LIGHT
        ) is False
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_inactive_waiting_for_clear_turns_off_without_second_delay(mock_hass):
    """Clearing authority release is immediate after the delay was already served."""
    pir_sensor = "binary_sensor.master_bathroom_pir"
    clearing_sensor = "binary_sensor.master_bathroom_aod"
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[pir_sensor],
        clearing_sensors=[clearing_sensor],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
        off_delay=300,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(pir_sensor, STATE_OFF)
    mock_hass.states.set(clearing_sensor, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        coordinator._set_entity_state(
            LIGHT,
            entity_state,
            EntityAutomationState.WAITING_FOR_CLEAR,
            "test delay already served",
        )
        mock_hass.states.set(BEDROOM_ON, STATE_OFF)
        await coordinator._handle_activation_condition_change(
            _condition_event(BEDROOM_ON, STATE_ON, STATE_OFF)
        )
        mock_hass.services.clear()

        mock_hass.states.set(clearing_sensor, STATE_OFF)
        await coordinator._handle_presence_change(
            _sensor_event(clearing_sensor, STATE_ON, STATE_OFF)
        )

        assert entity_state["state"] == EntityAutomationState.SETTLING_OFF
        assert entity_state["off_timer"] is None
        assert [
            call
            for call in mock_hass.services.calls
            if call["service"] == "turn_off"
            and call["service_data"].get("entity_id") == LIGHT
        ]
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_inactive_waiting_for_clear_safety_timeout_does_not_reacquire(
    mock_hass,
):
    """The safety net preserves served delay while the clearing sensor is occupied."""
    clearing_sensor = "binary_sensor.master_bathroom_aod"
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        clearing_sensors=[clearing_sensor],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    mock_hass.states.set(clearing_sensor, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_OFF)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        coordinator._set_entity_state(
            LIGHT,
            entity_state,
            EntityAutomationState.WAITING_FOR_CLEAR,
            "test inactive safety timeout",
        )
        entity_state["state_entered_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=6)
        )
        mock_hass.services.clear()

        await coordinator._periodic_reconciliation(None)

        assert entity_state["state"] == EntityAutomationState.WAITING_FOR_CLEAR
        assert coordinator._ownership_manager.other_entry_wants_on(
            "other_entry", LIGHT
        ) is False
        assert _turn_on_calls(mock_hass) == []
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_periodic_reconciliation_clears_inactive_pending_light(mock_hass):
    """A missed vacancy event must still arm cleanup for an inactive entry."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    mock_hass.states.set(BEDROOM_ON, STATE_OFF)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        coordinator._set_entity_state(
            LIGHT,
            entity_state,
            EntityAutomationState.PENDING_ACTIVATION,
            "test pending vacancy",
        )
        mock_hass.services.clear()

        await coordinator._periodic_reconciliation(None)

        assert entity_state["state"] == EntityAutomationState.CLEARING
        assert entity_state["off_timer"] is not None
        assert [
            call for call in mock_hass.services.calls if call["service"] == "turn_off"
        ] == []
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_gate_close_waits_for_separate_clearing_sensor_before_off(mock_hass):
    """A demoted entry must remain recoverable while its clearing sensor is on."""
    pir_sensor = "binary_sensor.master_bathroom_pir"
    clearing_sensor = "binary_sensor.master_bathroom_aod"
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[pir_sensor],
        clearing_sensors=[clearing_sensor],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(pir_sensor, STATE_ON)
    mock_hass.states.set(clearing_sensor, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        mock_hass.states.set(pir_sensor, STATE_OFF)
        await coordinator._handle_presence_change(
            _sensor_event(pir_sensor, STATE_ON, STATE_OFF)
        )
        mock_hass.states.set(BEDROOM_ON, STATE_OFF)
        await coordinator._handle_activation_condition_change(
            _condition_event(BEDROOM_ON, STATE_ON, STATE_OFF)
        )
        mock_hass.services.clear()

        await coordinator._periodic_reconciliation(None)

        assert entity_state["state"] == EntityAutomationState.PENDING_ACTIVATION
        assert entity_state["actuation"]["status"].value != "pending"
        assert [
            call
            for call in mock_hass.services.calls
            if call["service"] == "turn_off"
        ] == []

        mock_hass.states.set(clearing_sensor, STATE_OFF)
        await coordinator._handle_presence_change(
            _sensor_event(clearing_sensor, STATE_ON, STATE_OFF)
        )

        assert entity_state["state"] == EntityAutomationState.CLEARING
        assert entity_state["off_timer"] is not None
        assert [
            call for call in mock_hass.services.calls if call["service"] == "turn_off"
        ] == []
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_canceled_settling_off_recovers_to_wait_for_clear(mock_hass):
    """A canceled off actuation must not leave a terminal settling state."""
    clearing_sensor = "binary_sensor.master_bathroom_aod"
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        clearing_sensors=[clearing_sensor],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    mock_hass.states.set(clearing_sensor, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        coordinator._set_entity_state(
            LIGHT,
            entity_state,
            EntityAutomationState.SETTLING_OFF,
            "test canceled off",
        )
        entity_state["actuation"]["status"] = ActuationStatus.CANCELED
        mock_hass.services.clear()

        await coordinator._periodic_reconciliation(None)

        assert entity_state["state"] == EntityAutomationState.WAITING_FOR_CLEAR

        mock_hass.states.set(clearing_sensor, STATE_OFF)
        await coordinator._handle_presence_change(
            _sensor_event(clearing_sensor, STATE_ON, STATE_OFF)
        )

        assert [
            call
            for call in mock_hass.services.calls
            if call["service"] == "turn_off"
            and call["service_data"].get("entity_id") == LIGHT
        ]
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_failed_settling_off_retries_without_rearming_off_delay(mock_hass):
    """A failed off command retries immediately instead of restarting vacancy delay."""
    entry = _entry(
        entry_id="first",
        room_name="First",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
        off_delay=300,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    mock_hass.states.set(BEDROOM_OFF, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        coordinator._cancel_entity_timer(entity_state)
        coordinator._intent_for_service(
            LIGHT,
            entity_state,
            CONF_PRESENCE_CLEARED_SERVICE,
            IntentReason.CLEARING,
        )
        coordinator._set_entity_state(
            LIGHT,
            entity_state,
            EntityAutomationState.SETTLING_OFF,
            "test failed off",
        )
        entity_state["actuation"].update(
            {
                "status": ActuationStatus.FAILED,
                "target_state": STATE_OFF,
                "service_key": CONF_PRESENCE_CLEARED_SERVICE,
                "attempts": 3,
            }
        )
        mock_hass.services.clear()

        await coordinator._periodic_reconciliation(None)

        assert [
            call
            for call in mock_hass.services.calls
            if call["service"] == "turn_off"
            and call["service_data"].get("entity_id") == LIGHT
        ]
        assert entity_state["state"] == EntityAutomationState.SETTLING_OFF
        assert entity_state["off_timer"] is None
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_failed_settling_off_does_not_retry_after_room_becomes_occupied(
    mock_hass,
):
    """A stale failed OFF intent must revalidate occupancy before retrying."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
        off_delay=300,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        coordinator._cancel_entity_timer(entity_state)
        coordinator._intent_for_service(
            LIGHT,
            entity_state,
            CONF_PRESENCE_CLEARED_SERVICE,
            IntentReason.CLEARING,
        )
        coordinator._set_entity_state(
            LIGHT,
            entity_state,
            EntityAutomationState.SETTLING_OFF,
            "test stale failed off",
        )
        entity_state["actuation"].update(
            {
                "status": ActuationStatus.FAILED,
                "target_state": STATE_OFF,
                "service_key": CONF_PRESENCE_CLEARED_SERVICE,
                "attempts": 3,
            }
        )
        mock_hass.services.clear()

        await coordinator._periodic_reconciliation(None)

        assert [
            call for call in mock_hass.services.calls if call["service"] == "turn_off"
        ] == []
        assert entity_state["state"] == EntityAutomationState.OCCUPIED
        assert entity_state["off_timer"] is None
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_stale_pending_generation_does_not_dedupe_identical_off_intent(
    mock_hass,
):
    """An orphaned PENDING actuation is replaced by current-generation work."""
    entry = _entry(
        entry_id="first",
        room_name="First",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    mock_hass.states.set(BEDROOM_OFF, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        coordinator._cancel_entity_timer(entity_state)
        coordinator._intent_for_service(
            LIGHT,
            entity_state,
            CONF_PRESENCE_CLEARED_SERVICE,
            IntentReason.CLEARING,
        )
        coordinator._set_entity_state(
            LIGHT,
            entity_state,
            EntityAutomationState.SETTLING_OFF,
            "test orphaned pending",
        )
        entity_state["work_generation"] = 2
        entity_state["actuation"].update(
            {
                "status": ActuationStatus.PENDING,
                "target_state": STATE_OFF,
                "service_key": CONF_PRESENCE_CLEARED_SERVICE,
                "generation": 1,
            }
        )
        mock_hass.services.clear()

        await coordinator._apply_service_intent(
            LIGHT,
            entity_state,
            CONF_PRESENCE_CLEARED_SERVICE,
            IntentReason.CLEARING,
        )

        assert [
            call
            for call in mock_hass.services.calls
            if call["service"] == "turn_off"
            and call["service_data"].get("entity_id") == LIGHT
        ]
        assert entity_state["actuation"]["generation"] == entity_state[
            "work_generation"
        ]
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_taskless_pending_does_not_dedupe_identical_off_intent(mock_hass):
    """A current-generation PENDING without a task is not live work."""
    entry = _entry(
        entry_id="first",
        room_name="First",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    mock_hass.states.set(BEDROOM_OFF, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    coordinator._presence_sensors = {LOCAL_SENSOR}
    coordinator._clearing_sensors = {LOCAL_SENSOR}
    coordinator._activation_conditions = {BEDROOM_OFF}
    entity_state = coordinator._entity_states[LIGHT]
    coordinator._intent_for_service(
        LIGHT,
        entity_state,
        CONF_PRESENCE_CLEARED_SERVICE,
        IntentReason.CLEARING,
    )
    coordinator._set_entity_state(
        LIGHT,
        entity_state,
        EntityAutomationState.SETTLING_OFF,
        "test taskless pending",
    )
    entity_state["work_generation"] = 3
    entity_state["actuation"].update(
        {
            "status": ActuationStatus.PENDING,
            "target_state": STATE_OFF,
            "service_key": CONF_PRESENCE_CLEARED_SERVICE,
            "generation": 3,
            "timer": None,
            "dispatching": False,
        }
    )
    mock_hass.services.clear()

    await coordinator._apply_service_intent(
        LIGHT,
        entity_state,
        CONF_PRESENCE_CLEARED_SERVICE,
        IntentReason.CLEARING,
    )

    assert [
        call
        for call in mock_hass.services.calls
        if call["service"] == "turn_off"
        and call["service_data"].get("entity_id") == LIGHT
    ]


@pytest.mark.asyncio
async def test_service_call_error_schedules_retry_instead_of_wedging_pending(
    mock_hass,
):
    """A raised HA service call remains inside the actuator retry lifecycle."""
    entry = _entry(
        entry_id="first",
        room_name="First",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    mock_hass.states.set(BEDROOM_OFF, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    coordinator._presence_sensors = {LOCAL_SENSOR}
    coordinator._clearing_sensors = {LOCAL_SENSOR}
    coordinator._activation_conditions = {BEDROOM_OFF}
    entity_state = coordinator._entity_states[LIGHT]

    async def raise_service_error(*_args, **_kwargs):
        raise RuntimeError("Zigbee command failed")

    mock_hass.services.async_call = raise_service_error

    await coordinator._apply_service_intent(
        LIGHT,
        entity_state,
        CONF_PRESENCE_CLEARED_SERVICE,
        IntentReason.CLEARING,
    )

    assert entity_state["actuation"]["status"] == ActuationStatus.PENDING
    assert entity_state["actuation"]["timer"] is not None
    assert entity_state["actuation"]["dispatching"] is False
    assert "Zigbee command failed" in entity_state["actuation"]["last_error"]
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_periodic_reconciliation_recovers_taskless_pending_settling_off(
    mock_hass,
):
    """The safety net re-dispatches a taskless current-generation actuation."""
    entry = _entry(
        entry_id="first",
        room_name="First",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    mock_hass.states.set(BEDROOM_OFF, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    coordinator._presence_sensors = {LOCAL_SENSOR}
    coordinator._clearing_sensors = {LOCAL_SENSOR}
    coordinator._activation_conditions = {BEDROOM_OFF}
    entity_state = coordinator._entity_states[LIGHT]
    coordinator._intent_for_service(
        LIGHT,
        entity_state,
        CONF_PRESENCE_CLEARED_SERVICE,
        IntentReason.CLEARING,
    )
    coordinator._set_entity_state(
        LIGHT,
        entity_state,
        EntityAutomationState.SETTLING_OFF,
        "test taskless safety net",
    )
    entity_state["work_generation"] = 5
    entity_state["actuation"].update(
        {
            "status": ActuationStatus.PENDING,
            "target_state": STATE_OFF,
            "service_key": CONF_PRESENCE_CLEARED_SERVICE,
            "generation": 5,
            "timer": None,
            "dispatching": False,
        }
    )
    mock_hass.services.clear()

    await coordinator._periodic_reconciliation(None)

    assert [
        call
        for call in mock_hass.services.calls
        if call["service"] == "turn_off"
        and call["service_data"].get("entity_id") == LIGHT
    ]
    assert coordinator._actuation_is_currently_pending(entity_state) is True
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_presence_lock_service_error_does_not_escape_or_wedge(mock_hass):
    """Strict Presence Lock failures schedule retry without aborting handling."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    coordinator._presence_sensors = {LOCAL_SENSOR}
    coordinator._clearing_sensors = {LOCAL_SENSOR}
    coordinator._activation_conditions = {BEDROOM_ON}
    entity_state = coordinator._entity_states[LIGHT]

    async def raise_service_error(*_args, **_kwargs):
        raise RuntimeError("Zigbee command failed")

    mock_hass.services.async_call = raise_service_error

    await coordinator._handle_external_action(LIGHT, "turn_off")

    assert entity_state["actuation"]["status"] == ActuationStatus.PENDING
    assert entity_state["actuation"]["timer"] is not None
    assert entity_state["actuation"]["dispatching"] is False
    assert "Zigbee command failed" in entity_state["actuation"]["last_error"]
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_superseded_dispatch_cannot_clear_new_dispatch_token(mock_hass):
    """An older service completion cannot make a newer dispatch look taskless."""
    entry = _entry(
        entry_id="first",
        room_name="First",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    mock_hass.states.set(BEDROOM_OFF, STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    coordinator._presence_sensors = {LOCAL_SENSOR}
    coordinator._clearing_sensors = {LOCAL_SENSOR}
    coordinator._activation_conditions = {BEDROOM_OFF}
    entity_state = coordinator._entity_states[LIGHT]

    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()

    async def overlapping_call(
        domain,
        service,
        service_data=None,
        blocking=False,
        context=None,
    ):
        mock_hass.services.calls.append(
            {
                "domain": domain,
                "service": service,
                "service_data": service_data or {},
                "blocking": blocking,
                "context": context,
            }
        )
        if service == "turn_off":
            first_started.set()
            await release_first.wait()
        elif service == "turn_on":
            second_started.set()
            await release_second.wait()

    mock_hass.services.async_call = overlapping_call
    first_task = asyncio.create_task(
        coordinator._apply_service_intent(
            LIGHT,
            entity_state,
            CONF_PRESENCE_CLEARED_SERVICE,
            IntentReason.CLEARING,
        )
    )
    await asyncio.wait_for(first_started.wait(), timeout=1)

    mock_hass.states.set(LIGHT, STATE_OFF)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    second_task = asyncio.create_task(
        coordinator._handle_presence_change(
            _sensor_event(LOCAL_SENSOR, STATE_OFF, STATE_ON)
        )
    )
    await asyncio.wait_for(second_started.wait(), timeout=1)

    release_first.set()
    await first_task

    assert coordinator._actuation_is_currently_pending(entity_state) is True
    calls_before_reconcile = len(mock_hass.services.calls)
    await coordinator._periodic_reconciliation(None)
    assert len(mock_hass.services.calls) == calls_before_reconcile

    release_second.set()
    await second_task
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_resume_cancels_pending_strict_correction_before_reconcile(mock_hass):
    """Resume cannot orphan an in-flight strict Presence Lock correction."""
    entry = _entry(
        entry_id="presence_lock",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        coordinator._cancel_entity_timer(entity_state)
        coordinator._set_entity_state(
            LIGHT,
            entity_state,
            EntityAutomationState.PAUSED,
            "test pending strict correction",
        )
        entity_state["work_generation"] = 4
        entity_state["actuation"].update(
            {
                "status": ActuationStatus.PENDING,
                "target_state": STATE_OFF,
                "service_key": CONF_PRESENCE_CLEARED_SERVICE,
                "generation": 4,
            }
        )

        coordinator.set_automation_paused(
            LIGHT,
            False,
            reason="test resume",
            source="service",
        )

        assert entity_state["state"] == EntityAutomationState.IDLE
        assert entity_state["actuation"]["status"] == ActuationStatus.CANCELED
        await coordinator._reconcile_entity(LIGHT, entity_state)
        assert entity_state["state"] == EntityAutomationState.CLEARING
        assert entity_state["off_timer"] is not None
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_complementary_entries_honor_homekit_off_during_handoff(
    mock_hass, tmp_path
):
    """The bedroom-off handoff must not re-arm the shared bathroom light."""
    primary_entry = _entry(
        entry_id="primary",
        room_name="Master Bathroom",
        presence_sensors=[LOCAL_SENSOR, BEDROOM_SENSOR],
        activation_condition=BEDROOM_ON,
        presence_lock=True,
    )
    fallback_entry = _entry(
        entry_id="fallback",
        room_name="Master Bathroom Bedroom Off",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_ON, STATE_ON)
    mock_hass.states.set(BEDROOM_OFF, STATE_OFF)
    _configure_storage(mock_hass, tmp_path)

    primary = PresenceBasedLightingCoordinator(mock_hass, primary_entry)
    fallback = PresenceBasedLightingCoordinator(mock_hass, fallback_entry)
    mock_hass.data[DOMAIN] = {
        primary_entry.entry_id: primary,
        fallback_entry.entry_id: fallback,
    }
    await primary.async_start()
    await fallback.async_start()

    try:
        mock_hass.states.set(BEDROOM_ON, STATE_OFF)
        await primary._handle_activation_condition_change(
            _condition_event(BEDROOM_ON, STATE_ON, STATE_OFF)
        )
        mock_hass.states.set(BEDROOM_OFF, STATE_ON)
        await fallback._handle_activation_condition_change(
            _condition_event(BEDROOM_OFF, STATE_OFF, STATE_ON)
        )

        mock_hass.services.clear()
        homekit_context = MockContext("homekit")
        service_event = _service_event("turn_off", homekit_context)
        await primary._handle_service_call(service_event)
        await fallback._handle_service_call(service_event)

        mock_hass.states.set(LIGHT, STATE_OFF, context=homekit_context)
        state_event = _state_event(STATE_ON, STATE_OFF, homekit_context)
        await primary._handle_controlled_entity_change(state_event)
        await fallback._handle_controlled_entity_change(state_event)

        assert _turn_on_calls(mock_hass) == []
        # The gate-closed primary now also honours the entity-scoped override, so
        # it cannot re-arm the shared light when the activation gate flips back.
        assert primary._entity_states[LIGHT]["state"] in {
            EntityAutomationState.PENDING_ACTIVATION,
            EntityAutomationState.IDLE,
            EntityAutomationState.PAUSED,
        }
        assert primary._entity_states[LIGHT]["intent"]["desired"].value != "detected"
        assert primary._ownership_manager.other_entry_wants_on(
            fallback_entry.entry_id, LIGHT
        ) is False
        assert fallback.get_automation_paused(LIGHT) is True
    finally:
        primary.async_stop()
        fallback.async_stop()


@pytest.mark.asyncio
async def test_sibling_service_context_does_not_resume_manual_pause(mock_hass):
    """A PBL command from another entry is internal, not a manual turn-on."""
    first_entry = _entry(
        entry_id="first",
        room_name="First",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    second_entry = _entry(
        entry_id="second",
        room_name="Second",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    mock_hass.states.set(LIGHT, STATE_OFF)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_OFF, STATE_ON)

    first = PresenceBasedLightingCoordinator(mock_hass, first_entry)
    second = PresenceBasedLightingCoordinator(mock_hass, second_entry)
    mock_hass.data[DOMAIN] = {
        first_entry.entry_id: first,
        second_entry.entry_id: second,
    }
    first._presence_sensors = {LOCAL_SENSOR}
    first._clearing_sensors = {LOCAL_SENSOR}
    first._activation_conditions = {BEDROOM_OFF}
    second._presence_sensors = {LOCAL_SENSOR}
    second._clearing_sensors = {LOCAL_SENSOR}
    second._activation_conditions = {BEDROOM_OFF}
    first._set_entity_state(
        LIGHT,
        first._entity_states[LIGHT],
        EntityAutomationState.OCCUPIED,
        "test owner",
    )
    second.set_automation_paused(LIGHT, True)

    await first._apply_service_intent(
        LIGHT,
        first._entity_states[LIGHT],
        CONF_PRESENCE_DETECTED_SERVICE,
        IntentReason.PRESENCE,
    )
    sibling_context = mock_hass.services.calls[-1]["context"]
    sibling_context.parent_id = None

    await second._handle_service_call(_service_event("turn_on", sibling_context))

    assert second.get_automation_paused(LIGHT) is True


@pytest.mark.asyncio
async def test_opposite_child_service_context_is_external(mock_hass):
    """A child command in the opposite direction must not inherit trust."""
    entry = _entry(
        entry_id="first",
        room_name="First",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    mock_hass.states.set(LIGHT, STATE_OFF)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_OFF, STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    coordinator._presence_sensors = {LOCAL_SENSOR}
    coordinator._clearing_sensors = {LOCAL_SENSOR}
    coordinator._activation_conditions = {BEDROOM_OFF}
    coordinator._set_entity_state(
        LIGHT,
        coordinator._entity_states[LIGHT],
        EntityAutomationState.OCCUPIED,
        "test owner",
    )
    await coordinator._apply_service_intent(
        LIGHT,
        coordinator._entity_states[LIGHT],
        CONF_PRESENCE_DETECTED_SERVICE,
        IntentReason.PRESENCE,
    )
    own_context = mock_hass.services.calls[-1]["context"]
    own_context.parent_id = None
    child_context = MockContext("child", parent_id=own_context.id)

    await coordinator._handle_service_call(
        _service_event("turn_off", child_context)
    )

    assert coordinator.get_automation_paused(LIGHT) is True


@pytest.mark.asyncio
async def test_sibling_child_service_context_does_not_resume_manual_pause(mock_hass):
    """A child service call from a sibling command remains internal."""
    first_entry = _entry(
        entry_id="first",
        room_name="First",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    second_entry = _entry(
        entry_id="second",
        room_name="Second",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    mock_hass.states.set(LIGHT, STATE_OFF)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_OFF, STATE_ON)

    first = PresenceBasedLightingCoordinator(mock_hass, first_entry)
    second = PresenceBasedLightingCoordinator(mock_hass, second_entry)
    first._presence_sensors = {LOCAL_SENSOR}
    first._clearing_sensors = {LOCAL_SENSOR}
    first._activation_conditions = {BEDROOM_OFF}
    second._presence_sensors = {LOCAL_SENSOR}
    second._clearing_sensors = {LOCAL_SENSOR}
    second._activation_conditions = {BEDROOM_OFF}
    first._set_entity_state(
        LIGHT,
        first._entity_states[LIGHT],
        EntityAutomationState.OCCUPIED,
        "test owner",
    )
    second.set_automation_paused(LIGHT, True)
    await first._apply_service_intent(
        LIGHT,
        first._entity_states[LIGHT],
        CONF_PRESENCE_DETECTED_SERVICE,
        IntentReason.PRESENCE,
    )
    sibling_context = mock_hass.services.calls[-1]["context"]
    sibling_context.parent_id = None
    child_context = MockContext("child", parent_id=sibling_context.id)

    await second._handle_service_call(_service_event("turn_on", child_context))

    assert second.get_automation_paused(LIGHT) is True


@pytest.mark.asyncio
async def test_gate_closed_vacancy_honors_off_delay(mock_hass):
    """An inactive entry keeps the configured vacancy delay before clearing."""
    entry = _entry(
        entry_id="first",
        room_name="First",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
        off_delay=300,
    )
    mock_hass.states.set(LIGHT, STATE_ON)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_OFF, STATE_OFF)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        entity_state = coordinator._entity_states[LIGHT]
        assert entity_state["state"] == EntityAutomationState.PENDING_ACTIVATION
        mock_hass.services.clear()

        mock_hass.states.set(LOCAL_SENSOR, STATE_OFF)
        await coordinator._handle_presence_change(
            _sensor_event(LOCAL_SENSOR, STATE_ON, STATE_OFF)
        )

        assert entity_state["state"] == EntityAutomationState.CLEARING
        assert entity_state["off_timer"] is not None
        assert [
            call for call in mock_hass.services.calls if call["service"] == "turn_off"
        ] == []
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_sibling_state_context_does_not_resume_manual_pause(mock_hass):
    """A sibling PBL state result must not be mistaken for human control."""
    first_entry = _entry(
        entry_id="first",
        room_name="First",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    second_entry = _entry(
        entry_id="second",
        room_name="Second",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    mock_hass.states.set(LIGHT, STATE_OFF)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_OFF, STATE_ON)

    first = PresenceBasedLightingCoordinator(mock_hass, first_entry)
    second = PresenceBasedLightingCoordinator(mock_hass, second_entry)
    mock_hass.data[DOMAIN] = {
        first_entry.entry_id: first,
        second_entry.entry_id: second,
    }
    first._presence_sensors = {LOCAL_SENSOR}
    first._clearing_sensors = {LOCAL_SENSOR}
    first._activation_conditions = {BEDROOM_OFF}
    second._presence_sensors = {LOCAL_SENSOR}
    second._clearing_sensors = {LOCAL_SENSOR}
    second._activation_conditions = {BEDROOM_OFF}
    first._set_entity_state(
        LIGHT,
        first._entity_states[LIGHT],
        EntityAutomationState.OCCUPIED,
        "test owner",
    )
    second.set_automation_paused(LIGHT, True)

    await first._apply_service_intent(
        LIGHT,
        first._entity_states[LIGHT],
        CONF_PRESENCE_DETECTED_SERVICE,
        IntentReason.PRESENCE,
    )
    sibling_context = mock_hass.services.calls[-1]["context"]
    sibling_context.parent_id = None

    mock_hass.states.set(LIGHT, STATE_ON, context=sibling_context)
    await second._handle_controlled_entity_change(
        _state_event(STATE_OFF, STATE_ON, sibling_context)
    )

    assert second.get_automation_paused(LIGHT) is True


@pytest.mark.asyncio
async def test_sibling_child_state_context_does_not_resume_manual_pause(mock_hass):
    """A downstream child context remains attributable to the sibling command."""
    first_entry = _entry(
        entry_id="first",
        room_name="First",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    second_entry = _entry(
        entry_id="second",
        room_name="Second",
        presence_sensors=[LOCAL_SENSOR],
        activation_condition=BEDROOM_OFF,
        presence_lock=False,
    )
    mock_hass.states.set(LIGHT, STATE_OFF)
    mock_hass.states.set(LOCAL_SENSOR, STATE_ON)
    mock_hass.states.set(BEDROOM_OFF, STATE_ON)

    first = PresenceBasedLightingCoordinator(mock_hass, first_entry)
    second = PresenceBasedLightingCoordinator(mock_hass, second_entry)
    first._presence_sensors = {LOCAL_SENSOR}
    first._clearing_sensors = {LOCAL_SENSOR}
    first._activation_conditions = {BEDROOM_OFF}
    second._presence_sensors = {LOCAL_SENSOR}
    second._clearing_sensors = {LOCAL_SENSOR}
    second._activation_conditions = {BEDROOM_OFF}
    first._set_entity_state(
        LIGHT,
        first._entity_states[LIGHT],
        EntityAutomationState.OCCUPIED,
        "test owner",
    )
    second.set_automation_paused(LIGHT, True)

    await first._apply_service_intent(
        LIGHT,
        first._entity_states[LIGHT],
        CONF_PRESENCE_DETECTED_SERVICE,
        IntentReason.PRESENCE,
    )
    sibling_context = mock_hass.services.calls[-1]["context"]
    sibling_context.parent_id = None
    child_context = MockContext("child", parent_id=sibling_context.id)

    mock_hass.states.set(LIGHT, STATE_ON, context=child_context)
    await second._handle_controlled_entity_change(
        _state_event(STATE_OFF, STATE_ON, child_context)
    )

    assert second.get_automation_paused(LIGHT) is True
