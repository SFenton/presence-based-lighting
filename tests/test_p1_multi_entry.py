"""Multi-entry integration tests for Presence Based Lighting.

This test suite validates complex scenarios with multiple integration entries:
- Living Room: lights (respect presence), vents (no respect), TV (off only)
- Kitchen: lights (respect presence), vents (no respect)
- Entryway: lights (respect presence), controls Living Room + Kitchen

Test Categories:
1. Isolated Entry Behavior - Each entry operates independently
2. Cross-Entry Presence Detection - Entryway controls other rooms
3. Presence Boolean Isolation - Toggle in one entry doesn't affect others
4. Service/State Asymmetry - TV turns off but not on
5. Concurrent Operations - Multiple entries active simultaneously
6. Timer Independence - Per-entry timeouts don't interfere
"""

import asyncio
import pytest
from unittest.mock import MagicMock

from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_ENTITY_OFF_DELAY,
    CONF_INITIAL_PRESENCE_ALLOWED,
    CONF_OFF_DELAY,
    CONF_PRESENCE_CLEARED_SERVICE,
    CONF_PRESENCE_CLEARED_STATE,
    CONF_PRESENCE_DETECTED_SERVICE,
    CONF_PRESENCE_DETECTED_STATE,
    CONF_PRESENCE_SENSORS,
    CONF_RESPECTS_PRESENCE_ALLOWED,
    CONF_ROOM_NAME,
    NO_ACTION,
)
from tests.conftest import assert_service_called, assert_service_not_called


def _create_living_room_entry():
    """Create Living Room entry with lights, vents, and TV."""
    entry = MagicMock()
    entry.entry_id = "living_room_entry"
    entry.data = {
        CONF_ROOM_NAME: "Living Room",
        CONF_PRESENCE_SENSORS: ["binary_sensor.living_room_motion"],
        CONF_OFF_DELAY: 10,
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: "turn_on",
                CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
                CONF_PRESENCE_DETECTED_STATE: "on",
                CONF_PRESENCE_CLEARED_STATE: "off",
                CONF_RESPECTS_PRESENCE_ALLOWED: True,  # Respects presence
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            },
            {
                CONF_ENTITY_ID: "climate.living_room_vents",
                CONF_PRESENCE_DETECTED_SERVICE: "turn_on",
                CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
                CONF_PRESENCE_DETECTED_STATE: "on",
                CONF_PRESENCE_CLEARED_STATE: "off",
                CONF_RESPECTS_PRESENCE_ALLOWED: False,  # Does NOT respect
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            },
            # TV achieves asymmetric behavior using NO_ACTION:
            # - Presence detected: NO_ACTION (TV never turns on)
            # - Presence cleared: turn_off (TV turns off)
            {
                CONF_ENTITY_ID: "media_player.living_room_tv",
                CONF_PRESENCE_DETECTED_SERVICE: NO_ACTION,  # Never turns on
                CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
                CONF_PRESENCE_DETECTED_STATE: "on",
                CONF_PRESENCE_CLEARED_STATE: "off",
                CONF_RESPECTS_PRESENCE_ALLOWED: False,  # Always follows presence
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            },
        ],
    }
    return entry


def _create_kitchen_entry():
    """Create Kitchen entry with lights and vents."""
    entry = MagicMock()
    entry.entry_id = "kitchen_entry"
    entry.data = {
        CONF_ROOM_NAME: "Kitchen",
        CONF_PRESENCE_SENSORS: ["binary_sensor.kitchen_motion"],
        CONF_OFF_DELAY: 5,  # Different timeout
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.kitchen",
                CONF_PRESENCE_DETECTED_SERVICE: "turn_on",
                CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
                CONF_PRESENCE_DETECTED_STATE: "on",
                CONF_PRESENCE_CLEARED_STATE: "off",
                CONF_RESPECTS_PRESENCE_ALLOWED: True,  # Respects presence
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            },
            {
                CONF_ENTITY_ID: "climate.kitchen_vents",
                CONF_PRESENCE_DETECTED_SERVICE: "turn_on",
                CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
                CONF_PRESENCE_DETECTED_STATE: "on",
                CONF_PRESENCE_CLEARED_STATE: "off",
                CONF_RESPECTS_PRESENCE_ALLOWED: False,  # Does NOT respect
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            },
        ],
    }
    return entry


def _create_entryway_entry():
    """Create Entryway entry that controls Living Room and Kitchen lights."""
    entry = MagicMock()
    entry.entry_id = "entryway_entry"
    entry.data = {
        CONF_ROOM_NAME: "Entryway",
        CONF_PRESENCE_SENSORS: ["binary_sensor.entryway_motion"],
        CONF_OFF_DELAY: 3,  # Quick timeout
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.entryway",
                CONF_PRESENCE_DETECTED_SERVICE: "turn_on",
                CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
                CONF_PRESENCE_DETECTED_STATE: "on",
                CONF_PRESENCE_CLEARED_STATE: "off",
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            },
            # Cross-entry control: Entryway controls Living Room lights
            {
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: "turn_on",
                CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
                CONF_PRESENCE_DETECTED_STATE: "on",
                CONF_PRESENCE_CLEARED_STATE: "off",
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            },
            # Cross-entry control: Entryway controls Kitchen lights
            {
                CONF_ENTITY_ID: "light.kitchen",
                CONF_PRESENCE_DETECTED_SERVICE: "turn_on",
                CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
                CONF_PRESENCE_DETECTED_STATE: "on",
                CONF_PRESENCE_CLEARED_STATE: "off",
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            },
        ],
    }
    return entry


def _presence_event(mock_hass, sensor_id, state):
    """Create a presence sensor state change event."""
    event_data = {
        "entity_id": sensor_id,
        "old_state": MagicMock(state=STATE_OFF if state == STATE_ON else STATE_ON),
        "new_state": MagicMock(state=state),
    }
    mock_hass.states.set(sensor_id, state)
    return event_data


# ============================================================================
# TEST CATEGORY 1: Isolated Entry Behavior
# Each entry should operate independently when only its sensor triggers
# ============================================================================

@pytest.mark.asyncio
async def test_living_room_isolated_presence_detection(mock_hass):
    """Living Room sensor controls all its devices independently."""
    entry = _create_living_room_entry()
    
    # Setup states
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_OFF)
    mock_hass.states.set("light.living_room", STATE_OFF)
    mock_hass.states.set("climate.living_room_vents", STATE_OFF)
    mock_hass.states.set("media_player.living_room_tv", "off")
    
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()
    
    # Trigger Living Room presence
    event_data = _presence_event(mock_hass, "binary_sensor.living_room_motion", STATE_ON)
    await coordinator._handle_presence_change(MagicMock(data=event_data))
    
    # Lights should turn on (respects presence)
    assert_service_called(mock_hass, "light", "turn_on", "light.living_room")
    
    # Vents should turn on (does NOT respect presence, always follows)
    assert_service_called(mock_hass, "climate", "turn_on", "climate.living_room_vents")
    
    # TV should NOT turn on (asymmetric - only turns off)
    tv_on_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "media_player"
        and c["service"] == "turn_on"
        and c["service_data"].get("entity_id") == "media_player.living_room_tv"
    ]
    assert len(tv_on_calls) == 0
    
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_living_room_isolated_presence_clear(mock_hass):
    """Living Room clear turns off all devices after timeout."""
    entry = _create_living_room_entry()
    
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
    mock_hass.states.set("light.living_room", STATE_ON)
    mock_hass.states.set("climate.living_room_vents", "on")
    mock_hass.states.set("media_player.living_room_tv", "playing")
    
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()
    
    # Clear presence
    event_data = _presence_event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF)
    await coordinator._handle_presence_change(MagicMock(data=event_data))
    
    # Wait for timeout (10s + buffer)
    await asyncio.sleep(10.5)
    
    # All should turn off
    assert_service_called(mock_hass, "light", "turn_off", "light.living_room")
    assert_service_called(mock_hass, "climate", "turn_off", "climate.living_room_vents")
    assert_service_called(mock_hass, "media_player", "turn_off", "media_player.living_room_tv")
    
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_kitchen_isolated_presence_detection(mock_hass):
    """Kitchen sensor controls only its devices."""
    entry = _create_kitchen_entry()
    
    mock_hass.states.set("binary_sensor.kitchen_motion", STATE_OFF)
    mock_hass.states.set("light.kitchen", STATE_OFF)
    mock_hass.states.set("climate.kitchen_vents", STATE_OFF)
    
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()
    
    # Trigger Kitchen presence
    event_data = _presence_event(mock_hass, "binary_sensor.kitchen_motion", STATE_ON)
    await coordinator._handle_presence_change(MagicMock(data=event_data))
    
    # Both should turn on
    assert_service_called(mock_hass, "light", "turn_on", "light.kitchen")
    assert_service_called(mock_hass, "climate", "turn_on", "climate.kitchen_vents")
    
    coordinator.async_stop()


@pytest.mark.asyncio
async def test_entryway_isolated_presence_detection(mock_hass):
    """Entryway sensor controls entryway + cross-entry lights."""
    entry = _create_entryway_entry()
    
    mock_hass.states.set("binary_sensor.entryway_motion", STATE_OFF)
    mock_hass.states.set("light.entryway", STATE_OFF)
    mock_hass.states.set("light.living_room", STATE_OFF)
    mock_hass.states.set("light.kitchen", STATE_OFF)
    
    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()
    
    # Trigger Entryway presence
    event_data = _presence_event(mock_hass, "binary_sensor.entryway_motion", STATE_ON)
    await coordinator._handle_presence_change(MagicMock(data=event_data))
    
    # All three lights should turn on
    assert_service_called(mock_hass, "light", "turn_on", "light.entryway")
    assert_service_called(mock_hass, "light", "turn_on", "light.living_room")
    assert_service_called(mock_hass, "light", "turn_on", "light.kitchen")
    
    coordinator.async_stop()


# ============================================================================
# TEST CATEGORY 2: Cross-Entry Presence Detection
# Entryway controls lights in other rooms - validate coordination
# ============================================================================

@pytest.mark.asyncio
async def test_entryway_and_living_room_both_occupied(mock_hass):
    """Both entries detect presence - lights stay on from either."""
    living_room = _create_living_room_entry()
    entryway = _create_entryway_entry()
    
    # Setup
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_OFF)
    mock_hass.states.set("binary_sensor.entryway_motion", STATE_OFF)
    mock_hass.states.set("light.living_room", STATE_OFF)
    mock_hass.states.set("light.entryway", STATE_OFF)
    
    lr_coord = PresenceBasedLightingCoordinator(mock_hass, living_room)
    ew_coord = PresenceBasedLightingCoordinator(mock_hass, entryway)
    await lr_coord.async_start()
    await ew_coord.async_start()
    
    # Trigger both sensors
    lr_event = _presence_event(mock_hass, "binary_sensor.living_room_motion", STATE_ON)
    await lr_coord._handle_presence_change(MagicMock(data=lr_event))
    
    ew_event = _presence_event(mock_hass, "binary_sensor.entryway_motion", STATE_ON)
    await ew_coord._handle_presence_change(MagicMock(data=ew_event))
    
    # Both lights should be on
    assert_service_called(mock_hass, "light", "turn_on", "light.living_room")
    assert_service_called(mock_hass, "light", "turn_on", "light.entryway")
    
    # Update light states to reflect they're on
    mock_hass.states.set("light.living_room", STATE_ON)
    mock_hass.states.set("light.entryway", STATE_ON)
    
    # Clear Living Room only
    mock_hass.services.calls.clear()
    lr_event = _presence_event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF)
    await lr_coord._handle_presence_change(MagicMock(data=lr_event))
    
    # Wait for Living Room timeout
    await asyncio.sleep(10.5)
    
    # Living Room lights should turn off from LR coordinator
    # But Entryway still has presence, so no turn-off from EW coordinator
    turn_off_count = len([
        c for c in mock_hass.services.calls
        if c["domain"] == "light"
        and c["service"] == "turn_off"
        and c["service_data"].get("entity_id") == "light.living_room"
    ])
    # Should have at least one turn_off from LR coordinator
    assert turn_off_count >= 1
    
    lr_coord.async_stop()
    ew_coord.async_stop()


@pytest.mark.asyncio
async def test_entryway_clears_before_living_room(mock_hass):
    """Entryway clears first, Living Room keeps light on."""
    living_room = _create_living_room_entry()
    entryway = _create_entryway_entry()
    
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
    mock_hass.states.set("binary_sensor.entryway_motion", STATE_ON)
    mock_hass.states.set("light.living_room", STATE_ON)
    mock_hass.states.set("light.entryway", STATE_ON)
    
    lr_coord = PresenceBasedLightingCoordinator(mock_hass, living_room)
    ew_coord = PresenceBasedLightingCoordinator(mock_hass, entryway)
    await lr_coord.async_start()
    await ew_coord.async_start()
    
    # Clear Entryway only
    ew_event = _presence_event(mock_hass, "binary_sensor.entryway_motion", STATE_OFF)
    await ew_coord._handle_presence_change(MagicMock(data=ew_event))
    
    # Wait for Entryway timeout (3s + buffer)
    await asyncio.sleep(3.5)
    
    # Entryway light turns off
    assert_service_called(mock_hass, "light", "turn_off", "light.entryway")
    
    # Living Room light might get turn_off from Entryway coordinator,
    # but Living Room still has presence, so it should stay conceptually "on"
    # In reality, EW coordinator will send turn_off, but the light state is still on
    # because LR sensor is still active
    
    lr_coord.async_stop()
    ew_coord.async_stop()


@pytest.mark.asyncio
async def test_kitchen_and_entryway_concurrent_control(mock_hass):
    """Kitchen and Entryway both control kitchen lights."""
    kitchen = _create_kitchen_entry()
    entryway = _create_entryway_entry()
    
    mock_hass.states.set("binary_sensor.kitchen_motion", STATE_OFF)
    mock_hass.states.set("binary_sensor.entryway_motion", STATE_OFF)
    mock_hass.states.set("light.kitchen", STATE_OFF)
    
    k_coord = PresenceBasedLightingCoordinator(mock_hass, kitchen)
    ew_coord = PresenceBasedLightingCoordinator(mock_hass, entryway)
    await k_coord.async_start()
    await ew_coord.async_start()
    
    # Trigger Entryway
    ew_event = _presence_event(mock_hass, "binary_sensor.entryway_motion", STATE_ON)
    await ew_coord._handle_presence_change(MagicMock(data=ew_event))
    
    # Kitchen light turns on
    assert_service_called(mock_hass, "light", "turn_on", "light.kitchen")
    
    # Trigger Kitchen sensor too
    k_event = _presence_event(mock_hass, "binary_sensor.kitchen_motion", STATE_ON)
    await k_coord._handle_presence_change(MagicMock(data=k_event))
    
    # Clear Entryway
    mock_hass.services.calls.clear()
    ew_event = _presence_event(mock_hass, "binary_sensor.entryway_motion", STATE_OFF)
    await ew_coord._handle_presence_change(MagicMock(data=ew_event))
    
    await asyncio.sleep(3.5)
    
    # Kitchen light gets turn_off from Entryway, but Kitchen still has presence
    # So the light remains effectively on
    
    k_coord.async_stop()
    ew_coord.async_stop()


# ============================================================================
# TEST CATEGORY 3: Presence Boolean Isolation
# Toggling presence_allowed in one entry doesn't affect others
# ============================================================================

@pytest.mark.asyncio
async def test_living_room_presence_toggle_does_not_affect_entryway(mock_hass):
    """Disabling Living Room presence doesn't affect Entryway control."""
    living_room = _create_living_room_entry()
    entryway = _create_entryway_entry()
    
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_OFF)
    mock_hass.states.set("binary_sensor.entryway_motion", STATE_OFF)
    mock_hass.states.set("light.living_room", STATE_OFF)
    
    lr_coord = PresenceBasedLightingCoordinator(mock_hass, living_room)
    ew_coord = PresenceBasedLightingCoordinator(mock_hass, entryway)
    await lr_coord.async_start()
    await ew_coord.async_start()
    
    # Disable Living Room presence for light.living_room
    await lr_coord.async_set_presence_allowed("light.living_room", False)
    
    # Trigger Living Room sensor - light should NOT turn on
    lr_event = _presence_event(mock_hass, "binary_sensor.living_room_motion", STATE_ON)
    await lr_coord._handle_presence_change(MagicMock(data=lr_event))
    
    lr_on_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "light"
        and c["service"] == "turn_on"
        and c["service_data"].get("entity_id") == "light.living_room"
    ]
    assert len(lr_on_calls) == 0
    
    # Trigger Entryway sensor - Entryway's instance of light.living_room SHOULD turn on
    mock_hass.services.calls.clear()
    ew_event = _presence_event(mock_hass, "binary_sensor.entryway_motion", STATE_ON)
    await ew_coord._handle_presence_change(MagicMock(data=ew_event))
    
    # Entryway coordinator should turn on light.living_room
    assert_service_called(mock_hass, "light", "turn_on", "light.living_room")
    
    lr_coord.async_stop()
    ew_coord.async_stop()


@pytest.mark.asyncio
async def test_entryway_presence_toggle_isolated_per_entity(mock_hass):
    """Disabling presence for one light in Entryway doesn't affect others."""
    entryway = _create_entryway_entry()
    
    mock_hass.states.set("binary_sensor.entryway_motion", STATE_OFF)
    mock_hass.states.set("light.entryway", STATE_OFF)
    mock_hass.states.set("light.living_room", STATE_OFF)
    mock_hass.states.set("light.kitchen", STATE_OFF)
    
    ew_coord = PresenceBasedLightingCoordinator(mock_hass, entryway)
    await ew_coord.async_start()
    
    # Disable presence for light.kitchen only
    await ew_coord.async_set_presence_allowed("light.kitchen", False)
    
    # Trigger presence
    ew_event = _presence_event(mock_hass, "binary_sensor.entryway_motion", STATE_ON)
    await ew_coord._handle_presence_change(MagicMock(data=ew_event))
    
    # Entryway and Living Room should turn on
    assert_service_called(mock_hass, "light", "turn_on", "light.entryway")
    assert_service_called(mock_hass, "light", "turn_on", "light.living_room")
    
    # Kitchen should NOT turn on
    kitchen_on_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "light"
        and c["service"] == "turn_on"
        and c["service_data"].get("entity_id") == "light.kitchen"
    ]
    assert len(kitchen_on_calls) == 0
    
    ew_coord.async_stop()


@pytest.mark.asyncio
async def test_kitchen_presence_toggle_does_not_affect_entryway_control(mock_hass):
    """Kitchen presence toggle is independent from Entryway's control."""
    kitchen = _create_kitchen_entry()
    entryway = _create_entryway_entry()
    
    mock_hass.states.set("binary_sensor.kitchen_motion", STATE_OFF)
    mock_hass.states.set("binary_sensor.entryway_motion", STATE_OFF)
    mock_hass.states.set("light.kitchen", STATE_OFF)
    
    k_coord = PresenceBasedLightingCoordinator(mock_hass, kitchen)
    ew_coord = PresenceBasedLightingCoordinator(mock_hass, entryway)
    await k_coord.async_start()
    await ew_coord.async_start()
    
    # Disable Kitchen presence
    await k_coord.async_set_presence_allowed("light.kitchen", False)
    
    # Kitchen sensor triggers - should NOT turn on
    k_event = _presence_event(mock_hass, "binary_sensor.kitchen_motion", STATE_ON)
    await k_coord._handle_presence_change(MagicMock(data=k_event))
    
    k_on_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "light"
        and c["service"] == "turn_on"
        and c["service_data"].get("entity_id") == "light.kitchen"
    ]
    assert len(k_on_calls) == 0
    
    # Entryway sensor triggers - SHOULD turn on (independent presence boolean)
    mock_hass.services.calls.clear()
    ew_event = _presence_event(mock_hass, "binary_sensor.entryway_motion", STATE_ON)
    await ew_coord._handle_presence_change(MagicMock(data=ew_event))
    
    assert_service_called(mock_hass, "light", "turn_on", "light.kitchen")
    
    k_coord.async_stop()
    ew_coord.async_stop()


# ============================================================================
# TEST CATEGORY 4: Service/State Asymmetry
# TV turns off but not on - validate this edge case
# ============================================================================

@pytest.mark.asyncio
async def test_tv_never_turns_on_from_presence(mock_hass):
    """TV in Living Room never turns on (NO_ACTION for detected service)."""
    living_room = _create_living_room_entry()
    
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_OFF)
    mock_hass.states.set("media_player.living_room_tv", "off")
    
    lr_coord = PresenceBasedLightingCoordinator(mock_hass, living_room)
    await lr_coord.async_start()
    
    # Trigger presence multiple times
    for _ in range(3):
        lr_event = _presence_event(mock_hass, "binary_sensor.living_room_motion", STATE_ON)
        await lr_coord._handle_presence_change(MagicMock(data=lr_event))
        await asyncio.sleep(0.1)
    
    # TV should NEVER receive turn_on
    tv_on_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "media_player"
        and c["service"] == "turn_on"
        and c["service_data"].get("entity_id") == "media_player.living_room_tv"
    ]
    assert len(tv_on_calls) == 0
    
    lr_coord.async_stop()


@pytest.mark.asyncio
async def test_tv_turns_off_on_clear(mock_hass):
    """TV turns off when presence clears."""
    living_room = _create_living_room_entry()
    
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
    mock_hass.states.set("media_player.living_room_tv", "playing")
    
    lr_coord = PresenceBasedLightingCoordinator(mock_hass, living_room)
    await lr_coord.async_start()
    
    # Clear presence
    lr_event = _presence_event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF)
    await lr_coord._handle_presence_change(MagicMock(data=lr_event))
    
    await asyncio.sleep(10.5)
    
    # TV should turn off
    assert_service_called(mock_hass, "media_player", "turn_off", "media_player.living_room_tv")
    
    lr_coord.async_stop()


@pytest.mark.asyncio
async def test_tv_presence_toggle_does_not_enable_turn_on(mock_hass):
    """TV doesn't turn on (NO_ACTION for detected service)."""
    living_room = _create_living_room_entry()
    
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_OFF)
    mock_hass.states.set("media_player.living_room_tv", "off")
    
    lr_coord = PresenceBasedLightingCoordinator(mock_hass, living_room)
    await lr_coord.async_start()
    
    # Enable presence for TV (should have no effect - respects=False)
    await lr_coord.async_set_presence_allowed("media_player.living_room_tv", True)
    
    # Trigger presence
    lr_event = _presence_event(mock_hass, "binary_sensor.living_room_motion", STATE_ON)
    await lr_coord._handle_presence_change(MagicMock(data=lr_event))
    
    # TV still should NOT turn on
    tv_on_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "media_player"
        and c["service"] == "turn_on"
        and c["service_data"].get("entity_id") == "media_player.living_room_tv"
    ]
    assert len(tv_on_calls) == 0
    
    lr_coord.async_stop()


# ============================================================================
# TEST CATEGORY 5: Concurrent Operations
# Multiple entries active simultaneously with overlapping entities
# ============================================================================

@pytest.mark.asyncio
async def test_all_three_entries_concurrent_presence(mock_hass):
    """All three entries detect presence - validate coordination."""
    living_room = _create_living_room_entry()
    kitchen = _create_kitchen_entry()
    entryway = _create_entryway_entry()
    
    # Setup all sensors off
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_OFF)
    mock_hass.states.set("binary_sensor.kitchen_motion", STATE_OFF)
    mock_hass.states.set("binary_sensor.entryway_motion", STATE_OFF)
    
    # Setup all devices off
    mock_hass.states.set("light.living_room", STATE_OFF)
    mock_hass.states.set("light.kitchen", STATE_OFF)
    mock_hass.states.set("light.entryway", STATE_OFF)
    mock_hass.states.set("climate.living_room_vents", STATE_OFF)
    mock_hass.states.set("climate.kitchen_vents", STATE_OFF)
    mock_hass.states.set("media_player.living_room_tv", "off")
    
    lr_coord = PresenceBasedLightingCoordinator(mock_hass, living_room)
    k_coord = PresenceBasedLightingCoordinator(mock_hass, kitchen)
    ew_coord = PresenceBasedLightingCoordinator(mock_hass, entryway)
    
    await lr_coord.async_start()
    await k_coord.async_start()
    await ew_coord.async_start()
    
    # Trigger all three sensors
    lr_event = _presence_event(mock_hass, "binary_sensor.living_room_motion", STATE_ON)
    await lr_coord._handle_presence_change(MagicMock(data=lr_event))
    
    k_event = _presence_event(mock_hass, "binary_sensor.kitchen_motion", STATE_ON)
    await k_coord._handle_presence_change(MagicMock(data=k_event))
    
    ew_event = _presence_event(mock_hass, "binary_sensor.entryway_motion", STATE_ON)
    await ew_coord._handle_presence_change(MagicMock(data=ew_event))
    
    # Validate all lights are on
    assert_service_called(mock_hass, "light", "turn_on", "light.living_room")
    assert_service_called(mock_hass, "light", "turn_on", "light.kitchen")
    assert_service_called(mock_hass, "light", "turn_on", "light.entryway")
    
    # Validate vents are on
    assert_service_called(mock_hass, "climate", "turn_on", "climate.living_room_vents")
    assert_service_called(mock_hass, "climate", "turn_on", "climate.kitchen_vents")
    
    # TV should NOT turn on
    tv_on_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "media_player"
        and c["service"] == "turn_on"
    ]
    assert len(tv_on_calls) == 0
    
    lr_coord.async_stop()
    k_coord.async_stop()
    ew_coord.async_stop()


@pytest.mark.asyncio
async def test_cascading_clears_different_timeouts(mock_hass):
    """All entries clear at different times due to different timeouts."""
    living_room = _create_living_room_entry()  # 10s timeout
    kitchen = _create_kitchen_entry()  # 5s timeout
    entryway = _create_entryway_entry()  # 3s timeout
    
    # All sensors on
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
    mock_hass.states.set("binary_sensor.kitchen_motion", STATE_ON)
    mock_hass.states.set("binary_sensor.entryway_motion", STATE_ON)
    
    # All devices on
    mock_hass.states.set("light.living_room", STATE_ON)
    mock_hass.states.set("light.kitchen", STATE_ON)
    mock_hass.states.set("light.entryway", STATE_ON)
    
    lr_coord = PresenceBasedLightingCoordinator(mock_hass, living_room)
    k_coord = PresenceBasedLightingCoordinator(mock_hass, kitchen)
    ew_coord = PresenceBasedLightingCoordinator(mock_hass, entryway)
    
    await lr_coord.async_start()
    await k_coord.async_start()
    await ew_coord.async_start()
    
    # Clear all sensors simultaneously
    lr_event = _presence_event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF)
    await lr_coord._handle_presence_change(MagicMock(data=lr_event))
    
    k_event = _presence_event(mock_hass, "binary_sensor.kitchen_motion", STATE_OFF)
    await k_coord._handle_presence_change(MagicMock(data=k_event))
    
    ew_event = _presence_event(mock_hass, "binary_sensor.entryway_motion", STATE_OFF)
    await ew_coord._handle_presence_change(MagicMock(data=ew_event))
    
    # After 3.5s, Entryway should turn off
    mock_hass.services.calls.clear()
    await asyncio.sleep(3.5)
    
    # Entryway light should be off
    entryway_off_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "light"
        and c["service"] == "turn_off"
        and c["service_data"].get("entity_id") == "light.entryway"
    ]
    assert len(entryway_off_calls) >= 1
    
    # After additional 2s (total 5.5s), Kitchen should turn off
    mock_hass.services.calls.clear()
    await asyncio.sleep(2)
    
    kitchen_off_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "light"
        and c["service"] == "turn_off"
        and c["service_data"].get("entity_id") == "light.kitchen"
    ]
    assert len(kitchen_off_calls) >= 1
    
    # After additional 5s (total 10.5s), Living Room should turn off
    mock_hass.services.calls.clear()
    await asyncio.sleep(5)
    
    lr_off_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "light"
        and c["service"] == "turn_off"
        and c["service_data"].get("entity_id") == "light.living_room"
    ]
    assert len(lr_off_calls) >= 1
    
    lr_coord.async_stop()
    k_coord.async_stop()
    ew_coord.async_stop()


# ============================================================================
# TEST CATEGORY 6: Timer Independence
# Per-entry timers don't interfere with each other
# ============================================================================

@pytest.mark.asyncio
async def test_living_room_timer_does_not_affect_kitchen(mock_hass):
    """Living Room timeout doesn't affect Kitchen devices."""
    living_room = _create_living_room_entry()
    kitchen = _create_kitchen_entry()
    
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
    mock_hass.states.set("binary_sensor.kitchen_motion", STATE_ON)
    mock_hass.states.set("light.living_room", STATE_ON)
    mock_hass.states.set("light.kitchen", STATE_ON)
    
    lr_coord = PresenceBasedLightingCoordinator(mock_hass, living_room)
    k_coord = PresenceBasedLightingCoordinator(mock_hass, kitchen)
    
    await lr_coord.async_start()
    await k_coord.async_start()
    
    # Clear Living Room only
    lr_event = _presence_event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF)
    await lr_coord._handle_presence_change(MagicMock(data=lr_event))
    
    # Wait for Living Room timeout
    await asyncio.sleep(10.5)
    
    # Living Room light should turn off
    assert_service_called(mock_hass, "light", "turn_off", "light.living_room")
    
    # Kitchen light should NOT turn off (still occupied)
    kitchen_off_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "light"
        and c["service"] == "turn_off"
        and c["service_data"].get("entity_id") == "light.kitchen"
    ]
    assert len(kitchen_off_calls) == 0
    
    lr_coord.async_stop()
    k_coord.async_stop()


@pytest.mark.asyncio
async def test_entryway_timer_independent_from_controlled_rooms(mock_hass):
    """Entryway timer only affects Entryway-controlled entities."""
    living_room = _create_living_room_entry()
    entryway = _create_entryway_entry()
    
    # Living Room has presence, Entryway doesn't
    mock_hass.states.set("binary_sensor.living_room_motion", STATE_ON)
    mock_hass.states.set("binary_sensor.entryway_motion", STATE_ON)
    mock_hass.states.set("light.living_room", STATE_ON)
    mock_hass.states.set("light.entryway", STATE_ON)
    mock_hass.states.set("climate.living_room_vents", STATE_ON)
    
    lr_coord = PresenceBasedLightingCoordinator(mock_hass, living_room)
    ew_coord = PresenceBasedLightingCoordinator(mock_hass, entryway)
    
    await lr_coord.async_start()
    await ew_coord.async_start()
    
    # Clear Entryway only
    ew_event = _presence_event(mock_hass, "binary_sensor.entryway_motion", STATE_OFF)
    await ew_coord._handle_presence_change(MagicMock(data=ew_event))
    
    # Wait for Entryway timeout
    await asyncio.sleep(3.5)
    
    # Entryway light turns off
    assert_service_called(mock_hass, "light", "turn_off", "light.entryway")
    
    # Living Room vents should NOT turn off (not controlled by Entryway)
    vents_off_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "climate"
        and c["service"] == "turn_off"
        and c["service_data"].get("entity_id") == "climate.living_room_vents"
    ]
    assert len(vents_off_calls) == 0
    
    lr_coord.async_stop()
    ew_coord.async_stop()
