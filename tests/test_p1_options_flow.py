"""Additional option flow coverage tests for Presence Based Lighting."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# Conftest already sets up all the necessary mocks, so we don't need to duplicate them here

from custom_components.presence_based_lighting.config_flow import (  # noqa: E402  # pylint: disable=wrong-import-position
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
async def test_options_flow_init_transitions_to_select_entity(mock_config_entry):
    """Test that init step transitions to select_entity step."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler._controlled_entities = [{"existing": True}]  # type: ignore[attr-defined]
    
    # Mock only the next step method
    async def mock_select_entity():
        return "select_entity_step"
    handler.async_step_select_entity = mock_select_entity

    user_input = {
        CONF_PRESENCE_SENSORS: ["binary_sensor.updated_motion"],
        CONF_OFF_DELAY: 10,
    }

    result = await handler.async_step_init(user_input)

    # Verify base data was updated
    assert handler._base_data[CONF_PRESENCE_SENSORS] == ["binary_sensor.updated_motion"]  # type: ignore[attr-defined]
    assert handler._base_data[CONF_OFF_DELAY] == 10  # type: ignore[attr-defined]

    # Verify entities list was preserved (NOT reset - this was the bug!)
    assert handler._controlled_entities == [{"existing": True}]  # type: ignore[attr-defined]
    assert handler._selected_entity_id is None  # type: ignore[attr-defined]
    
    # Verify it transitions to select_entity step
    assert result == "select_entity_step"


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
    handler.async_step_add_another = AsyncMock(return_value="add_another_form")
    
    configure_input = {
        CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
        CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
        CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
        CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
        CONF_RESPECTS_PRESENCE_ALLOWED: True,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: False,
    }
    
    result2 = await handler.async_step_configure_entity(configure_input)

    # Verify entity was added to controlled_entities list with all fields
    # Note: mock_config_entry has 1 existing entity, so adding another makes 2
    assert len(handler._controlled_entities) == 2  # type: ignore[attr-defined]
    stored_entity = handler._controlled_entities[1]  # type: ignore[attr-defined]  # Get the newly added one
    assert stored_entity[CONF_ENTITY_ID] == "light.new_entity"
    assert stored_entity[CONF_PRESENCE_DETECTED_SERVICE] == DEFAULT_DETECTED_SERVICE
    assert stored_entity[CONF_PRESENCE_CLEARED_SERVICE] == DEFAULT_CLEARED_SERVICE
    assert stored_entity[CONF_RESPECTS_PRESENCE_ALLOWED] is True
    assert stored_entity[CONF_INITIAL_PRESENCE_ALLOWED] == DEFAULT_INITIAL_PRESENCE_ALLOWED
    
    # Verify state was reset for next entity
    assert handler._selected_entity_id is None  # type: ignore[attr-defined]
    assert handler._current_entity_config == {}  # type: ignore[attr-defined]
    
    # Verify transition to add_another
    handler.async_step_add_another.assert_awaited_once()
    assert result2 == "add_another_form"
    
    # Step 3: Restore real method and finish flow (don't add another)
    handler.async_step_add_another = PresenceBasedLightingOptionsFlowHandler.async_step_add_another.__get__(handler)
    add_another_input = {"add_another": False}
    result3 = await handler.async_step_add_another(add_another_input)
    
    # Verify config entry was updated with the entities
    handler.hass.config_entries.async_update_entry.assert_called_once()
    update_call = handler.hass.config_entries.async_update_entry.call_args
    assert update_call[0][0] is mock_config_entry
    updated_data = update_call[1]["data"]
    assert CONF_CONTROLLED_ENTITIES in updated_data
    # Now we have both the original entity and the new one
    assert len(updated_data[CONF_CONTROLLED_ENTITIES]) == 2
    # Check that the new entity was added
    entity_ids = [e[CONF_ENTITY_ID] for e in updated_data[CONF_CONTROLLED_ENTITIES]]
    assert "light.new_entity" in entity_ids
    assert "light.living_room" in entity_ids  # Original entity preserved
    
    # Verify flow completed
    handler.async_create_entry.assert_called_once_with(title="", data={})
    assert result3 == {"type": "create_entry"}


@pytest.mark.asyncio
async def test_options_flow_requires_at_least_one_entity(mock_config_entry):
    """Test that finishing without adding any entities shows an error."""
    handler = PresenceBasedLightingOptionsFlowHandler(mock_config_entry)
    handler._controlled_entities = []  # type: ignore[attr-defined]
    handler.async_step_select_entity = AsyncMock(return_value="select_entity_step")

    # Try to finish without adding any entities
    result = await handler.async_step_add_another({"add_another": False})

    assert handler._errors["base"] == "no_controlled_entities"  # type: ignore[attr-defined]
    handler.async_step_select_entity.assert_awaited_once()
    assert result == "select_entity_step"
