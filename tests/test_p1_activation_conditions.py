"""Tests for activation conditions feature.

Activation conditions are optional binary_sensor/input_boolean entities that must 
ALL be on for lights to activate. This enables scenarios like:
- Only turn on lights when it's dark (lux sensor)
- Only turn on lights when home (presence state)
- Only turn on lights during certain modes (input_boolean)
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from custom_components.presence_based_lighting.const import (
    CONF_ACTIVATION_CONDITIONS,
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_INITIAL_PRESENCE_ALLOWED,
    CONF_OFF_DELAY,
    CONF_PRESENCE_CLEARED_SERVICE,
    CONF_PRESENCE_CLEARED_STATE,
    CONF_PRESENCE_DETECTED_SERVICE,
    CONF_PRESENCE_DETECTED_STATE,
    CONF_PRESENCE_SENSORS,
    CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
    CONF_REQUIRE_VACANCY_FOR_CLEARED,
    CONF_RESPECTS_PRESENCE_ALLOWED,
    CONF_ROOM_NAME,
    DEFAULT_CLEARED_SERVICE,
    DEFAULT_CLEARED_STATE,
    DEFAULT_DETECTED_SERVICE,
    DEFAULT_DETECTED_STATE,
    DEFAULT_INITIAL_PRESENCE_ALLOWED,
    DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED,
    DEFAULT_REQUIRE_VACANCY_FOR_CLEARED,
    DOMAIN,
)
from tests.conftest import assert_service_called, assert_service_not_called, setup_entity_states


def _state(state, attributes=None):
    return type(
        "State",
        (),
        {
            "state": state,
            "attributes": attributes or {},
            "context": type("Ctx", (), {"id": "ctx", "parent_id": None})(),
        },
    )()


def _event(mock_hass, entity_id, old_state, new_state, old_attrs=None, new_attrs=None):
    mock_hass.states.set(entity_id, new_state)
    return type(
        "Event",
        (),
        {
            "data": {
                "entity_id": entity_id,
                "old_state": _state(old_state, old_attrs),
                "new_state": _state(new_state, new_attrs),
            }
        },
    )()


@pytest.fixture
def mock_config_entry_with_activation_condition():
    """Config entry with a single activation condition."""
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.version = 6
    entry.data = {
        CONF_ROOM_NAME: "Living Room",
        CONF_PRESENCE_SENSORS: ["binary_sensor.motion"],
        CONF_ACTIVATION_CONDITIONS: ["binary_sensor.lux_dark"],
        CONF_OFF_DELAY: 1,
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED,
                CONF_REQUIRE_VACANCY_FOR_CLEARED: DEFAULT_REQUIRE_VACANCY_FOR_CLEARED,
                CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
            }
        ],
    }
    entry.entry_id = "test_entry_activation"
    entry.unique_id = "Living Room"
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock()
    return entry


@pytest.fixture
def mock_config_entry_with_multiple_activation_conditions():
    """Config entry with multiple activation conditions (AND logic)."""
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.version = 6
    entry.data = {
        CONF_ROOM_NAME: "Living Room",
        CONF_PRESENCE_SENSORS: ["binary_sensor.motion"],
        CONF_ACTIVATION_CONDITIONS: ["binary_sensor.lux_dark", "input_boolean.home_mode"],
        CONF_OFF_DELAY: 1,
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.living_room",
                CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
                CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
                CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
                CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED,
                CONF_REQUIRE_VACANCY_FOR_CLEARED: DEFAULT_REQUIRE_VACANCY_FOR_CLEARED,
                CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
            }
        ],
    }
    entry.entry_id = "test_entry_multi_activation"
    entry.unique_id = "Living Room"
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock()
    return entry


class TestActivationConditionsMet:
    """Test behavior when activation conditions are satisfied."""

    @pytest.mark.asyncio
    async def test_presence_with_condition_met_turns_on_lights(
        self, mock_hass, mock_config_entry_with_activation_condition
    ):
        """When presence detected AND condition is met, lights should turn on."""
        mock_hass.states.set("light.living_room", STATE_OFF)
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("binary_sensor.lux_dark", STATE_ON)

        coordinator = PresenceBasedLightingCoordinator(
            mock_hass, mock_config_entry_with_activation_condition
        )
        await coordinator.async_start()

        # Clear any prior calls
        mock_hass.services.calls.clear()

        # Presence detected with condition already met
        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.motion", STATE_OFF, STATE_ON)
        )

        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")


class TestActivationConditionsNotMet:
    """Test behavior when activation conditions are NOT satisfied."""

    @pytest.mark.asyncio
    async def test_presence_with_condition_not_met_does_not_turn_on_lights(
        self, mock_hass, mock_config_entry_with_activation_condition
    ):
        """When presence detected BUT condition is NOT met, lights should NOT turn on."""
        mock_hass.states.set("light.living_room", STATE_OFF)
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("binary_sensor.lux_dark", STATE_OFF)  # Too bright

        coordinator = PresenceBasedLightingCoordinator(
            mock_hass, mock_config_entry_with_activation_condition
        )
        await coordinator.async_start()

        # Clear any prior calls
        mock_hass.services.calls.clear()

        # Presence detected but condition not met
        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.motion", STATE_OFF, STATE_ON)
        )

        assert_service_not_called(mock_hass, "light", "turn_on")


class TestActivationConditionBecomesTrue:
    """Test reactive triggering when condition becomes true while occupied."""

    @pytest.mark.asyncio
    async def test_condition_met_while_occupied_turns_on_lights(
        self, mock_hass, mock_config_entry_with_activation_condition
    ):
        """When already occupied and condition becomes true, lights should turn on."""
        mock_hass.states.set("light.living_room", STATE_OFF)
        mock_hass.states.set("binary_sensor.motion", STATE_ON)  # Room is occupied
        mock_hass.states.set("binary_sensor.lux_dark", STATE_OFF)  # Initially too bright

        coordinator = PresenceBasedLightingCoordinator(
            mock_hass, mock_config_entry_with_activation_condition
        )
        await coordinator.async_start()

        # Clear any prior calls
        mock_hass.services.calls.clear()

        # Condition becomes true while room is occupied
        await coordinator._handle_activation_condition_change(
            _event(mock_hass, "binary_sensor.lux_dark", STATE_OFF, STATE_ON)
        )

        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")

    @pytest.mark.asyncio
    async def test_condition_met_while_empty_does_not_turn_on_lights(
        self, mock_hass, mock_config_entry_with_activation_condition
    ):
        """When room is empty and condition becomes true, lights should NOT turn on."""
        mock_hass.states.set("light.living_room", STATE_OFF)
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)  # Room is empty
        mock_hass.states.set("binary_sensor.lux_dark", STATE_OFF)

        coordinator = PresenceBasedLightingCoordinator(
            mock_hass, mock_config_entry_with_activation_condition
        )
        await coordinator.async_start()

        # Clear any prior calls
        mock_hass.services.calls.clear()

        # Condition becomes true but room is empty
        await coordinator._handle_activation_condition_change(
            _event(mock_hass, "binary_sensor.lux_dark", STATE_OFF, STATE_ON)
        )

        assert_service_not_called(mock_hass, "light", "turn_on")


class TestClearingIgnoresConditions:
    """Test that clearing happens regardless of activation conditions."""

    @pytest.mark.asyncio
    async def test_clearing_happens_when_condition_is_true(
        self, mock_hass, mock_config_entry_with_activation_condition
    ):
        """Lights should clear when presence clears, even if condition is still true."""
        mock_hass.states.set("light.living_room", STATE_ON)
        mock_hass.states.set("binary_sensor.motion", STATE_ON)
        mock_hass.states.set("binary_sensor.lux_dark", STATE_ON)

        coordinator = PresenceBasedLightingCoordinator(
            mock_hass, mock_config_entry_with_activation_condition
        )
        await coordinator.async_start()

        # Clear any prior calls
        mock_hass.services.calls.clear()

        # Presence clears
        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.motion", STATE_ON, STATE_OFF)
        )

        # Wait for timer
        await asyncio.sleep(1.1)

        assert_service_called(mock_hass, "light", "turn_off", "light.living_room")

    @pytest.mark.asyncio
    async def test_clearing_happens_when_condition_is_false(
        self, mock_hass, mock_config_entry_with_activation_condition
    ):
        """Lights should clear when presence clears, even if condition is false."""
        mock_hass.states.set("light.living_room", STATE_ON)
        mock_hass.states.set("binary_sensor.motion", STATE_ON)
        mock_hass.states.set("binary_sensor.lux_dark", STATE_OFF)  # Condition now false

        coordinator = PresenceBasedLightingCoordinator(
            mock_hass, mock_config_entry_with_activation_condition
        )
        await coordinator.async_start()

        # Clear any prior calls
        mock_hass.services.calls.clear()

        # Presence clears
        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.motion", STATE_ON, STATE_OFF)
        )

        # Wait for timer
        await asyncio.sleep(1.1)

        assert_service_called(mock_hass, "light", "turn_off", "light.living_room")


class TestMultipleActivationConditions:
    """Test AND logic with multiple activation conditions."""

    @pytest.mark.asyncio
    async def test_all_conditions_must_be_met(
        self, mock_hass, mock_config_entry_with_multiple_activation_conditions
    ):
        """Lights only turn on when ALL conditions are met."""
        mock_hass.states.set("light.living_room", STATE_OFF)
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("binary_sensor.lux_dark", STATE_ON)
        mock_hass.states.set("input_boolean.home_mode", STATE_OFF)  # One condition not met

        coordinator = PresenceBasedLightingCoordinator(
            mock_hass, mock_config_entry_with_multiple_activation_conditions
        )
        await coordinator.async_start()

        # Clear any prior calls
        mock_hass.services.calls.clear()

        # Presence detected but not all conditions met
        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.motion", STATE_OFF, STATE_ON)
        )

        assert_service_not_called(mock_hass, "light", "turn_on")

    @pytest.mark.asyncio
    async def test_all_conditions_met_turns_on(
        self, mock_hass, mock_config_entry_with_multiple_activation_conditions
    ):
        """Lights turn on when ALL conditions are met."""
        mock_hass.states.set("light.living_room", STATE_OFF)
        mock_hass.states.set("binary_sensor.motion", STATE_OFF)
        mock_hass.states.set("binary_sensor.lux_dark", STATE_ON)
        mock_hass.states.set("input_boolean.home_mode", STATE_ON)  # All conditions met

        coordinator = PresenceBasedLightingCoordinator(
            mock_hass, mock_config_entry_with_multiple_activation_conditions
        )
        await coordinator.async_start()

        # Clear any prior calls
        mock_hass.services.calls.clear()

        # Presence detected with all conditions met
        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.motion", STATE_OFF, STATE_ON)
        )

        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")

    @pytest.mark.asyncio
    async def test_last_condition_met_while_occupied_triggers(
        self, mock_hass, mock_config_entry_with_multiple_activation_conditions
    ):
        """When the last condition becomes true while occupied, lights turn on."""
        mock_hass.states.set("light.living_room", STATE_OFF)
        mock_hass.states.set("binary_sensor.motion", STATE_ON)  # Room is occupied
        mock_hass.states.set("binary_sensor.lux_dark", STATE_ON)
        mock_hass.states.set("input_boolean.home_mode", STATE_OFF)  # One condition not met

        coordinator = PresenceBasedLightingCoordinator(
            mock_hass, mock_config_entry_with_multiple_activation_conditions
        )
        await coordinator.async_start()

        # Clear any prior calls
        mock_hass.services.calls.clear()

        # Last condition becomes true
        await coordinator._handle_activation_condition_change(
            _event(mock_hass, "input_boolean.home_mode", STATE_OFF, STATE_ON)
        )

        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")


class TestNoActivationConditions:
    """Test backward compatibility when no activation conditions are configured."""

    @pytest.mark.asyncio
    async def test_no_conditions_behaves_as_before(self, mock_hass, mock_config_entry):
        """When no activation conditions configured, presence triggers lights immediately."""
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)

        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_presence_change(
            _event(mock_hass, "binary_sensor.living_room_motion", STATE_OFF, STATE_ON)
        )

        assert_service_called(mock_hass, "light", "turn_on", "light.living_room")
