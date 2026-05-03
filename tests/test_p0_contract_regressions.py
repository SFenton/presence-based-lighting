"""Contract tests for architectural behavior that is currently inconsistent.

These tests intentionally assert the user-facing contracts described by the
integration UI/docs rather than the current implementation details. They are
expected to fail until the coordinator/service architecture is brought into
alignment with those contracts.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import (
    PresenceBasedLightingCoordinator,
    SERVICE_PAUSE_AUTOMATION,
    async_setup,
)
from custom_components.presence_based_lighting.const import (
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
    CONF_PRESENCE_SENSORS,
    CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
    CONF_REQUIRE_VACANCY_FOR_CLEARED,
    CONF_RESPECTS_PRESENCE_ALLOWED,
    CONF_RLC_TRACKING_ENTITY,
    CONF_ROOM_NAME,
    DEFAULT_CLEARED_SERVICE,
    DEFAULT_CLEARED_STATE,
    DEFAULT_DETECTED_SERVICE,
    DEFAULT_DETECTED_STATE,
    DEFAULT_MANUAL_DISABLE_STATES,
    DOMAIN,
)
from tests.conftest import assert_service_called, setup_entity_states


def _context(context_id: str = "manual"):
    return type("Ctx", (), {"id": context_id, "parent_id": None})()


def _state(state: str, *, attributes: dict | None = None, context=None):
    return type(
        "State",
        (),
        {
            "state": state,
            "attributes": attributes or {},
            "context": context or _context(),
        },
    )()


def _state_change_event(
    mock_hass,
    entity_id: str,
    old_state: str,
    new_state: str,
    *,
    old_attrs: dict | None = None,
    new_attrs: dict | None = None,
    context=None,
):
    mock_hass.states.set(entity_id, new_state, context=context, attributes=new_attrs)
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": _state(old_state, attributes=old_attrs),
                "new_state": _state(new_state, attributes=new_attrs, context=context),
            }
        },
    )()


def _service_event(entity_id: str, service: str):
    return type(
        "Event",
        (),
        {
            "data": {
                "service_data": {"entity_id": entity_id},
                "service": service,
            },
            "context": _context("external"),
        },
    )()


def _service_call(*, target_switches=None, entity_id=None):
    call = MagicMock()
    call.data = {}
    if entity_id is not None:
        call.data["entity_id"] = entity_id
    call.target = {"entity_id": target_switches} if target_switches else None
    return call


def _entry(
    *,
    entry_id: str,
    room_name: str,
    presence_sensor: str,
    controlled_entity: str = "light.living_room",
    off_delay: int = 1,
    respects_presence_allowed: bool = True,
    disable_on_external: bool = True,
    require_occupancy: bool = False,
    require_vacancy: bool = False,
    manual_disable_states: list[str] | None = None,
    rlc_tracking_entity: str | None = None,
):
    entity_config = {
        CONF_ENTITY_ID: controlled_entity,
        CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
        CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
        CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
        CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
        CONF_RESPECTS_PRESENCE_ALLOWED: respects_presence_allowed,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: disable_on_external,
        CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: require_occupancy,
        CONF_REQUIRE_VACANCY_FOR_CLEARED: require_vacancy,
        CONF_INITIAL_PRESENCE_ALLOWED: True,
    }
    if manual_disable_states is not None:
        entity_config[CONF_MANUAL_DISABLE_STATES] = manual_disable_states
    if rlc_tracking_entity is not None:
        entity_config[CONF_RLC_TRACKING_ENTITY] = rlc_tracking_entity

    entry = MagicMock()
    entry.domain = DOMAIN
    entry.entry_id = entry_id
    entry.unique_id = room_name
    entry.version = 7
    entry.data = {
        CONF_ROOM_NAME: room_name,
        CONF_PRESENCE_SENSORS: [presence_sensor],
        CONF_OFF_DELAY: off_delay,
        CONF_CONTROLLED_ENTITIES: [entity_config],
    }
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock()
    return entry


@pytest.mark.asyncio
async def test_entity_that_ignores_presence_allowed_still_follows_presence(
    mock_hass, mock_config_entry
):
    """An entity with respect_presence_allowed=False should ignore the switch."""
    mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][
        CONF_RESPECTS_PRESENCE_ALLOWED
    ] = False
    setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
    await coordinator.async_start()

    try:
        await coordinator.async_set_presence_allowed("light.living_room", False)
        mock_hass.services.clear()

        await coordinator._handle_presence_change(
            _state_change_event(
                mock_hass,
                "binary_sensor.living_room_motion",
                STATE_OFF,
                STATE_ON,
            )
        )

        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_pause_service_targets_actual_per_entity_presence_switch(
    mock_hass, mock_config_entry
):
    """The public pause service should accept the switch entity that exists."""
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
    mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await coordinator.async_start()
    await async_setup(mock_hass, {})

    try:
        actual_presence_switch = "switch.living_room_presence_living_room_presence_allowed"
        handler = mock_hass.services._registered[(DOMAIN, SERVICE_PAUSE_AUTOMATION)]

        await handler(_service_call(target_switches=actual_presence_switch))

        assert coordinator.get_automation_paused("light.living_room") is True
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_manual_off_pauses_automation_by_default_contract(
    mock_hass, mock_config_entry
):
    """The documented default contract is that manual off pauses automation."""
    assert DEFAULT_MANUAL_DISABLE_STATES == [STATE_OFF]
    mock_config_entry.data[CONF_CONTROLLED_ENTITIES][0][
        CONF_MANUAL_DISABLE_STATES
    ] = list(DEFAULT_MANUAL_DISABLE_STATES)
    setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
    await coordinator.async_start()

    try:
        await coordinator._handle_controlled_entity_change(
            _state_change_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )

        assert coordinator.get_automation_paused("light.living_room") is True
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_overlapping_entries_do_not_turn_off_entity_still_needed_elsewhere(
    mock_hass,
):
    """One room clearing should not turn off a light another occupied room owns."""
    living_room_entry = _entry(
        entry_id="living_room_entry",
        room_name="Living Room",
        presence_sensor="binary_sensor.living_room_motion",
        controlled_entity="light.living_room",
        off_delay=30,
    )
    entryway_entry = _entry(
        entry_id="entryway_entry",
        room_name="Entryway",
        presence_sensor="binary_sensor.entryway_motion",
        controlled_entity="light.living_room",
        off_delay=0,
    )

    mock_hass.states.set("light.living_room", STATE_ON)
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
    mock_hass.states.set("binary_sensor.entryway_motion", STATE_ON)

    living_room = PresenceBasedLightingCoordinator(mock_hass, living_room_entry)
    entryway = PresenceBasedLightingCoordinator(mock_hass, entryway_entry)
    await living_room.async_start()
    await entryway.async_start()

    try:
        mock_hass.services.clear()
        await entryway._handle_presence_change(
            _state_change_event(
                mock_hass,
                "binary_sensor.entryway_motion",
                STATE_ON,
                STATE_OFF,
            )
        )
        await asyncio.sleep(0.05)

        turn_off_calls = [
            call
            for call in mock_hass.services.calls
            if call["domain"] == "light"
            and call["service"] == "turn_off"
            and call["service_data"].get("entity_id") == "light.living_room"
        ]
        assert turn_off_calls == []
    finally:
        living_room.async_stop()
        entryway.async_stop()


@pytest.mark.asyncio
async def test_presence_lock_fallback_still_protects_group_targets_with_interceptor(
    mock_hass,
):
    """Interceptor mode should not create a gap for grouped service targets."""
    entry = _entry(
        entry_id="presence_lock_entry",
        room_name="Living Room",
        presence_sensor="binary_sensor.living_room_motion",
        controlled_entity="light.living_room",
        disable_on_external=False,
        require_vacancy=True,
    )
    mock_hass.states.set("light.living_room", STATE_ON)
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
    mock_hass.states.set(
        "light.downstairs",
        STATE_ON,
        attributes={"entity_id": ["light.living_room"]},
    )
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        coordinator._using_interceptor = True
        mock_hass.services.clear()

        await coordinator._handle_service_call(
            _service_event("light.downstairs", "turn_off")
        )

        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")
    finally:
        coordinator.async_stop()


@pytest.mark.asyncio
async def test_missing_rlc_tracking_entity_falls_back_to_direct_manual_state(
    mock_hass,
):
    """Unavailable RLC data should not swallow real manual state changes."""
    entry = _entry(
        entry_id="rlc_entry",
        room_name="Living Room",
        presence_sensor="binary_sensor.living_room_motion",
        controlled_entity="light.living_room",
        manual_disable_states=[STATE_OFF],
        rlc_tracking_entity="sensor.living_room_real_last_changed",
    )
    mock_hass.states.set("light.living_room", STATE_ON)
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    try:
        await coordinator._handle_controlled_entity_change(
            _state_change_event(mock_hass, "light.living_room", STATE_ON, STATE_OFF)
        )

        assert coordinator.get_automation_paused("light.living_room") is True
    finally:
        coordinator.async_stop()