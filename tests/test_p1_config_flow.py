"""Config flow regression tests for Presence Based Lighting."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

# Ensure HA stubs defined before importing the config flow
import tests.conftest  # noqa: F401

import pytest

from custom_components.presence_based_lighting.config_flow import (
    ACTION_ADD_ENTITY,
    ACTION_DELETE_ENTITIES,
    ACTION_EDIT_ENTITY,
    ACTION_NO_ACTION,
    FIELD_LANDING_ACTION,
    FIELD_PRESENCE_CLEARED_STATE_CUSTOM,
    FIELD_PRESENCE_DETECTED_STATE_CUSTOM,
    NO_ACTION,
    PresenceBasedLightingFlowHandler,
    STATE_OPTION_CUSTOM,
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
    option_values = [option["value"] for option in detected_selector["select"]["options"]]
    assert option_values[-1] == STATE_OPTION_CUSTOM
    assert "select" in cleared_selector
    assert all(
        getattr(field, "schema", None) != FIELD_PRESENCE_DETECTED_STATE_CUSTOM
        for field in schema.schema
    )
    assert all(
        getattr(field, "schema", None) != FIELD_PRESENCE_CLEARED_STATE_CUSTOM
        for field in schema.schema
    )


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
    "custom_components.presence_based_lighting.config_flow._async_get_history_states",
    new_callable=AsyncMock,
)
@patch(
    "custom_components.presence_based_lighting.config_flow._get_services_for_entity",
    return_value=SERVICE_OPTION_FIXTURE,
)
async def test_history_states_populate_dropdown_when_live_state_missing(_mock_services, mock_history):
    """Recorder history should seed dropdowns when present."""
    mock_history.return_value = ["occupied", "vacant"]

    handler = PresenceBasedLightingFlowHandler()
    handler.hass = MagicMock()
    handler.hass.states.get.return_value = None
    handler.async_show_form = MagicMock(return_value={"type": "form"})

    await handler.async_step_user(
        {
            CONF_ROOM_NAME: "Bedroom",
            CONF_PRESENCE_SENSORS: ["binary_sensor.bed_motion"],
            CONF_OFF_DELAY: DEFAULT_OFF_DELAY,
        }
    )
    await handler.async_step_select_entity({CONF_ENTITY_ID: "light.bedroom"})

    await handler.async_step_configure_entity()

    schema = handler.async_show_form.call_args.kwargs["data_schema"]
    detected_field = next(field for field in schema.schema if field.schema == CONF_PRESENCE_DETECTED_STATE)
    cleared_field = next(field for field in schema.schema if field.schema == CONF_PRESENCE_CLEARED_STATE)

    detected_selector = schema.schema[detected_field]
    cleared_selector = schema.schema[cleared_field]

    assert detected_selector == cleared_selector
    option_values = [option["value"] for option in detected_selector["select"]["options"]]
    assert option_values == [
        "occupied",
        "vacant",
        DEFAULT_DETECTED_STATE,
        DEFAULT_CLEARED_STATE,
        STATE_OPTION_CUSTOM,
    ]


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
    assert values == [DEFAULT_DETECTED_STATE, DEFAULT_CLEARED_STATE, STATE_OPTION_CUSTOM]
    assert all(
        getattr(field, "schema", None) != FIELD_PRESENCE_DETECTED_STATE_CUSTOM
        for field in schema.schema
    )
    assert all(
        getattr(field, "schema", None) != FIELD_PRESENCE_CLEARED_STATE_CUSTOM
        for field in schema.schema
    )


@pytest.mark.asyncio
@patch(
    "custom_components.presence_based_lighting.config_flow._get_services_for_entity",
    return_value=SERVICE_OPTION_FIXTURE,
)
async def test_configure_entity_saves_custom_state_when_selected(_mock_services):
    """Selecting Custom should persist the provided manual state."""
    handler = PresenceBasedLightingFlowHandler()
    handler.hass = MagicMock()
    state_obj = MagicMock()
    state_obj.attributes = {"friendly_name": "Office Lamp", "options": ["auto", "manual"]}
    state_obj.state = "auto"
    handler.hass.states.get.return_value = state_obj
    handler.async_show_form = MagicMock(return_value={"type": "form"})

    async def mock_manage():
        return "manage_step"

    handler.async_step_manage_entities = mock_manage

    await handler.async_step_user(
        {
            CONF_ROOM_NAME: "Office",
            CONF_PRESENCE_SENSORS: ["binary_sensor.office_motion"],
            CONF_OFF_DELAY: DEFAULT_OFF_DELAY,
        }
    )
    await handler.async_step_select_entity({CONF_ENTITY_ID: "light.office"})

    custom_input = _default_configure_input()
    custom_input[CONF_PRESENCE_DETECTED_STATE] = STATE_OPTION_CUSTOM
    custom_input[FIELD_PRESENCE_DETECTED_STATE_CUSTOM] = "dimmed"

    result = await handler.async_step_configure_entity(custom_input)

    assert result == "manage_step"
    saved = handler._controlled_entities[0]
    assert saved[CONF_PRESENCE_DETECTED_STATE] == "dimmed"
    assert saved[CONF_PRESENCE_CLEARED_STATE] == DEFAULT_CLEARED_STATE


@pytest.mark.asyncio
@patch(
    "custom_components.presence_based_lighting.config_flow._get_services_for_entity",
    return_value=SERVICE_OPTION_FIXTURE,
)
async def test_existing_custom_state_shows_text_field(_mock_services):
    """Editing an entity with an unknown state should display the custom input."""
    handler = PresenceBasedLightingFlowHandler()
    handler.hass = MagicMock()
    state_obj = MagicMock()
    state_obj.attributes = {"friendly_name": "Office Lamp", "options": ["on", "off"]}
    state_obj.state = "on"
    handler.hass.states.get.return_value = state_obj
    handler.async_show_form = MagicMock(return_value={"type": "form"})

    handler._current_entity_config = {  # type: ignore[attr-defined]
        CONF_ENTITY_ID: "light.office",
        CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
        CONF_PRESENCE_DETECTED_STATE: "dimmed",
        CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
        CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
        CONF_RESPECTS_PRESENCE_ALLOWED: DEFAULT_RESPECTS_PRESENCE_ALLOWED,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: False,
    }
    handler._selected_entity_id = "light.office"  # type: ignore[attr-defined]

    await handler.async_step_configure_entity()

    schema = handler.async_show_form.call_args.kwargs["data_schema"]
    detected_field = next(field for field in schema.schema if field.schema == CONF_PRESENCE_DETECTED_STATE)
    assert schema.schema[detected_field]["select"]["options"][-1]["value"] == STATE_OPTION_CUSTOM
    custom_field = next(
        field for field in schema.schema if getattr(field, "schema", None) == FIELD_PRESENCE_DETECTED_STATE_CUSTOM
    )
    assert schema.schema[custom_field]["text"]["multiline"] is False
    assert all(
        getattr(field, "schema", None) != FIELD_PRESENCE_CLEARED_STATE_CUSTOM
        for field in schema.schema
    )


@pytest.mark.asyncio
@patch(
    "custom_components.presence_based_lighting.config_flow._get_services_for_entity",
    return_value=SERVICE_OPTION_FIXTURE,
)
async def test_configure_entity_requires_custom_text_when_option_selected(_mock_services):
    """Custom selection without text should surface a validation error."""
    handler = PresenceBasedLightingFlowHandler()
    handler.hass = MagicMock()
    state_obj = MagicMock()
    state_obj.attributes = {"friendly_name": "Den Light", "options": ["auto", "manual"]}
    state_obj.state = "auto"
    handler.hass.states.get.return_value = state_obj
    handler.async_show_form = MagicMock(return_value={"type": "form"})

    await handler.async_step_user(
        {
            CONF_ROOM_NAME: "Den",
            CONF_PRESENCE_SENSORS: ["binary_sensor.den_motion"],
            CONF_OFF_DELAY: DEFAULT_OFF_DELAY,
        }
    )
    await handler.async_step_select_entity({CONF_ENTITY_ID: "light.den"})

    custom_input = _default_configure_input()
    custom_input[CONF_PRESENCE_DETECTED_STATE] = STATE_OPTION_CUSTOM

    result = await handler.async_step_configure_entity(custom_input)

    assert result == {"type": "form"}
    assert handler._errors == {FIELD_PRESENCE_DETECTED_STATE_CUSTOM: "custom_state_required"}
    schema = handler.async_show_form.call_args.kwargs["data_schema"]
    detected_field = next(field for field in schema.schema if field.schema == CONF_PRESENCE_DETECTED_STATE)
    assert schema.schema[detected_field]["select"]["options"][-1]["value"] == STATE_OPTION_CUSTOM
    custom_field = next(
        field for field in schema.schema if getattr(field, "schema", None) == FIELD_PRESENCE_DETECTED_STATE_CUSTOM
    )
    assert "text" in schema.schema[custom_field]
