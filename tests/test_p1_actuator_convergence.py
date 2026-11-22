"""Closed-loop actuator convergence tests."""
from datetime import timedelta

import pytest
from custom_components.presence_based_lighting import _ACTUATION_MAX_ATTEMPTS
from custom_components.presence_based_lighting import ActuationStatus
from custom_components.presence_based_lighting import EntityAutomationState
from custom_components.presence_based_lighting import IntentReason
from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.const import (
    CONF_PRESENCE_CLEARED_SERVICE,
)
from custom_components.presence_based_lighting.const import (
    CONF_PRESENCE_DETECTED_SERVICE,
)
from custom_components.presence_based_lighting.const import CONF_RLC_TRACKING_ENTITY
from custom_components.presence_based_lighting.real_last_changed import (
    ATTR_PREVIOUS_VALID_STATE,
)
from homeassistant.const import STATE_OFF
from homeassistant.const import STATE_ON
from homeassistant.util import dt as dt_util
from tests.conftest import assert_service_called
from tests.conftest import MockContext
from tests.conftest import MockState
from tests.conftest import setup_entity_states


def _event(mock_hass, entity_id, old_state, new_state, context, *, attributes=None):
    mock_hass.states.set(entity_id, new_state, context=context, attributes=attributes)
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": MockState(entity_id, old_state),
                "new_state": MockState(
                    entity_id, new_state, context=context, attributes=attributes
                ),
            }
        },
    )()


def _make_coordinator(mock_hass, mock_config_entry):
    coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
    coordinator._presence_sensors = {"binary_sensor.living_room_motion"}
    coordinator._clearing_sensors = {"binary_sensor.living_room_motion"}
    coordinator._activation_conditions = set()
    return coordinator


async def _start_cleared_actuation(coordinator):
    entity_state = coordinator._entity_states["light.living_room"]
    entity_state["state"] = EntityAutomationState.OCCUPIED
    await coordinator._execute_entity_off_timer("light.living_room", entity_state, 0)
    timer = entity_state["actuation"].get("timer")
    if timer is not None:
        timer.cancel()
        entity_state["actuation"]["timer"] = None
    return entity_state


async def _rebound_pending_off_then_retarget_detected(
    mock_hass,
    coordinator,
    rebound_shape,
):
    """Create the recorder race, then retarget to the fresh presence edge."""
    hue_group = {"is_hue_group": True}
    mock_hass.states.set("light.living_room", STATE_ON, attributes=hue_group)
    mock_hass.states.set(
        "sensor.living_room_lights",
        "09:43:36",
        attributes={ATTR_PREVIOUS_VALID_STATE: STATE_ON},
    )
    entity_state = coordinator._entity_states["light.living_room"]
    entity_state["config"][CONF_RLC_TRACKING_ENTITY] = "sensor.living_room_lights"
    entity_state["last_effective_state"] = STATE_ON
    coordinator._rlc_to_entity["sensor.living_room_lights"] = "light.living_room"
    entity_state = await _start_cleared_actuation(coordinator)
    own_context = mock_hass.services.calls[-1]["context"]
    if rebound_shape == "state":
        await coordinator._handle_controlled_entity_change(
            _event(
                mock_hass,
                "light.living_room",
                STATE_ON,
                STATE_OFF,
                own_context,
                attributes=hue_group,
            )
        )
        old_rebound_state = STATE_OFF
    else:
        old_rebound_state = STATE_ON
    await coordinator._handle_controlled_entity_change(
        _event(
            mock_hass,
            "light.living_room",
            old_rebound_state,
            STATE_ON,
            own_context,
            attributes=hue_group,
        )
    )

    await coordinator._handle_presence_change(
        _event(
            mock_hass,
            "binary_sensor.living_room_motion",
            STATE_OFF,
            STATE_ON,
            MockContext(id="fresh-presence"),
        )
    )
    assert entity_state["actuation"]["target_state"] == STATE_ON
    assert entity_state["actuation"]["status"] == ActuationStatus.CONFIRMED
    assert entity_state["actuation"]["attempts"] == 0
    return entity_state


async def _deliver_late_hue_off(mock_hass, coordinator, context):
    """The raw callback sees stale RLC state; the RLC callback sees the OFF."""
    await coordinator._handle_controlled_entity_change(
        _event(
            mock_hass,
            "light.living_room",
            STATE_ON,
            STATE_OFF,
            context,
            attributes={"is_hue_group": True},
        )
    )
    event = _event(
        mock_hass,
        "sensor.living_room_lights",
        "09:43:36",
        "09:43:55",
        context,
        attributes={ATTR_PREVIOUS_VALID_STATE: STATE_OFF},
    )
    event.data["old_state"].attributes[ATTR_PREVIOUS_VALID_STATE] = STATE_ON
    await coordinator._handle_rlc_tracking_change(event)


@pytest.mark.asyncio
async def test_cleared_intent_settles_before_idle(mock_hass, mock_config_entry):
    """Timer expiry enters settling-off and only reaches IDLE after confirmation."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)

    entity_state = await _start_cleared_actuation(coordinator)

    assert entity_state["state"] == EntityAutomationState.SETTLING_OFF
    assert entity_state["actuation"]["status"] == ActuationStatus.PENDING
    assert_service_called(mock_hass, "light", "turn_off", "light.living_room")

    context = mock_hass.services.calls[-1]["context"]
    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_ON, STATE_OFF, context)
    )
    await coordinator._execute_actuation_confirmation_timer(
        "light.living_room", entity_state, 0
    )

    assert entity_state["state"] == EntityAutomationState.IDLE
    assert entity_state["actuation"]["status"] == ActuationStatus.CONFIRMED


@pytest.mark.asyncio
async def test_own_context_rebound_retries_off(mock_hass, mock_config_entry):
    """Own-context on rebound is command feedback, not manual control."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)

    entity_state = await _start_cleared_actuation(coordinator)
    first_context = mock_hass.services.calls[-1]["context"]

    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_ON, STATE_OFF, first_context)
    )
    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_OFF, STATE_ON, first_context)
    )
    timer = entity_state["actuation"].get("timer")
    if timer is not None:
        timer.cancel()
        entity_state["actuation"]["timer"] = None

    await coordinator._execute_actuation_retry_timer(
        "light.living_room", entity_state, 0
    )

    turn_off_calls = [
        call for call in mock_hass.services.calls if call["service"] == "turn_off"
    ]
    assert len(turn_off_calls) == 2
    assert entity_state["state"] == EntityAutomationState.SETTLING_OFF
    assert coordinator.get_automation_paused("light.living_room") is False


@pytest.mark.asyncio
async def test_rebound_does_not_retry_when_presence_returns(
    mock_hass, mock_config_entry
):
    """Off convergence is canceled as soon as clearing sensors are no longer clear."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)

    entity_state = await _start_cleared_actuation(coordinator)
    first_context = mock_hass.services.calls[-1]["context"]
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)

    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_OFF, STATE_ON, first_context)
    )

    turn_off_calls = [
        call for call in mock_hass.services.calls if call["service"] == "turn_off"
    ]
    assert len(turn_off_calls) == 1
    assert entity_state["actuation"]["status"] == ActuationStatus.CANCELED


@pytest.mark.asyncio
@pytest.mark.parametrize("rebound_shape", ("state", "attribute_only"))
@pytest.mark.parametrize("occupied", (True, False))
async def test_late_unattributed_hue_off_preserves_current_presence_authority(
    mock_hass,
    mock_config_entry,
    rebound_shape,
    occupied,
):
    """A delayed completion of a superseded owned OFF must not pause the room."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)
    entity_state = await _rebound_pending_off_then_retarget_detected(
        mock_hass,
        coordinator,
        rebound_shape,
    )

    if not occupied:
        await coordinator._handle_presence_change(
            _event(
                mock_hass,
                "binary_sensor.living_room_motion",
                STATE_ON,
                STATE_OFF,
                MockContext(id="vacancy"),
            )
        )
        entity_state["off_timer"].cancel()
    mock_hass.services.clear()

    await _deliver_late_hue_off(
        mock_hass, coordinator, MockContext(id="late-hue-report")
    )

    assert coordinator.get_automation_paused("light.living_room") is False
    assert entity_state["state"] == (
        EntityAutomationState.OCCUPIED if occupied else EntityAutomationState.CLEARING
    )
    assert (
        coordinator.get_entity_control_state("light.living_room")[
            "late_owned_off_echo_count"
        ]
        == 1
    )
    assert [
        call["service"]
        for call in mock_hass.services.calls
        if call["service_data"].get("entity_id") == "light.living_room"
    ] == (["turn_on"] if occupied else [])


@pytest.mark.asyncio
async def test_late_off_after_hue_echo_window_remains_an_external_change(
    mock_hass,
    mock_config_entry,
    monkeypatch,
):
    """An aged-out rebound cannot mask a later unattributed physical OFF."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)
    entity_state = await _rebound_pending_off_then_retarget_detected(
        mock_hass,
        coordinator,
        "attribute_only",
    )
    later = dt_util.utcnow() + timedelta(seconds=61)
    monkeypatch.setattr(dt_util, "utcnow", lambda: later)
    mock_hass.services.clear()

    await _deliver_late_hue_off(
        mock_hass, coordinator, MockContext(id="late-hue-report-expired")
    )

    assert coordinator.get_automation_paused("light.living_room") is True
    assert entity_state["late_owned_off_echo_count"] == 0
    assert mock_hass.services.calls == []


@pytest.mark.asyncio
async def test_explicit_manual_off_is_not_swallowed_by_pending_echo(
    mock_hass,
    mock_config_entry,
):
    """An attributed user OFF keeps normal manual-pause precedence."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)
    entity_state = await _rebound_pending_off_then_retarget_detected(
        mock_hass,
        coordinator,
        "attribute_only",
    )
    manual_context = MockContext(
        id="manual-off",
        user_id="user-1",
    )

    await coordinator._handle_service_call(
        type(
            "Event",
            (),
            {
                "data": {
                    "domain": "light",
                    "service": "turn_off",
                    "service_data": {"entity_id": "light.living_room"},
                },
                "context": manual_context,
            },
        )()
    )
    await _deliver_late_hue_off(mock_hass, coordinator, manual_context)

    assert entity_state["pending_off_echo"] is None
    assert entity_state["late_owned_off_echo_count"] == 0
    assert coordinator.get_automation_paused("light.living_room") is True


@pytest.mark.asyncio
async def test_stale_off_timer_after_pause_resume_does_not_turn_off(
    mock_hass, mock_config_entry
):
    """A canceled off timer must not fire after pause/resume makes automation active again."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)
    entity_state = coordinator._entity_states["light.living_room"]
    entity_state["state"] = EntityAutomationState.OCCUPIED

    await coordinator._start_entity_off_timer("light.living_room", entity_state)
    stale_generation = entity_state["work_generation"]
    if entity_state["off_timer"] is not None:
        entity_state["off_timer"].cancel()
        entity_state["off_timer"] = None

    coordinator.set_automation_paused("light.living_room", True)
    coordinator.set_automation_paused("light.living_room", False)
    mock_hass.services.clear()

    await coordinator._execute_entity_off_timer(
        "light.living_room",
        entity_state,
        0,
        stale_generation,
    )

    turn_off_calls = [
        call for call in mock_hass.services.calls if call["service"] == "turn_off"
    ]
    assert turn_off_calls == []


@pytest.mark.asyncio
async def test_stale_actuation_retry_after_generation_change_does_not_call_service(
    mock_hass, mock_config_entry
):
    """A retry captured by an older generation must not act on newer work."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)

    entity_state = await _start_cleared_actuation(coordinator)
    stale_generation = entity_state["actuation"]["generation"]
    current_generation = coordinator._bump_entity_work_generation(
        entity_state, "test newer work"
    )
    entity_state["actuation"]["generation"] = current_generation
    mock_hass.services.clear()

    await coordinator._execute_actuation_retry_timer(
        "light.living_room",
        entity_state,
        0,
        stale_generation,
    )

    turn_off_calls = [
        call for call in mock_hass.services.calls if call["service"] == "turn_off"
    ]
    assert turn_off_calls == []


@pytest.mark.asyncio
async def test_failed_convergence_visible_after_max_attempts(
    mock_hass, mock_config_entry
):
    """A still-on entity after max attempts is marked failed instead of hidden."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)

    entity_state = await _start_cleared_actuation(coordinator)
    entity_state["actuation"]["attempts"] = _ACTUATION_MAX_ATTEMPTS

    await coordinator._retry_or_fail_entity_actuation(
        "light.living_room", entity_state, STATE_ON
    )

    assert entity_state["actuation"]["status"] == ActuationStatus.FAILED
    assert "did not converge" in entity_state["actuation"]["last_error"]


@pytest.mark.asyncio
async def test_retry_delay_uses_progressive_backoff(mock_hass, mock_config_entry):
    """Retries should back off after the immediate first actuation attempt."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)
    entity_state = await _start_cleared_actuation(coordinator)
    captured = []

    def capture_timer(entity_id, state, delay, retry):
        captured.append((entity_id, delay, retry))

    coordinator._schedule_actuation_timer = capture_timer

    entity_state["actuation"]["attempts"] = 1
    await coordinator._retry_or_fail_entity_actuation(
        "light.living_room", entity_state, STATE_ON
    )
    entity_state["actuation"]["attempts"] = 2
    await coordinator._retry_or_fail_entity_actuation(
        "light.living_room", entity_state, STATE_ON
    )

    assert captured[0][2] is True
    assert 3.0 <= captured[0][1] <= 3.75
    assert captured[1][2] is True
    assert 7.0 <= captured[1][1] <= 7.75
    assert entity_state["actuation"]["next_retry_delay"] == captured[-1][1]


@pytest.mark.asyncio
async def test_late_target_after_failed_off_is_confirmed_not_manual(
    mock_hass, mock_config_entry
):
    """A slow light that eventually reaches PBL's off target must not pause automation."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)

    entity_state = await _start_cleared_actuation(coordinator)
    entity_state["actuation"]["attempts"] = _ACTUATION_MAX_ATTEMPTS
    await coordinator._retry_or_fail_entity_actuation(
        "light.living_room", entity_state, STATE_ON
    )

    external_context = type("Ctx", (), {"id": "late_external", "parent_id": None})()
    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_ON, STATE_OFF, external_context)
    )

    assert entity_state["state"] == EntityAutomationState.IDLE
    assert entity_state["actuation"]["status"] == ActuationStatus.CONFIRMED
    assert entity_state["actuation"]["last_error"] is None
    assert coordinator.get_automation_paused("light.living_room") is False


async def _confirm_off_with_own_echo(mock_hass, coordinator):
    """Confirm PBL's off, then replay a Hue group's stale own-context on echo."""
    entity_state = await _start_cleared_actuation(coordinator)
    context = mock_hass.services.calls[-1]["context"]
    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_ON, STATE_OFF, context)
    )
    await coordinator._execute_actuation_confirmation_timer(
        "light.living_room", entity_state, 0
    )
    assert entity_state["actuation"]["status"] == ActuationStatus.CONFIRMED
    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_OFF, STATE_ON, context)
    )
    return entity_state


@pytest.mark.asyncio
async def test_late_correction_of_own_echo_after_confirmed_off_is_not_manual(
    mock_hass, mock_config_entry
):
    """A context-less off correcting PBL's own stale on echo must not pause."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)
    entity_state = await _confirm_off_with_own_echo(mock_hass, coordinator)
    calls_before = len(mock_hass.services.calls)

    bridge_context = type("Ctx", (), {"id": "hue_correction", "parent_id": None})()
    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_ON, STATE_OFF, bridge_context)
    )

    assert coordinator.get_automation_paused("light.living_room") is False
    assert entity_state["state"] == EntityAutomationState.IDLE
    assert entity_state["actuation"]["status"] == ActuationStatus.CONFIRMED
    assert entity_state["own_echo"] is None
    assert len(mock_hass.services.calls) == calls_before


@pytest.mark.asyncio
async def test_own_echo_correction_reasserts_detected_intent(
    mock_hass, mock_config_entry
):
    """A stale on echo must not hide a dark occupied room or pause on correction."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)
    entity_state = await _confirm_off_with_own_echo(mock_hass, coordinator)

    motion_context = type("Ctx", (), {"id": "motion", "parent_id": None})()
    await coordinator._handle_presence_change(
        _event(
            mock_hass,
            "binary_sensor.living_room_motion",
            STATE_OFF,
            STATE_ON,
            motion_context,
        )
    )
    assert entity_state["actuation"]["target_state"] == STATE_ON
    assert entity_state["actuation"]["status"] == ActuationStatus.CONFIRMED
    calls_before = len(mock_hass.services.calls)

    bridge_context = type("Ctx", (), {"id": "hue_correction", "parent_id": None})()
    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_ON, STATE_OFF, bridge_context)
    )

    assert coordinator.get_automation_paused("light.living_room") is False
    assert entity_state["actuation"]["target_state"] == STATE_ON
    assert entity_state["actuation"]["status"] == ActuationStatus.PENDING
    turn_on_calls = [
        call
        for call in mock_hass.services.calls[calls_before:]
        if call["service"] == "turn_on"
    ]
    assert len(turn_on_calls) == 1


@pytest.mark.asyncio
async def test_off_after_own_echo_window_still_pauses(mock_hass, mock_config_entry):
    """Manual off semantics are unchanged once the echo correction window passes."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)
    entity_state = await _confirm_off_with_own_echo(mock_hass, coordinator)
    entity_state["own_echo"]["at"] -= timedelta(seconds=61)

    manual_context = type("Ctx", (), {"id": "manual", "parent_id": None})()
    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_ON, STATE_OFF, manual_context)
    )

    assert coordinator.get_automation_paused("light.living_room") is True


@pytest.mark.asyncio
async def test_own_echo_marker_is_consumed_by_external_change(
    mock_hass, mock_config_entry
):
    """Only the first external change after an echo can be its correction."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)
    entity_state = await _confirm_off_with_own_echo(mock_hass, coordinator)

    bridge_context = type("Ctx", (), {"id": "hue_correction", "parent_id": None})()
    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_ON, STATE_OFF, bridge_context)
    )
    manual_on = type("Ctx", (), {"id": "manual_on", "parent_id": None})()
    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_OFF, STATE_ON, manual_on)
    )
    manual_off = type("Ctx", (), {"id": "manual_off", "parent_id": None})()
    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_ON, STATE_OFF, manual_off)
    )

    assert entity_state["own_echo"] is None
    assert coordinator.get_automation_paused("light.living_room") is True


@pytest.mark.asyncio
async def test_periodic_reconciliation_starts_missing_off_actuation(
    mock_hass, mock_config_entry
):
    """The audit loop starts actuation when IDLE disagrees with actual state."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)
    entity_state = coordinator._entity_states["light.living_room"]
    entity_state["state"] = EntityAutomationState.IDLE

    await coordinator._periodic_reconciliation(None)

    assert entity_state["state"] == EntityAutomationState.SETTLING_OFF
    assert entity_state["actuation"]["service_key"] == CONF_PRESENCE_CLEARED_SERVICE
    assert_service_called(mock_hass, "light", "turn_off", "light.living_room")


@pytest.mark.asyncio
async def test_periodic_reconciliation_reasserts_occupied_light_drift(
    mock_hass, mock_config_entry
):
    """The audit loop re-sends detected intent if an occupied entity drifts off."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)
    entity_state = coordinator._entity_states["light.living_room"]
    entity_state["state"] = EntityAutomationState.OCCUPIED
    await coordinator._apply_service_intent(
        "light.living_room",
        entity_state,
        CONF_PRESENCE_DETECTED_SERVICE,
        IntentReason.PRESENCE,
    )
    mock_hass.states.set("light.living_room", STATE_OFF)
    mock_hass.services.clear()

    await coordinator._periodic_reconciliation(None)

    assert_service_called(mock_hass, "light", "turn_on", "light.living_room")
