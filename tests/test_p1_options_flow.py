"""Additional option flow coverage tests for Presence Based Lighting."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# Conftest already sets up all the necessary mocks, so we don't need to duplicate them here

from custom_components.presence_based_lighting.config_flow import (  # noqa: E402  # pylint: disable=wrong-import-position
    ACTION_FINISH,
    FIELD_MANAGE_ACTION,
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
async def test_options_flow_init_transitions_to_manage_entities(mock_config_entry):
    """Test that init step transitions to manage_entities view."""
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
async def test_options_flow_complete_multi_step_flow(mock_config_entry):
    """Test complete multi-step flow: select entity -> configure -> finish."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler.hass = MagicMock()
    handler.hass.config_entries = MagicMock()
    handler.hass.states = MagicMock()
    handler.hass.states.get = MagicMock(return_value=MagicMock(attributes={"friendly_name": "Living Room Light"}))
    handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    handler.async_show_form = MagicMock(return_value="form")  # Mock UI form display
    
    # Step 1: Call select_entity with valid input - should transition to configure_entity
    select_input = {CONF_ENTITY_ID: "light.new_entity"}
    # Mock only the next step to verify transition
    handler.async_step_configure_entity = AsyncMock(return_value="configure_form")
    result1 = await handler.async_step_select_entity(select_input)
    
    # Verify entity was selected and stored
    assert handler._selected_entity_id == "light.new_entity"  # type: ignore[attr-defined]
    assert handler._current_entity_config[CONF_ENTITY_ID] == "light.new_entity"  # type: ignore[attr-defined]
    handler.async_step_configure_entity.assert_awaited_once_with()
    assert result1 == "configure_form"
    
    # Step 2: Restore real method and call configure_entity with service selections
    handler.async_step_configure_entity = PresenceBasedLightingOptionsFlowHandler.async_step_configure_entity.__get__(handler)
    handler.async_step_manage_entities = AsyncMock(return_value="manage_form")

    configure_input = {
        CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
        CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
        CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
        CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
        CONF_RESPECTS_PRESENCE_ALLOWED: True,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: False,
    }

    result2 = await handler.async_step_configure_entity(configure_input)

    assert len(handler._controlled_entities) == 2  # type: ignore[attr-defined]
    stored_entity = handler._controlled_entities[1]  # type: ignore[attr-defined]
    assert stored_entity[CONF_ENTITY_ID] == "light.new_entity"
    assert stored_entity[CONF_PRESENCE_DETECTED_SERVICE] == DEFAULT_DETECTED_SERVICE
    assert stored_entity[CONF_PRESENCE_CLEARED_SERVICE] == DEFAULT_CLEARED_SERVICE
    assert stored_entity[CONF_RESPECTS_PRESENCE_ALLOWED] is True
    assert stored_entity[CONF_INITIAL_PRESENCE_ALLOWED] == DEFAULT_INITIAL_PRESENCE_ALLOWED
    assert handler._selected_entity_id is None  # type: ignore[attr-defined]
    assert handler._current_entity_config == {}  # type: ignore[attr-defined]
    handler.async_step_manage_entities.assert_awaited_once()
    assert result2 == "manage_form"

    # Step 3: Finish the flow via manage_entities
    handler.async_step_manage_entities = PresenceBasedLightingOptionsFlowHandler.async_step_manage_entities.__get__(handler)

    finish_input = {FIELD_MANAGE_ACTION: ACTION_FINISH}
    result3 = await handler.async_step_manage_entities(finish_input)

    handler.hass.config_entries.async_update_entry.assert_called_once()
    update_call = handler.hass.config_entries.async_update_entry.call_args
    assert update_call[0][0] is mock_config_entry
    updated_data = update_call[1]["data"]
    assert CONF_CONTROLLED_ENTITIES in updated_data
    assert len(updated_data[CONF_CONTROLLED_ENTITIES]) == 2
    entity_ids = [e[CONF_ENTITY_ID] for e in updated_data[CONF_CONTROLLED_ENTITIES]]
    assert "light.new_entity" in entity_ids
    assert "light.living_room" in entity_ids
    handler.async_create_entry.assert_called_once_with(title="", data={})
    assert result3 == {"type": "create_entry"}


@pytest.mark.asyncio
async def test_options_flow_requires_at_least_one_entity(mock_config_entry):
    """Test that finishing without adding any entities shows an error."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler._controlled_entities = []  # type: ignore[attr-defined]
    handler.async_show_form = MagicMock(return_value="manage_form")

    result = await handler.async_step_manage_entities({FIELD_MANAGE_ACTION: ACTION_FINISH})

    assert handler._errors["base"] == "no_controlled_entities"  # type: ignore[attr-defined]
    handler.async_show_form.assert_called_once()
    assert result == "manage_form"


@pytest.mark.asyncio
async def test_options_flow_loads_existing_entities_on_init(mock_config_entry):
    """Test that options flow loads existing entities from config entry on initialization."""
    # mock_config_entry fixture has 1 entity: light.living_room
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    
    # Verify existing entities were loaded
    assert len(handler._controlled_entities) == 1  # type: ignore[attr-defined]
    assert handler._controlled_entities[0][CONF_ENTITY_ID] == "light.living_room"  # type: ignore[attr-defined]
    assert handler._controlled_entities[0][CONF_PRESENCE_DETECTED_SERVICE] == DEFAULT_DETECTED_SERVICE  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_options_flow_preserves_entities_when_updating_base_settings(mock_config_entry):
    """Test that updating presence sensors and delay preserves existing entities.
    
    This is a regression test for the bug where async_step_init would wipe
    all entities when the user updated base settings.
    """
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    
    # Verify we start with 1 existing entity
    assert len(handler._controlled_entities) == 1  # type: ignore[attr-defined]
    original_entity = handler._controlled_entities[0]  # type: ignore[attr-defined]
    
    # Mock the next step
    async def mock_manage_entities():
        return "manage_entities_step"
    handler.async_step_manage_entities = mock_manage_entities
    
    # Update base settings (sensors and delay)
    user_input = {
        CONF_PRESENCE_SENSORS: ["binary_sensor.new_sensor_1", "binary_sensor.new_sensor_2"],
        CONF_OFF_DELAY: 30,
    }
    
    result = await handler.async_step_init(user_input)
    
    # Verify base settings were updated
    assert handler._base_data[CONF_PRESENCE_SENSORS] == ["binary_sensor.new_sensor_1", "binary_sensor.new_sensor_2"]  # type: ignore[attr-defined]
    assert handler._base_data[CONF_OFF_DELAY] == 30  # type: ignore[attr-defined]
    
    # CRITICAL: Verify existing entity was preserved (not wiped)
    assert len(handler._controlled_entities) == 1  # type: ignore[attr-defined]
    assert handler._controlled_entities[0] == original_entity  # type: ignore[attr-defined]
    assert handler._controlled_entities[0][CONF_ENTITY_ID] == "light.living_room"  # type: ignore[attr-defined]
    assert result == "manage_entities_step"


@pytest.mark.asyncio
async def test_options_flow_adds_new_entity_without_removing_existing(mock_config_entry):
    """Test that adding a new entity preserves existing entities.
    
    Verifies the complete flow: init (with existing) -> add new -> both present.
    """
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler.hass = MagicMock()
    handler.hass.config_entries = MagicMock()
    handler.hass.states = MagicMock()
    handler.hass.states.get = MagicMock(return_value=MagicMock(attributes={"friendly_name": "Bedroom Light"}))
    handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    handler.async_step_manage_entities = AsyncMock(return_value="manage_form")
    
    # Verify starting state: 1 entity (light.living_room from fixture)
    assert len(handler._controlled_entities) == 1  # type: ignore[attr-defined]
    assert handler._controlled_entities[0][CONF_ENTITY_ID] == "light.living_room"  # type: ignore[attr-defined]
    
    # Add a second entity
    handler._selected_entity_id = "light.bedroom"
    handler._current_entity_config = {CONF_ENTITY_ID: "light.bedroom"}
    
    configure_input = {
        CONF_PRESENCE_DETECTED_SERVICE: "turn_on",
        CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
        CONF_PRESENCE_DETECTED_STATE: "on",
        CONF_PRESENCE_CLEARED_STATE: "off",
        CONF_RESPECTS_PRESENCE_ALLOWED: True,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
    }
    
    await handler.async_step_configure_entity(configure_input)
    
    # Verify both entities are present
    assert len(handler._controlled_entities) == 2  # type: ignore[attr-defined]
    entity_ids = [e[CONF_ENTITY_ID] for e in handler._controlled_entities]  # type: ignore[attr-defined]
    assert "light.living_room" in entity_ids  # Original preserved
    assert "light.bedroom" in entity_ids  # New one added
    
    # Finish the flow via manage_entities
    handler.async_step_manage_entities = PresenceBasedLightingOptionsFlowHandler.async_step_manage_entities.__get__(handler)
    await handler.async_step_manage_entities({FIELD_MANAGE_ACTION: ACTION_FINISH})
    
    # Verify final data has both entities
    update_call = handler.hass.config_entries.async_update_entry.call_args
    updated_data = update_call[1]["data"]
    final_entity_ids = [e[CONF_ENTITY_ID] for e in updated_data[CONF_CONTROLLED_ENTITIES]]
    assert len(final_entity_ids) == 2
    assert "light.living_room" in final_entity_ids
    assert "light.bedroom" in final_entity_ids


@pytest.mark.asyncio
async def test_options_flow_delete_entity_from_manage(mock_config_entry):
    """Ensure entities can be removed from the manage view."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler.async_show_form = MagicMock(return_value="manage_form")

    assert len(handler._controlled_entities) == 1  # type: ignore[attr-defined]
    result = await handler.async_step_manage_entities({FIELD_MANAGE_ACTION: "delete:0"})
    assert len(handler._controlled_entities) == 0  # type: ignore[attr-defined]
    handler.async_show_form.assert_called_once()
    assert result == "manage_form"
