"""Tests for per-entity timeout functionality."""

import asyncio
import pytest
from unittest.mock import MagicMock

from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from tests.conftest import assert_service_called, assert_service_not_called
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
    DEFAULT_CLEARED_SERVICE,
    DEFAULT_CLEARED_STATE,
    DEFAULT_DETECTED_SERVICE,
    DEFAULT_DETECTED_STATE,
)


def _presence_event(mock_hass, sensor_id, state):
    """Fire a presence sensor state change."""
    event_data = {
        "entity_id": sensor_id,
        "old_state": MagicMock(state=STATE_OFF if state == STATE_ON else STATE_ON),
        "new_state": MagicMock(state=state),
    }
    mock_hass.states.set(sensor_id, state)
    return event_data


@pytest.mark.asyncio
async def test_entity_specific_timeout_overrides_global(mock_hass):
    """Test that entity-specific timeout overrides the global timeout."""
    entry = MagicMock()
    entry.entry_id = "test123"
    entry.data = {
        CONF_ROOM_NAME: "Test Room",
        CONF_PRESENCE_SENSORS: ["binary_sensor.motion"],
        CONF_OFF_DELAY: 30,  # Global timeout
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.fast",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
                CONF_ENTITY_OFF_DELAY: 5,  # Entity-specific timeout
            },
            {
                CONF_ENTITY_ID: "light.slow",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
                # No entity-specific timeout - will use global 30s
            },
        ],
    }

    mock_hass.states.set("binary_sensor.motion", STATE_ON)
    mock_hass.states.set("light.fast", STATE_ON)
    mock_hass.states.set("light.slow", STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    # Trigger presence detected
    event_data = _presence_event(mock_hass, "binary_sensor.motion", STATE_ON)
    await coordinator._handle_presence_change(MagicMock(data=event_data))

    # Clear presence - should start both timers
    event_data = _presence_event(mock_hass, "binary_sensor.motion", STATE_OFF)
    await coordinator._handle_presence_change(MagicMock(data=event_data))

    # Verify timers are running
    assert coordinator._entity_states["light.fast"]["off_timer"] is not None
    assert coordinator._entity_states["light.slow"]["off_timer"] is not None

    # Wait past fast entity timeout (5s + buffer)
    await asyncio.sleep(5.5)

    # Fast entity should have turned off
    assert_service_called(mock_hass, "light", "turn_off", "light.fast")

    # Slow entity should still be on (30s timeout) - check it wasn't turned off
    slow_off_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "light" 
        and c["service"] == "turn_off" 
        and c["service_data"].get("entity_id") == "light.slow"
    ]
    assert len(slow_off_calls) == 0

    coordinator.async_stop()


@pytest.mark.asyncio
async def test_entities_use_global_timeout_when_not_specified(mock_hass):
    """Test that entities without specific timeouts use the global value."""
    entry = MagicMock()
    entry.entry_id = "test456"
    entry.data = {
        CONF_ROOM_NAME: "Test Room",
        CONF_PRESENCE_SENSORS: ["binary_sensor.motion"],
        CONF_OFF_DELAY: 3,  # Short global timeout for testing
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.one",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            },
            {
                CONF_ENTITY_ID: "light.two",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            },
        ],
    }

    mock_hass.states.set("binary_sensor.motion", STATE_ON)
    mock_hass.states.set("light.one", STATE_ON)
    mock_hass.states.set("light.two", STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    # Trigger presence detected
    event_data = _presence_event(mock_hass, "binary_sensor.motion", STATE_ON)
    await coordinator._handle_presence_change(MagicMock(data=event_data))

    # Clear presence
    event_data = _presence_event(mock_hass, "binary_sensor.motion", STATE_OFF)
    await coordinator._handle_presence_change(MagicMock(data=event_data))

    # Wait for global timeout
    await asyncio.sleep(3.5)

    # Both entities should have turned off using the global 3s timeout
    assert_service_called(mock_hass, "light", "turn_off", "light.one")
    assert_service_called(mock_hass, "light", "turn_off", "light.two")

    coordinator.async_stop()


@pytest.mark.asyncio
async def test_entity_timer_cancelled_on_reoccupancy(mock_hass):
    """Test that per-entity timers are cancelled when presence returns."""
    entry = MagicMock()
    entry.entry_id = "test789"
    entry.data = {
        CONF_ROOM_NAME: "Test Room",
        CONF_PRESENCE_SENSORS: ["binary_sensor.motion"],
        CONF_OFF_DELAY: 30,
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.test",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
                CONF_ENTITY_OFF_DELAY: 10,
            },
        ],
    }

    mock_hass.states.set("binary_sensor.motion", STATE_ON)
    mock_hass.states.set("light.test", STATE_ON)

    coordinator = PresenceBasedLightingCoordinator(mock_hass, entry)
    await coordinator.async_start()

    # Clear presence - start timer
    event_data = _presence_event(mock_hass, "binary_sensor.motion", STATE_OFF)
    await coordinator._handle_presence_change(MagicMock(data=event_data))

    assert coordinator._entity_states["light.test"]["off_timer"] is not None

    # Wait a bit (but not full timeout)
    await asyncio.sleep(2)

    # Reoccupy before timeout
    event_data = _presence_event(mock_hass, "binary_sensor.motion", STATE_ON)
    await coordinator._handle_presence_change(MagicMock(data=event_data))

    # Timer may be restarted (for primer sensor flow) but lights should stay on
    # since clearing sensors are not clear (room is occupied)

    # Wait past original timeout
    await asyncio.sleep(9)

    # Light should NOT have turned off
    test_off_calls = [
        c for c in mock_hass.services.calls
        if c["domain"] == "light" 
        and c["service"] == "turn_off" 
        and c["service_data"].get("entity_id") == "light.test"
    ]
    assert len(test_off_calls) == 0

    coordinator.async_stop()
