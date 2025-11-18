"""Config flow regression tests for Presence Based Lighting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.presence_based_lighting.config_flow import (
    ACTION_ADD_ENTITY,
    ACTION_DELETE_ENTITIES,
    ACTION_EDIT_ENTITY,
    ACTION_NO_ACTION,
    FIELD_LANDING_ACTION,
    NO_ACTION,
    PresenceBasedLightingFlowHandler,
    STEP_CHOOSE_EDIT_ENTITY,
    STEP_DELETE_ENTITIES,
    STEP_SELECT_ENTITY,
)
from custom_components.presence_based_lighting.const import (
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
    CONF_RESPECTS_PRESENCE_ALLOWED,
    CONF_ROOM_NAME,
    DEFAULT_CLEARED_SERVICE,
    DEFAULT_CLEARED_STATE,
    DEFAULT_DETECTED_SERVICE,
    DEFAULT_DETECTED_STATE,
    DEFAULT_INITIAL_PRESENCE_ALLOWED,
    DEFAULT_OFF_DELAY,
    DEFAULT_RESPECTS_PRESENCE_ALLOWED,
)


SERVICE_OPTION_FIXTURE = [
    {"value": NO_ACTION, "label": "No Action"},
    {"value": "turn_on", "label": "Turn on"},
    {"value": "turn_off", "label": "Turn off"},
]


def _default_configure_input() -> dict:
    return {
        CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
        CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
        CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
        CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
        CONF_RESPECTS_PRESENCE_ALLOWED: DEFAULT_RESPECTS_PRESENCE_ALLOWED,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: False,
    }


def _entity_fixture(entity_id: str) -> dict:
    return {
        CONF_ENTITY_ID: entity_id,
        CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
        CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
        CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
        CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
        CONF_RESPECTS_PRESENCE_ALLOWED: True,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: False,
        CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
    }


@pytest.mark.asyncio
@patch(
    "custom_components.presence_based_lighting.config_flow._get_services_for_entity",
    return_value=SERVICE_OPTION_FIXTURE,
)
async def test_configure_entity_transitions_back_to_manage(_mock_services):
    """Completing entity configuration should return the manage view."""
    handler = PresenceBasedLightingFlowHandler()
    handler.hass = MagicMock()
    state_obj = MagicMock()
    state_obj.attributes = {"friendly_name": "Kitchen Light", "options": ["on", "off"]}
    state_obj.state = "off"
    handler.hass.states.get.return_value = state_obj
    handler.async_show_form = MagicMock(return_value={"type": "form"})

    async def mock_manage():
        return "manage_step"

    handler.async_step_manage_entities = mock_manage

    await handler.async_step_user(
        {
            CONF_ROOM_NAME: "Kitchen",
            CONF_PRESENCE_SENSORS: ["binary_sensor.kitchen_motion"],
            CONF_OFF_DELAY: DEFAULT_OFF_DELAY,
        }
    )
    await handler.async_step_select_entity({CONF_ENTITY_ID: "light.kitchen"})

    result = await handler.async_step_configure_entity(_default_configure_input())

    assert result == "manage_step"
    assert handler._controlled_entities[0][CONF_ENTITY_ID] == "light.kitchen"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_manage_entities_creates_entry_when_ready():
    """Submitting from the manage step should create the entry when data exists."""
    handler = PresenceBasedLightingFlowHandler()
    handler.hass = MagicMock()
    handler.async_show_form = MagicMock(return_value={"type": "form"})
    handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    handler._base_data = {  # type: ignore[attr-defined]
        CONF_ROOM_NAME: "Office",
        CONF_PRESENCE_SENSORS: ["binary_sensor.office_motion"],
        CONF_OFF_DELAY: 15,
    }
    handler._controlled_entities = [_entity_fixture("light.office")]  # type: ignore[attr-defined]

    result = await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_NO_ACTION})

    expected_payload = {
        CONF_ROOM_NAME: "Office",
        CONF_PRESENCE_SENSORS: ["binary_sensor.office_motion"],
        CONF_OFF_DELAY: 15,
        CONF_CONTROLLED_ENTITIES: handler._controlled_entities,  # type: ignore[attr-defined]
    }
    handler.async_create_entry.assert_called_once_with(title="Office", data=expected_payload)
    assert result == {"type": "create_entry"}


@pytest.mark.asyncio
async def test_manage_entities_routes_to_edit_delete_and_add():
    """Manage step should branch to edit, delete, and add entity flows."""
    handler = PresenceBasedLightingFlowHandler()
    handler.hass = MagicMock()
    handler.async_show_form = MagicMock(return_value="form")
    handler._base_data = {  # type: ignore[attr-defined]
        CONF_ROOM_NAME: "Hallway",
        CONF_PRESENCE_SENSORS: ["binary_sensor.hall_motion"],
        CONF_OFF_DELAY: 20,
    }
    handler._controlled_entities = [  # type: ignore[attr-defined]
        _entity_fixture("light.hallway_1"),
        _entity_fixture("light.hallway_2"),
    ]

    result_edit = await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_EDIT_ENTITY})
    assert result_edit == "form"
    handler.async_show_form.assert_called_once()
    assert handler.async_show_form.call_args.kwargs["step_id"] == STEP_CHOOSE_EDIT_ENTITY

    handler.async_show_form.reset_mock()
    result_delete = await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_DELETE_ENTITIES})
    assert result_delete == "form"
    handler.async_show_form.assert_called_once()
    assert handler.async_show_form.call_args.kwargs["step_id"] == STEP_DELETE_ENTITIES

    handler.async_show_form.reset_mock()
    result_add = await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_ADD_ENTITY})
    assert result_add == "form"
    handler.async_show_form.assert_called_once()
    assert handler.async_show_form.call_args.kwargs["step_id"] == STEP_SELECT_ENTITY
    assert handler._selected_entity_id is None  # type: ignore[attr-defined]
    assert handler._current_entity_config == {}  # type: ignore[attr-defined]


@pytest.mark.asyncio
@patch(
    "custom_components.presence_based_lighting.config_flow._get_services_for_entity",
    return_value=SERVICE_OPTION_FIXTURE,
)
async def test_configure_entity_uses_state_dropdown_when_options_available(_mock_services):
    """State fields should render dropdown selectors when HA exposes options."""
    handler = PresenceBasedLightingFlowHandler()
    handler.hass = MagicMock()
    state_obj = MagicMock()
    state_obj.attributes = {
        "friendly_name": "Hallway Light",
        "options": ["auto", "manual"],
    }
    state_obj.state = "auto"
    handler.hass.states.get.return_value = state_obj
    handler.async_show_form = MagicMock(return_value={"type": "form"})

    await handler.async_step_user(
        {
            CONF_ROOM_NAME: "Hallway",
            CONF_PRESENCE_SENSORS: ["binary_sensor.hallway_motion"],
            CONF_OFF_DELAY: DEFAULT_OFF_DELAY,
        }
    )
    await handler.async_step_select_entity({CONF_ENTITY_ID: "light.hallway"})

    await handler.async_step_configure_entity()

    schema = handler.async_show_form.call_args.kwargs["data_schema"]
    detected_field = next(field for field in schema.schema if field.schema == CONF_PRESENCE_DETECTED_STATE)
    cleared_field = next(field for field in schema.schema if field.schema == CONF_PRESENCE_CLEARED_STATE)

    detected_selector = schema.schema[detected_field]
    cleared_selector = schema.schema[cleared_field]

    assert "select" in detected_selector
    assert detected_selector["select"]["options"][0]["value"] == "auto"
    assert "select" in cleared_selector


@pytest.mark.asyncio
@patch(
    "custom_components.presence_based_lighting.config_flow._get_services_for_entity",
    return_value=SERVICE_OPTION_FIXTURE,
)
async def test_configure_entity_falls_back_to_text_when_no_state_options(_mock_services):
    """State inputs should fall back to text fields when HA provides no options."""
    handler = PresenceBasedLightingFlowHandler()
    handler.hass = MagicMock()
    handler.hass.states.get.return_value = None
    handler.async_show_form = MagicMock(return_value={"type": "form"})

    await handler.async_step_user(
        {
            CONF_ROOM_NAME: "Hallway",
            CONF_PRESENCE_SENSORS: ["binary_sensor.hallway_motion"],
            CONF_OFF_DELAY: DEFAULT_OFF_DELAY,
        }
    )
    await handler.async_step_select_entity({CONF_ENTITY_ID: "light.hallway"})

    await handler.async_step_configure_entity()

    schema = handler.async_show_form.call_args.kwargs["data_schema"]
    detected_field = next(field for field in schema.schema if field.schema == CONF_PRESENCE_DETECTED_STATE)
    cleared_field = next(field for field in schema.schema if field.schema == CONF_PRESENCE_CLEARED_STATE)

    assert schema.schema[detected_field] is str
    assert schema.schema[cleared_field] is str


@pytest.mark.asyncio
@patch(
    "custom_components.presence_based_lighting.config_flow._get_services_for_entity",
    return_value=SERVICE_OPTION_FIXTURE,
)
async def test_state_dropdown_includes_defaults_for_both_fields(_mock_services):
    """Dropdowns should expose both configured states even if HA only reports one."""
    handler = PresenceBasedLightingFlowHandler()
    handler.hass = MagicMock()
    state_obj = MagicMock()
    state_obj.attributes = {"friendly_name": "Porch Light"}
    state_obj.state = "on"
    handler.hass.states.get.return_value = state_obj
    handler.async_show_form = MagicMock(return_value={"type": "form"})

    await handler.async_step_user(
        {
            CONF_ROOM_NAME: "Porch",
            CONF_PRESENCE_SENSORS: ["binary_sensor.porch_motion"],
            CONF_OFF_DELAY: DEFAULT_OFF_DELAY,
        }
    )
    await handler.async_step_select_entity({CONF_ENTITY_ID: "light.porch"})

    await handler.async_step_configure_entity()

    schema = handler.async_show_form.call_args.kwargs["data_schema"]
    detected_field = next(field for field in schema.schema if field.schema == CONF_PRESENCE_DETECTED_STATE)
    cleared_field = next(field for field in schema.schema if field.schema == CONF_PRESENCE_CLEARED_STATE)

    detected_selector = schema.schema[detected_field]
    cleared_selector = schema.schema[cleared_field]

    assert detected_selector == cleared_selector
    values = [option["value"] for option in detected_selector["select"]["options"]]
    assert values == [DEFAULT_DETECTED_STATE, DEFAULT_CLEARED_STATE]
