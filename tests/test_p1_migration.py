"""Migration tests for Presence Based Lighting v2 schema."""

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.presence_based_lighting import async_migrate_entry
from custom_components.presence_based_lighting.const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_INITIAL_PRESENCE_ALLOWED,
    CONF_LIGHT_ENTITIES,
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
    DEFAULT_DISABLE_ON_EXTERNAL,
    DEFAULT_INITIAL_PRESENCE_ALLOWED,
    DEFAULT_OFF_DELAY,
    DEFAULT_RESPECTS_PRESENCE_ALLOWED,
)


@pytest.mark.asyncio
async def test_migrate_populates_controlled_entities(mock_hass):
    entry = MagicMock()
    entry.entry_id = "abc123"
    entry.version = 1
    entry.data = {
        CONF_ROOM_NAME: "Living Room",
        CONF_LIGHT_ENTITIES: ["light.one", "light.two"],
        CONF_PRESENCE_SENSORS: ["binary_sensor.room"],
        CONF_OFF_DELAY: 10,
    }

    registry = MagicMock()
    registry.async_get_entity_id.return_value = "switch.presence_based_lighting_legacy"
    last_state = MagicMock()
    last_state.state = STATE_OFF

    with patch("custom_components.presence_based_lighting.er.async_get", return_value=registry), patch(
        "custom_components.presence_based_lighting.async_get_last_state",
        AsyncMock(return_value=last_state),
    ):
        assert await async_migrate_entry(mock_hass, entry) is True

    assert entry.version == 2
    registry.async_remove.assert_called_once_with("switch.presence_based_lighting_legacy")
    controlled = entry.data[CONF_CONTROLLED_ENTITIES]
    assert len(controlled) == 2
    for entity_id, cfg in zip(["light.one", "light.two"], controlled):
        assert cfg[CONF_ENTITY_ID] == entity_id
        assert cfg[CONF_INITIAL_PRESENCE_ALLOWED] is False
        assert cfg[CONF_PRESENCE_DETECTED_SERVICE] == DEFAULT_DETECTED_SERVICE
        assert cfg[CONF_PRESENCE_CLEARED_SERVICE] == DEFAULT_CLEARED_SERVICE
        assert cfg[CONF_PRESENCE_DETECTED_STATE] == DEFAULT_DETECTED_STATE
        assert cfg[CONF_PRESENCE_CLEARED_STATE] == DEFAULT_CLEARED_STATE
        assert cfg[CONF_RESPECTS_PRESENCE_ALLOWED] == DEFAULT_RESPECTS_PRESENCE_ALLOWED
        assert cfg[CONF_DISABLE_ON_EXTERNAL_CONTROL] == DEFAULT_DISABLE_ON_EXTERNAL


@pytest.mark.asyncio
async def test_migrate_without_legacy_switch_uses_defaults(mock_hass):
    entry = MagicMock()
    entry.entry_id = "xyz789"
    entry.version = 1
    entry.data = {
        CONF_ROOM_NAME: "Office",
        CONF_LIGHT_ENTITIES: ["light.office"],
        CONF_PRESENCE_SENSORS: ["binary_sensor.office"],
    }

    registry = MagicMock()
    registry.async_get_entity_id.return_value = None

    with patch("custom_components.presence_based_lighting.er.async_get", return_value=registry), patch(
        "custom_components.presence_based_lighting.async_get_last_state",
        AsyncMock(return_value=None),
    ):
        assert await async_migrate_entry(mock_hass, entry) is True

    controlled = entry.data[CONF_CONTROLLED_ENTITIES]
    assert controlled[0][CONF_INITIAL_PRESENCE_ALLOWED] == DEFAULT_INITIAL_PRESENCE_ALLOWED
    assert entry.data[CONF_OFF_DELAY] == DEFAULT_OFF_DELAY