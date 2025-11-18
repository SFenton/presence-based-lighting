"""Additional option flow coverage tests for Presence Based Lighting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.presence_based_lighting.config_flow import (  # noqa: E402  # pylint: disable=wrong-import-position
    ACTION_ADD_ENTITY,
    ACTION_DELETE_ENTITIES,
    ACTION_EDIT_ENTITY,
    ACTION_NO_ACTION,
    FIELD_DELETE_ENTITIES,
    FIELD_EDIT_ENTITY,
    FIELD_LANDING_ACTION,
    NO_ACTION,
    PresenceBasedLightingOptionsFlowHandler,
    _get_services_for_entity,
)
from custom_components.presence_based_lighting.const import (  # noqa: E402  # pylint: disable=wrong-import-position
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_OFF_DELAY,
    CONF_PRESENCE_CLEARED_SERVICE,
    CONF_PRESENCE_CLEARED_STATE,
    CONF_PRESENCE_DETECTED_SERVICE,
    CONF_PRESENCE_DETECTED_STATE,
    CONF_PRESENCE_SENSORS,
    CONF_RESPECTS_PRESENCE_ALLOWED,
    DEFAULT_CLEARED_SERVICE,
    DEFAULT_CLEARED_STATE,
    DEFAULT_DETECTED_SERVICE,
    DEFAULT_DETECTED_STATE,
)


def _default_configure_input():
    return {
        CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
        CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
        CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
        CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
        CONF_RESPECTS_PRESENCE_ALLOWED: True,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: False,
    }


@pytest.mark.asyncio
async def test_options_flow_init_transitions_to_manage_entities(mock_config_entry):
    """The init step should transition to the manage step with updated base data."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler._controlled_entities = [{"existing": True}]  # type: ignore[attr-defined]

    async def mock_manage():
        return "manage_entities_step"

    handler.async_step_manage_entities = mock_manage

    user_input = {
        CONF_PRESENCE_SENSORS: ["binary_sensor.updated_motion"],
        CONF_OFF_DELAY: 10,
    }

    result = await handler.async_step_init(user_input)

    assert handler._base_data[CONF_PRESENCE_SENSORS] == ["binary_sensor.updated_motion"]  # type: ignore[attr-defined]
    assert handler._base_data[CONF_OFF_DELAY] == 10  # type: ignore[attr-defined]
    assert handler._controlled_entities == [{"existing": True}]  # type: ignore[attr-defined]
    assert handler._selected_entity_id is None  # type: ignore[attr-defined]
    assert result == "manage_entities_step"


@pytest.mark.asyncio
async def test_landing_requires_entities_before_submit(mock_config_entry):
    """Selecting submit with no entities should surface an error."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    assert hasattr(handler, "_finalize_and_reload")
    handler._controlled_entities = []  # type: ignore[attr-defined]
    handler.async_show_form = MagicMock(return_value="manage_form")

    result = await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_NO_ACTION})

    assert handler._errors["base"] == "no_controlled_entities"  # type: ignore[attr-defined]
    handler.async_show_form.assert_called_once()
    assert result == "manage_form"


@pytest.mark.asyncio
async def test_landing_submit_finalizes_changes(mock_config_entry):
    """Choosing the No Action path should finalize immediately when entities exist."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler.hass = MagicMock()
    handler.hass.config_entries = MagicMock()
    handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    result = await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_NO_ACTION})

    handler.hass.config_entries.async_update_entry.assert_called_once()
    handler.async_create_entry.assert_called_once_with(title="", data={})
    assert result == {"type": "create_entry"}


@pytest.mark.asyncio
async def test_choose_edit_entity_updates_existing_and_finalizes(mock_config_entry):
    """Edit flow should let the user update an entity and then finalize the entry."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler.hass = MagicMock()
    handler.hass.config_entries = MagicMock()
    handler.hass.states = MagicMock()
    handler.hass.states.get = MagicMock(return_value=MagicMock(attributes={"friendly_name": "Living Room Light"}))
    handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    handler.async_show_form = MagicMock(return_value="form")

    await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_EDIT_ENTITY})
    await handler.async_step_choose_edit_entity({FIELD_EDIT_ENTITY: "0"})

    configure_input = {
        CONF_PRESENCE_DETECTED_SERVICE: "turn_on",
        CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
        CONF_PRESENCE_DETECTED_STATE: "on",
        CONF_PRESENCE_CLEARED_STATE: "off",
        CONF_RESPECTS_PRESENCE_ALLOWED: False,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
    }

    result = await handler.async_step_configure_entity(configure_input)

    handler.hass.config_entries.async_update_entry.assert_called_once()
    handler.async_create_entry.assert_called_once()
    updated_entity = handler._controlled_entities[0]  # type: ignore[attr-defined]
    assert updated_entity[CONF_PRESENCE_DETECTED_SERVICE] == "turn_on"
    assert result == {"type": "create_entry"}


@pytest.mark.asyncio
async def test_add_new_entity_from_landing(mock_config_entry):
    """Add flow should collect entity info and finalize with the new card."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler.hass = MagicMock()
    handler.hass.config_entries = MagicMock()
    handler.hass.states = MagicMock()
    handler.hass.states.get = MagicMock(return_value=MagicMock(attributes={"friendly_name": "Bedroom Light"}))
    handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    handler.async_show_form = MagicMock(return_value="form")
    result = await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_ADD_ENTITY})
    assert result == "form"

    await handler.async_step_select_entity({CONF_ENTITY_ID: "light.bedroom"})
    await handler.async_step_configure_entity(_default_configure_input())

    entity_ids = [e[CONF_ENTITY_ID] for e in handler._controlled_entities]  # type: ignore[attr-defined]
    assert "light.bedroom" in entity_ids
    handler.hass.config_entries.async_update_entry.assert_called_once()
    handler.async_create_entry.assert_called_once()


@pytest.mark.asyncio
async def test_delete_entities_flow_removes_selected(mock_config_entry_multi):
    """Deleting multiple entities should persist the pruned list and close the flow."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry_multi)
    handler.hass = MagicMock()
    handler.hass.config_entries = MagicMock()
    handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    handler.async_show_form = MagicMock(return_value="form")

    registry = MagicMock()
    registry.async_get_entity_id.return_value = "switch.living_room_presence_toggle"
    with patch("custom_components.presence_based_lighting.config_flow.er.async_get", return_value=registry) as mock_get:
        await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_DELETE_ENTITIES})
        await handler.async_step_delete_entities({FIELD_DELETE_ENTITIES: ["0"]})

    mock_get.assert_called_once_with(handler.hass)
    registry.async_get_entity_id.assert_called_once_with(
        "switch",
        "presence_based_lighting",
        "test_entry_id_living_room_1_presence_allowed",
    )
    registry.async_remove.assert_called_once_with("switch.living_room_presence_toggle")

    remaining_ids = [e[CONF_ENTITY_ID] for e in handler._controlled_entities]  # type: ignore[attr-defined]
    assert remaining_ids == ["light.living_room_2"]
    handler.hass.config_entries.async_update_entry.assert_called_once()
    handler.async_create_entry.assert_called_once()


@pytest.mark.asyncio
async def test_options_flow_loads_existing_entities_on_init(mock_config_entry):
    """Options flow should load existing entities immediately."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    assert len(handler._controlled_entities) == 1  # type: ignore[attr-defined]
    assert handler._controlled_entities[0][CONF_ENTITY_ID] == "light.living_room"  # type: ignore[attr-defined]
    assert handler._controlled_entities[0][CONF_PRESENCE_DETECTED_SERVICE] == DEFAULT_DETECTED_SERVICE  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_options_flow_preserves_entities_when_updating_base_settings(mock_config_entry):
    """Updating presence sensors and delay should not drop entities."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    assert len(handler._controlled_entities) == 1  # type: ignore[attr-defined]
    original_entity = handler._controlled_entities[0]  # type: ignore[attr-defined]

    async def mock_manage_entities():
        return "manage_entities_step"

    handler.async_step_manage_entities = mock_manage_entities

    user_input = {
        CONF_PRESENCE_SENSORS: ["binary_sensor.new_sensor_1", "binary_sensor.new_sensor_2"],
        CONF_OFF_DELAY: 30,
    }

    result = await handler.async_step_init(user_input)

    assert handler._base_data[CONF_PRESENCE_SENSORS] == ["binary_sensor.new_sensor_1", "binary_sensor.new_sensor_2"]  # type: ignore[attr-defined]
    assert handler._base_data[CONF_OFF_DELAY] == 30  # type: ignore[attr-defined]
    assert handler._controlled_entities[0] == original_entity  # type: ignore[attr-defined]
    assert result == "manage_entities_step"


def test_get_services_for_entity_uses_service_descriptions():
    """Action dropdown should leverage HA metadata including icon/title/description."""

    class DummyServices:
        def async_get_all_descriptions(self):
            return {
                "light": {
                    "pulse": {
                        "name": "Pulse",
                        "description": "Flash the entity briefly",
                        "icon": "mdi:flash",
                    }
                }
            }

    hass = MagicMock()
    hass.services = DummyServices()

    options = _get_services_for_entity(hass, "light.kitchen")

    assert options[0]["value"] == NO_ACTION
    assert options[1]["value"] == "pulse"
    label = options[1]["label"]
    assert "[mdi:flash]" in label
    assert "Pulse" in label
    assert "Flash the entity briefly" in label
