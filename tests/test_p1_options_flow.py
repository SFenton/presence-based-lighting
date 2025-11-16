"""Additional option flow coverage tests for Presence Based Lighting."""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol


def _ensure_module(name: str):
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        sys.modules[name] = module
    return module


# Ensure minimal Home Assistant stubs required by the config flow
homeassistant_root = _ensure_module("homeassistant")
helpers_root = _ensure_module("homeassistant.helpers")

config_entries_module = _ensure_module("homeassistant.config_entries")
config_entries_module.ConfigFlow = type("ConfigFlow", (), {})
config_entries_module.OptionsFlow = type("OptionsFlow", (), {})
setattr(homeassistant_root, "config_entries", config_entries_module)

cv_module = _ensure_module("homeassistant.helpers.config_validation")


def entity_id(value: str) -> str:
    if not isinstance(value, str) or "." not in value:
        raise vol.Invalid("invalid_entity")
    return value


cv_module.entity_id = entity_id
setattr(helpers_root, "config_validation", cv_module)

selector_module = _ensure_module("homeassistant.helpers.selector")


class _BaseSelectorConfig:
    def __init__(self, **kwargs):
        self.config = kwargs


class EntitySelectorConfig(_BaseSelectorConfig):
    pass


class EntitySelector:
    def __init__(self, config=None):
        self.config = config


class TextSelectorConfig(_BaseSelectorConfig):
    pass


class TextSelector:
    def __init__(self, config=None):
        self.config = config


class BooleanSelector:
    def __init__(self, config=None):
        self.config = config


class TextSelectorType:
    TEXT = "text"


selector_module.EntitySelector = EntitySelector
selector_module.EntitySelectorConfig = EntitySelectorConfig
selector_module.TextSelector = TextSelector
selector_module.TextSelectorConfig = TextSelectorConfig
selector_module.BooleanSelector = BooleanSelector
selector_module.TextSelectorType = TextSelectorType
setattr(helpers_root, "selector", selector_module)

from custom_components.presence_based_lighting.config_flow import (  # noqa: E402  # pylint: disable=wrong-import-position
    FIELD_ADD_ANOTHER,
    FIELD_SKIP_ENTITY,
    PresenceBasedLightingOptionsFlowHandler,
)
from custom_components.presence_based_lighting.const import (  # noqa: E402  # pylint: disable=wrong-import-position
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_OFF_DELAY,
    CONF_INITIAL_PRESENCE_ALLOWED,
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
    DEFAULT_INITIAL_PRESENCE_ALLOWED,
)


@pytest.mark.asyncio
async def test_options_flow_init_resets_defaults(mock_config_entry):
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler._controlled_entities = [{"existing": True}]  # type: ignore[attr-defined]
    handler._entity_defaults_queue = [{"queued": True}]  # type: ignore[attr-defined]
    handler._pending_entity_default = {"pending": True}  # type: ignore[attr-defined]

    handler.async_step_entities = AsyncMock(return_value="next-step")

    user_input = {
        CONF_PRESENCE_SENSORS: ["binary_sensor.updated_motion"],
        CONF_OFF_DELAY: 10,
    }

    result = await handler.async_step_init(user_input)

    assert handler._base_data[CONF_PRESENCE_SENSORS] == ["binary_sensor.updated_motion"]  # type: ignore[attr-defined]
    assert handler._base_data[CONF_OFF_DELAY] == 10  # type: ignore[attr-defined]
    assert handler._entity_defaults_queue == mock_config_entry.data[CONF_CONTROLLED_ENTITIES]  # type: ignore[attr-defined]
    assert handler._controlled_entities == []  # type: ignore[attr-defined]
    assert handler._pending_entity_default is None  # type: ignore[attr-defined]
    handler.async_step_entities.assert_awaited_once_with()
    assert result == "next-step"


@pytest.mark.asyncio
async def test_options_flow_entities_updates_entry(mock_config_entry):
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler.hass = MagicMock()
    handler.hass.config_entries = MagicMock()
    handler.async_create_entry = AsyncMock(return_value={"type": "create_entry"})
    handler._entity_defaults_queue = []  # type: ignore[attr-defined]
    handler._controlled_entities = []  # type: ignore[attr-defined]

    user_input = {
        CONF_ENTITY_ID: "light.new_entity",
        CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
        CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
        CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
        CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
        CONF_RESPECTS_PRESENCE_ALLOWED: True,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: False,
        FIELD_ADD_ANOTHER: False,
    }

    result = await handler.async_step_entities(user_input)

    expected_entity = {
        CONF_ENTITY_ID: "light.new_entity",
        CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
        CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
        CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
        CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
        CONF_RESPECTS_PRESENCE_ALLOWED: True,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: False,
        CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
    }

    stored_entity = handler._controlled_entities[0]  # type: ignore[attr-defined]
    assert stored_entity == expected_entity

    handler.hass.config_entries.async_update_entry.assert_called_once()
    update_call = handler.hass.config_entries.async_update_entry.call_args
    assert update_call[0][0] is mock_config_entry
    updated_data = update_call[1]["data"]
    assert updated_data[CONF_CONTROLLED_ENTITIES] == handler._controlled_entities  # type: ignore[attr-defined]

    handler.async_create_entry.assert_awaited_once_with(title="", data={})
    assert result == handler.async_create_entry.return_value


@pytest.mark.asyncio
async def test_options_flow_entities_require_entity_when_skipping(mock_config_entry):
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler._entity_defaults_queue = []  # type: ignore[attr-defined]
    handler._controlled_entities = []  # type: ignore[attr-defined]
    handler._show_entity_form = MagicMock(return_value="form")  # type: ignore[attr-defined]

    result = await handler.async_step_entities({FIELD_SKIP_ENTITY: True})

    assert handler._errors["base"] == "no_controlled_entities"  # type: ignore[attr-defined]
    handler._show_entity_form.assert_called_once()  # type: ignore[attr-defined]
    assert result == "form"
