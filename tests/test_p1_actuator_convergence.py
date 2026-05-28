"""Closed-loop actuator convergence tests."""

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import (
    ActuationStatus,
    EntityAutomationState,
    PresenceBasedLightingCoordinator,
    _ACTUATION_MAX_ATTEMPTS,
)
from custom_components.presence_based_lighting.const import CONF_PRESENCE_CLEARED_SERVICE
from tests.conftest import MockState, assert_service_called, setup_entity_states


def _event(mock_hass, entity_id, old_state, new_state, context):
    mock_hass.states.set(entity_id, new_state, context=context)
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": MockState(entity_id, old_state),
                "new_state": MockState(entity_id, new_state, context=context),
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
    await coordinator._execute_actuation_confirmation_timer("light.living_room", entity_state, 0)

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

    await coordinator._execute_actuation_retry_timer("light.living_room", entity_state, 0)

    turn_off_calls = [call for call in mock_hass.services.calls if call["service"] == "turn_off"]
    assert len(turn_off_calls) == 2
    assert entity_state["state"] == EntityAutomationState.SETTLING_OFF
    assert coordinator.get_automation_paused("light.living_room") is False


@pytest.mark.asyncio
async def test_rebound_does_not_retry_when_presence_returns(mock_hass, mock_config_entry):
    """Off convergence is canceled as soon as clearing sensors are no longer clear."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)

    entity_state = await _start_cleared_actuation(coordinator)
    first_context = mock_hass.services.calls[-1]["context"]
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)

    await coordinator._handle_controlled_entity_change(
        _event(mock_hass, "light.living_room", STATE_OFF, STATE_ON, first_context)
    )

    turn_off_calls = [call for call in mock_hass.services.calls if call["service"] == "turn_off"]
    assert len(turn_off_calls) == 1
    assert entity_state["actuation"]["status"] == ActuationStatus.CANCELED


@pytest.mark.asyncio
async def test_failed_convergence_visible_after_max_attempts(mock_hass, mock_config_entry):
    """A still-on entity after max attempts is marked failed instead of hidden."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)

    entity_state = await _start_cleared_actuation(coordinator)
    entity_state["actuation"]["attempts"] = _ACTUATION_MAX_ATTEMPTS

    await coordinator._retry_or_fail_entity_actuation("light.living_room", entity_state, STATE_ON)

    assert entity_state["actuation"]["status"] == ActuationStatus.FAILED
    assert "did not converge" in entity_state["actuation"]["last_error"]


@pytest.mark.asyncio
async def test_periodic_reconciliation_starts_missing_off_actuation(mock_hass, mock_config_entry):
    """The audit loop starts actuation when IDLE disagrees with actual state."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
    coordinator = _make_coordinator(mock_hass, mock_config_entry)
    entity_state = coordinator._entity_states["light.living_room"]
    entity_state["state"] = EntityAutomationState.IDLE

    await coordinator._periodic_reconciliation(None)

    assert entity_state["state"] == EntityAutomationState.SETTLING_OFF
    assert entity_state["actuation"]["service_key"] == CONF_PRESENCE_CLEARED_SERVICE
    assert_service_called(mock_hass, "light", "turn_off", "light.living_room")