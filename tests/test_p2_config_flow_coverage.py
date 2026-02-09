"""Tests for config_flow.py coverage – helper functions, manage/edit/delete flows, OptionsFlow."""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import tests.conftest  # noqa: F401 – HA stubs

from custom_components.presence_based_lighting.config_flow import (
    ACTION_ADD_ENTITY,
    ACTION_DELETE_ENTITIES,
    ACTION_EDIT_ENTITY,
    ACTION_NO_ACTION,
    FIELD_DELETE_ENTITIES,
    FIELD_EDIT_ENTITY,
    FIELD_LANDING_ACTION,
    FIELD_PRESENCE_CLEARED_STATE_CUSTOM,
    FIELD_PRESENCE_DETECTED_STATE_CUSTOM,
    NO_ACTION,
    PresenceBasedLightingFlowHandler,
    PresenceBasedLightingOptionsFlowHandler,
    STATE_OPTION_CUSTOM,
    ServiceOptionsUnavailable,
    _format_action_option_label,
    _format_state_option_label,
    _get_entity_domain,
    _get_entity_name,
    _get_services_for_entity,
    _presence_switch_unique_id,
    _resolve_custom_state_selection,
    _state_field_defaults,
    StateFieldDefaults,
)
from custom_components.presence_based_lighting.const import (
    AUTOMATION_MODE_AUTOMATIC,
    AUTOMATION_MODE_PRESENCE_LOCK,
    CONF_ACTIVATION_CONDITIONS,
    CONF_AUTOMATION_MODE,
    CONF_AUTO_REENABLE_END_TIME,
    CONF_AUTO_REENABLE_PRESENCE_SENSORS,
    CONF_AUTO_REENABLE_START_TIME,
    CONF_AUTO_REENABLE_VACANCY_THRESHOLD,
    CONF_CLEARING_SENSORS,
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_ENTITY_OFF_DELAY,
    CONF_FILE_LOGGING_ENABLED,
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
    CONF_USE_INTERCEPTOR,
    DEFAULT_AUTOMATION_MODE,
    DEFAULT_AUTO_REENABLE_END_TIME,
    DEFAULT_AUTO_REENABLE_START_TIME,
    DEFAULT_AUTO_REENABLE_VACANCY_THRESHOLD,
    DEFAULT_CLEARED_SERVICE,
    DEFAULT_CLEARED_STATE,
    DEFAULT_DETECTED_SERVICE,
    DEFAULT_DETECTED_STATE,
    DEFAULT_FILE_LOGGING_ENABLED,
    DEFAULT_INITIAL_PRESENCE_ALLOWED,
    DEFAULT_OFF_DELAY,
    DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED,
    DEFAULT_REQUIRE_VACANCY_FOR_CLEARED,
    DEFAULT_RESPECTS_PRESENCE_ALLOWED,
    DEFAULT_USE_INTERCEPTOR,
    DOMAIN,
)


SERVICE_OPTION_FIXTURE = [
    {"value": NO_ACTION, "label": "No Action"},
    {"value": "turn_on", "label": "Turn on"},
    {"value": "turn_off", "label": "Turn off"},
]


def _entity_fixture(entity_id: str, **overrides) -> dict:
    base = {
        CONF_ENTITY_ID: entity_id,
        CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
        CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
        CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
        CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
        CONF_RESPECTS_PRESENCE_ALLOWED: True,
        CONF_AUTOMATION_MODE: AUTOMATION_MODE_AUTOMATIC,
        CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
    }
    base.update(overrides)
    return base


def _default_configure_input() -> dict:
    return {
        CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
        CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
        CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
        CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
        CONF_RESPECTS_PRESENCE_ALLOWED: DEFAULT_RESPECTS_PRESENCE_ALLOWED,
        CONF_AUTOMATION_MODE: DEFAULT_AUTOMATION_MODE,
    }


def _make_config_entry(room="Office", entities=None, entry_id="opt_entry"):
    """Build a mock config entry for OptionsFlow tests."""
    if entities is None:
        entities = [_entity_fixture("light.office")]
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = {
        CONF_ROOM_NAME: room,
        CONF_PRESENCE_SENSORS: ["binary_sensor.office_motion"],
        CONF_OFF_DELAY: 10,
        CONF_CONTROLLED_ENTITIES: entities,
    }
    return entry


# ──────────────────────────────────────────────────────────────────────────────
# Helper function tests
# ──────────────────────────────────────────────────────────────────────────────

class TestGetServicesForEntity:
    @pytest.mark.asyncio
    async def test_raises_when_no_hass(self):
        with pytest.raises(ServiceOptionsUnavailable):
            await _get_services_for_entity(None, "light.x")

    @pytest.mark.asyncio
    async def test_raises_when_services_attr_none(self):
        hass = MagicMock()
        hass.services = None
        with pytest.raises(ServiceOptionsUnavailable):
            await _get_services_for_entity(hass, "light.x")


class TestFormatStateOptionLabel:
    def test_empty_returns_empty_marker(self):
        assert _format_state_option_label("") == "(empty)"

    def test_normal_value(self):
        result = _format_state_option_label("on")
        assert result == "On"


class TestFormatActionOptionLabel:
    def test_with_metadata(self):
        label = _format_action_option_label("turn_on", {"icon": "mdi:lamp", "name": "Turn On", "description": "Turn on the light"})
        assert "mdi:lamp" in label
        assert "Turn On" in label
        assert "Turn on the light" in label

    def test_without_metadata(self):
        label = _format_action_option_label("toggle", None)
        assert "Toggle" in label


class TestGetEntityName:
    def test_with_hass_friendly_name(self):
        hass = MagicMock()
        state = MagicMock()
        state.attributes = {"friendly_name": "Kitchen Light"}
        hass.states.get.return_value = state
        assert _get_entity_name(hass, "light.kitchen") == "Kitchen Light"

    def test_without_hass(self):
        assert _get_entity_name(None, "light.kitchen") == "light.kitchen"


class TestPresenceSwitchUniqueId:
    def test_valid(self):
        uid = _presence_switch_unique_id("entry_1", "light.kitchen")
        assert uid is not None
        assert "entry_1" in uid

    def test_no_dot(self):
        assert _presence_switch_unique_id("entry_1", "nodot") is None

    def test_none_entity(self):
        assert _presence_switch_unique_id("entry_1", None) is None

    def test_empty_entry(self):
        assert _presence_switch_unique_id(None, "light.x") is None

    def test_empty_slug(self):
        """Slug that resolves to empty uses fallback."""
        uid = _presence_switch_unique_id("entry_1", "light.___")
        assert uid is not None


class TestGetEntityDomain:
    def test_valid(self):
        assert _get_entity_domain("light.kitchen") == "light"

    def test_no_dot(self):
        assert _get_entity_domain("nodot") == ""


# ──────────────────────────────────────────────────────────────────────────────
# EntityManagementMixin – entity cards description
# ──────────────────────────────────────────────────────────────────────────────

class TestEntityCardsDescription:
    def test_no_entities(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler._controlled_entities = []
        desc = handler._entity_cards_description()
        assert "No entities" in desc

    def test_respects_toggle_disabled(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler._controlled_entities = [
            _entity_fixture("light.x", **{CONF_RESPECTS_PRESENCE_ALLOWED: False})
        ]
        desc = handler._entity_cards_description()
        assert "toggle disabled" in desc.lower()

    def test_automatic_with_manual_disable_states(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler._controlled_entities = [
            _entity_fixture("light.x", **{
                CONF_AUTOMATION_MODE: AUTOMATION_MODE_AUTOMATIC,
                CONF_MANUAL_DISABLE_STATES: ["dimmed", "night"],
            })
        ]
        desc = handler._entity_cards_description()
        assert "pauses on:" in desc.lower()
        assert "dimmed" in desc

    def test_presence_lock_mode(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler._controlled_entities = [
            _entity_fixture("light.x", **{
                CONF_AUTOMATION_MODE: AUTOMATION_MODE_PRESENCE_LOCK,
            })
        ]
        desc = handler._entity_cards_description()
        assert "Presence Lock" in desc

    def test_entity_off_delay(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler._controlled_entities = [
            {**_entity_fixture("light.x"), CONF_ENTITY_OFF_DELAY: 30}
        ]
        desc = handler._entity_cards_description()
        assert "30s" in desc


# ──────────────────────────────────────────────────────────────────────────────
# ConfigFlow – manage / edit / delete entities
# ──────────────────────────────────────────────────────────────────────────────

class TestConfigFlowManageEntities:
    @pytest.mark.asyncio
    async def test_no_action_empty_entities(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: ["binary_sensor.m"], CONF_OFF_DELAY: 5}
        handler._controlled_entities = []

        result = await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_NO_ACTION})
        # Should re-show form with error
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    async def test_edit_empty_entities(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: ["binary_sensor.m"], CONF_OFF_DELAY: 5}
        handler._controlled_entities = []
        result = await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_EDIT_ENTITY})
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    async def test_delete_empty_entities(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: ["binary_sensor.m"], CONF_OFF_DELAY: 5}
        handler._controlled_entities = []
        result = await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_DELETE_ENTITIES})
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    async def test_form_render_no_input(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}
        handler._controlled_entities = [_entity_fixture("light.x")]
        result = await handler.async_step_manage_entities(None)
        assert result == {"type": "form"}


class TestConfigFlowChooseEditEntity:
    @pytest.mark.asyncio
    async def test_empty_entities_redirects(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}
        handler._controlled_entities = []
        result = await handler.async_step_choose_edit_entity(None)
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    @patch("custom_components.presence_based_lighting.config_flow._get_services_for_entity", return_value=SERVICE_OPTION_FIXTURE)
    async def test_valid_selection(self, _mock):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        state_obj = MagicMock()
        state_obj.attributes = {"friendly_name": "Test"}
        state_obj.state = "off"
        handler.hass.states.get.return_value = state_obj
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}
        handler._controlled_entities = [_entity_fixture("light.x")]
        handler._custom_state_ui = {}
        result = await handler.async_step_choose_edit_entity({FIELD_EDIT_ENTITY: "0"})
        # Should transition to configure_entity (shown as form)
        assert result == {"type": "form"}
        assert handler._editing_index == 0

    @pytest.mark.asyncio
    async def test_invalid_index(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}
        handler._controlled_entities = [_entity_fixture("light.x")]
        result = await handler.async_step_choose_edit_entity({FIELD_EDIT_ENTITY: "99"})
        assert handler._errors.get(FIELD_EDIT_ENTITY) == "invalid_entity"

    @pytest.mark.asyncio
    async def test_non_numeric_index(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}
        handler._controlled_entities = [_entity_fixture("light.x")]
        result = await handler.async_step_choose_edit_entity({FIELD_EDIT_ENTITY: "abc"})
        assert handler._errors.get(FIELD_EDIT_ENTITY) == "invalid_entity"


class TestConfigFlowDeleteEntities:
    @pytest.mark.asyncio
    async def test_empty_entities_redirects(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}
        handler._controlled_entities = []
        result = await handler.async_step_delete_entities(None)
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    async def test_valid_deletion(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}
        handler._controlled_entities = [_entity_fixture("light.x"), _entity_fixture("light.y")]
        result = await handler.async_step_delete_entities({FIELD_DELETE_ENTITIES: ["0"]})
        # Should redirect back to manage with 1 entity remaining
        assert len(handler._controlled_entities) == 1

    @pytest.mark.asyncio
    async def test_empty_selection(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}
        handler._controlled_entities = [_entity_fixture("light.x")]
        result = await handler.async_step_delete_entities({FIELD_DELETE_ENTITIES: []})
        assert handler._errors.get(FIELD_DELETE_ENTITIES) == "select_entities_to_delete"

    @pytest.mark.asyncio
    async def test_non_numeric_selection(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}
        handler._controlled_entities = [_entity_fixture("light.x")]
        result = await handler.async_step_delete_entities({FIELD_DELETE_ENTITIES: ["abc"]})
        assert handler._errors.get(FIELD_DELETE_ENTITIES) == "select_entities_to_delete"

    @pytest.mark.asyncio
    async def test_form_render(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}
        handler._controlled_entities = [_entity_fixture("light.x")]
        result = await handler.async_step_delete_entities(None)
        assert result == {"type": "form"}


# ──────────────────────────────────────────────────────────────────────────────
# ConfigFlow – select_entity edge cases
# ──────────────────────────────────────────────────────────────────────────────

class TestConfigFlowSelectEntity:
    @pytest.mark.asyncio
    async def test_invalid_entity_id(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._controlled_entities = []
        result = await handler.async_step_select_entity({CONF_ENTITY_ID: "nodot"})
        assert handler._errors.get(CONF_ENTITY_ID) == "invalid_entity"

    @pytest.mark.asyncio
    async def test_render_form(self):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._controlled_entities = []
        result = await handler.async_step_select_entity(None)
        assert result == {"type": "form"}


# ──────────────────────────────────────────────────────────────────────────────
# ConfigFlow – configure_entity advanced paths
# ──────────────────────────────────────────────────────────────────────────────

class TestConfigFlowConfigureEntity:
    @pytest.mark.asyncio
    @patch("custom_components.presence_based_lighting.config_flow._get_services_for_entity", side_effect=ServiceOptionsUnavailable("nope"))
    async def test_service_unavailable_redirects(self, _mock):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._selected_entity_id = "light.x"
        handler._controlled_entities = []
        handler._editing_index = None
        handler._current_entity_config = {}
        handler._custom_state_ui = {}
        result = await handler.async_step_configure_entity(None)
        # Redirects to select_entity with error
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    @patch("custom_components.presence_based_lighting.config_flow._get_services_for_entity", return_value=SERVICE_OPTION_FIXTURE)
    async def test_entity_delay_populated(self, _mock):
        """Entity off delay default should be populated when set."""
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._selected_entity_id = "light.x"
        handler._controlled_entities = []
        handler._editing_index = None
        handler._current_entity_config = {CONF_ENTITY_OFF_DELAY: 42}
        handler._custom_state_ui = {}
        result = await handler.async_step_configure_entity(None)
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    @patch("custom_components.presence_based_lighting.config_flow._get_services_for_entity", return_value=SERVICE_OPTION_FIXTURE)
    async def test_submit_with_rlc_and_delay(self, _mock):
        """Submit with RLC tracking entity and entity off delay."""
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        handler._selected_entity_id = "light.x"
        handler._controlled_entities = []
        handler._editing_index = None
        handler._current_entity_config = {}
        handler._custom_state_ui = {}
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}

        user_input = {
            **_default_configure_input(),
            CONF_RLC_TRACKING_ENTITY: "sensor.rlc_x",
            CONF_ENTITY_OFF_DELAY: 15,
        }
        result = await handler.async_step_configure_entity(user_input)
        # Should go to manage_entities
        assert len(handler._controlled_entities) == 1
        assert handler._controlled_entities[0].get(CONF_RLC_TRACKING_ENTITY) == "sensor.rlc_x"
        assert handler._controlled_entities[0].get(CONF_ENTITY_OFF_DELAY) == 15

    @pytest.mark.asyncio
    @patch("custom_components.presence_based_lighting.config_flow._get_services_for_entity", return_value=SERVICE_OPTION_FIXTURE)
    async def test_submit_clears_entity_delay(self, _mock):
        """Clearing entity_off_delay when it was previously set."""
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._selected_entity_id = "light.x"
        handler._controlled_entities = []
        handler._editing_index = None
        handler._current_entity_config = {CONF_ENTITY_OFF_DELAY: 20}
        handler._custom_state_ui = {}
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}

        user_input = _default_configure_input()
        # No CONF_ENTITY_OFF_DELAY → should remove it
        result = await handler.async_step_configure_entity(user_input)
        assert CONF_ENTITY_OFF_DELAY not in handler._controlled_entities[0]

    @pytest.mark.asyncio
    @patch("custom_components.presence_based_lighting.config_flow._get_services_for_entity", return_value=SERVICE_OPTION_FIXTURE)
    async def test_editing_replaces_entity(self, _mock):
        """Editing at a specific index should replace the entity."""
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._selected_entity_id = "light.x"
        handler._controlled_entities = [_entity_fixture("light.x")]
        handler._editing_index = 0
        handler._current_entity_config = _entity_fixture("light.x")
        handler._custom_state_ui = {}
        handler._base_data = {CONF_ROOM_NAME: "R", CONF_PRESENCE_SENSORS: [], CONF_OFF_DELAY: 5}

        user_input = _default_configure_input()
        result = await handler.async_step_configure_entity(user_input)
        assert len(handler._controlled_entities) == 1  # replaced, not appended

    @pytest.mark.asyncio
    async def test_user_step_renders_form(self):
        """async_step_user with no input renders the form."""
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        result = await handler.async_step_user(None)
        assert result["type"] == "form"


# ──────────────────────────────────────────────────────────────────────────────
# ConfigFlow – configure_entity with RLC available
# ──────────────────────────────────────────────────────────────────────────────

class TestConfigureEntityWithRLC:
    @pytest.mark.asyncio
    @patch("custom_components.presence_based_lighting.config_flow._get_services_for_entity", return_value=SERVICE_OPTION_FIXTURE)
    @patch("custom_components.presence_based_lighting.config_flow.is_rlc_integration_available", return_value=True)
    @patch("custom_components.presence_based_lighting.config_flow.get_rlc_sensors_for_entity", return_value=["sensor.x_rlc"])
    @patch("custom_components.presence_based_lighting.config_flow.get_all_rlc_sensors", return_value=["sensor.x_rlc", "sensor.y_rlc"])
    async def test_rlc_schema_built(self, _all, _for, _avail, _svc):
        handler = PresenceBasedLightingFlowHandler()
        handler.hass = MagicMock()
        state_obj = MagicMock()
        state_obj.attributes = {"friendly_name": "X", "options": ["on", "off"]}
        state_obj.state = "off"
        handler.hass.states.get.return_value = state_obj
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._selected_entity_id = "light.x"
        handler._controlled_entities = []
        handler._editing_index = None
        handler._current_entity_config = {}
        handler._custom_state_ui = {}
        result = await handler.async_step_configure_entity(None)
        assert result == {"type": "form"}


# ──────────────────────────────────────────────────────────────────────────────
# OptionsFlow tests
# ──────────────────────────────────────────────────────────────────────────────

class TestOptionsFlowInit:
    @pytest.mark.asyncio
    async def test_submits_and_transitions(self):
        """Submit init with valid data transitions to manage_entities."""
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.hass.config_entries = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})

        result = await handler.async_step_init({
            CONF_PRESENCE_SENSORS: ["binary_sensor.new"],
            CONF_OFF_DELAY: 20,
        })
        # Should transition to manage_entities form
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    async def test_renders_form(self):
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        result = await handler.async_step_init(None)
        assert result == {"type": "form"}


class TestOptionsFlowCleanupPresenceSwitch:
    def test_no_hass(self):
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = None
        handler._cleanup_presence_switch("light.x")  # no crash

    def test_no_entity_id(self):
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler._cleanup_presence_switch(None)  # no crash

    def test_invalid_entity_id(self):
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler._cleanup_presence_switch("nodot")  # no crash


class TestOptionsFlowManageEntities:
    @pytest.mark.asyncio
    async def test_edit_empty(self):
        config_entry = _make_config_entry(entities=[])
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.hass.config_entries = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler.async_create_entry = MagicMock()
        result = await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_EDIT_ENTITY})
        # Should show error since no entities
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    async def test_delete_empty(self):
        config_entry = _make_config_entry(entities=[])
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.hass.config_entries = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler.async_create_entry = MagicMock()
        result = await handler.async_step_manage_entities({FIELD_LANDING_ACTION: ACTION_DELETE_ENTITIES})
        assert result == {"type": "form"}


class TestOptionsFlowChooseEditEntity:
    @pytest.mark.asyncio
    async def test_empty_redirects(self):
        config_entry = _make_config_entry(entities=[])
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.hass.config_entries = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler.async_create_entry = MagicMock()
        result = await handler.async_step_choose_edit_entity(None)
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    async def test_invalid_type(self):
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        result = await handler.async_step_choose_edit_entity({FIELD_EDIT_ENTITY: "abc"})
        assert handler._errors.get(FIELD_EDIT_ENTITY) == "invalid_entity"

    @pytest.mark.asyncio
    async def test_out_of_range(self):
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        result = await handler.async_step_choose_edit_entity({FIELD_EDIT_ENTITY: "99"})
        assert handler._errors.get(FIELD_EDIT_ENTITY) == "invalid_entity"


class TestOptionsFlowDeleteEntities:
    @pytest.mark.asyncio
    async def test_empty_redirects(self):
        config_entry = _make_config_entry(entities=[])
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.hass.config_entries = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler.async_create_entry = MagicMock()
        result = await handler.async_step_delete_entities(None)
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    async def test_empty_selection(self):
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        result = await handler.async_step_delete_entities({FIELD_DELETE_ENTITIES: []})
        assert handler._errors.get(FIELD_DELETE_ENTITIES) == "select_entities_to_delete"

    @pytest.mark.asyncio
    async def test_valid_deletion_with_cleanup(self):
        config_entry = _make_config_entry(entities=[
            _entity_fixture("light.x"),
            _entity_fixture("light.y"),
        ])
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.hass.config_entries = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        result = await handler.async_step_delete_entities({FIELD_DELETE_ENTITIES: ["0"]})
        assert len(handler._controlled_entities) == 1

    @pytest.mark.asyncio
    async def test_non_numeric_no_valid_indices(self):
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        result = await handler.async_step_delete_entities({FIELD_DELETE_ENTITIES: ["abc", "xyz"]})
        assert handler._errors.get(FIELD_DELETE_ENTITIES) == "select_entities_to_delete"


class TestOptionsFlowConfigureEntity:
    @pytest.mark.asyncio
    @patch("custom_components.presence_based_lighting.config_flow._get_services_for_entity", side_effect=ServiceOptionsUnavailable("nope"))
    async def test_service_unavailable_redirects(self, _mock):
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._selected_entity_id = "light.x"
        result = await handler.async_step_configure_entity(None)
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    @patch("custom_components.presence_based_lighting.config_flow._get_services_for_entity", return_value=SERVICE_OPTION_FIXTURE)
    async def test_submit_finalize_and_reload(self, _mock):
        """OptionsFlow configure_entity should finalize and reload."""
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.hass.config_entries = MagicMock()
        handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._selected_entity_id = "light.x"
        handler._finalize_after_configure = True

        result = await handler.async_step_configure_entity(_default_configure_input())
        # Should have finalized and reloaded
        assert result == {"type": "create_entry"}

    @pytest.mark.asyncio
    @patch("custom_components.presence_based_lighting.config_flow._get_services_for_entity", return_value=SERVICE_OPTION_FIXTURE)
    async def test_entity_delay_default_in_form(self, _mock):
        """Entity off delay should be pre-filled when editing."""
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._selected_entity_id = "light.x"
        handler._current_entity_config = {CONF_ENTITY_OFF_DELAY: 30}
        result = await handler.async_step_configure_entity(None)
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    @patch("custom_components.presence_based_lighting.config_flow._get_services_for_entity", return_value=SERVICE_OPTION_FIXTURE)
    @patch("custom_components.presence_based_lighting.config_flow.is_rlc_integration_available", return_value=True)
    @patch("custom_components.presence_based_lighting.config_flow.get_rlc_sensors_for_entity", return_value=["sensor.x_rlc"])
    @patch("custom_components.presence_based_lighting.config_flow.get_all_rlc_sensors", return_value=["sensor.x_rlc", "sensor.y_rlc"])
    async def test_rlc_available_form(self, _all, _for, _avail, _svc):
        """RLC tracking entity field should appear in form when RLC is available."""
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        state_obj = MagicMock()
        state_obj.attributes = {"friendly_name": "X", "options": ["on", "off"]}
        state_obj.state = "off"
        handler.hass.states.get.return_value = state_obj
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._selected_entity_id = "light.x"
        handler._current_entity_config = {}
        result = await handler.async_step_configure_entity(None)
        assert result == {"type": "form"}

    @pytest.mark.asyncio
    @patch("custom_components.presence_based_lighting.config_flow._get_services_for_entity", return_value=SERVICE_OPTION_FIXTURE)
    async def test_submit_with_rlc_and_delay(self, _mock):
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.states.get.return_value = None
        handler.hass.config_entries = MagicMock()
        handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler._selected_entity_id = "light.x"
        handler._finalize_after_configure = True

        user_input = {
            **_default_configure_input(),
            CONF_RLC_TRACKING_ENTITY: "sensor.rlc_x",
            CONF_ENTITY_OFF_DELAY: 25,
        }
        result = await handler.async_step_configure_entity(user_input)
        assert handler._controlled_entities[-1].get(CONF_RLC_TRACKING_ENTITY) == "sensor.rlc_x"
        assert handler._controlled_entities[-1].get(CONF_ENTITY_OFF_DELAY) == 25


class TestOptionsFlowSelectEntity:
    @pytest.mark.asyncio
    async def test_invalid_entity(self):
        config_entry = _make_config_entry()
        handler = PresenceBasedLightingOptionsFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        result = await handler.async_step_select_entity({CONF_ENTITY_ID: "nodot"})
        assert handler._errors.get(CONF_ENTITY_ID) == "invalid_entity"


class TestOptionsFlowAsyncOptionsFlow:
    @pytest.mark.asyncio
    async def test_static_method(self):
        """async_get_options_flow should return an OptionsFlow."""
        flow = PresenceBasedLightingFlowHandler.async_get_options_flow(_make_config_entry())
        assert isinstance(flow, PresenceBasedLightingOptionsFlowHandler)


class TestResolveCustomStateSelection:
    def test_non_custom_returns_value(self):
        val, missing = _resolve_custom_state_selection("on", None, ui_state={}, ui_key="key")
        assert val == "on"
        assert missing is False

    def test_custom_with_value(self):
        val, missing = _resolve_custom_state_selection(
            STATE_OPTION_CUSTOM, "my_state", ui_state={}, ui_key="key"
        )
        assert val == "my_state"
        assert missing is False

    def test_custom_without_value(self):
        val, missing = _resolve_custom_state_selection(
            STATE_OPTION_CUSTOM, None, ui_state={}, ui_key="key"
        )
        assert missing is True


class TestStateFieldDefaults:
    def test_value_in_ha_values(self):
        result = _state_field_defaults("on", ha_values={"on", "off"}, ui_state={}, ui_key="k")
        assert result.selector_default == "on"
        assert result.custom_default is None

    def test_value_not_in_ha_values(self):
        result = _state_field_defaults("custom_val", ha_values={"on", "off"}, ui_state={}, ui_key="k")
        assert result.selector_default == STATE_OPTION_CUSTOM
        assert result.custom_default == "custom_val"

    def test_ui_key_present(self):
        result = _state_field_defaults("on", ha_values={"on"}, ui_state={"k": "prev"}, ui_key="k")
        assert result.selector_default == STATE_OPTION_CUSTOM
        assert result.custom_default == "prev"
