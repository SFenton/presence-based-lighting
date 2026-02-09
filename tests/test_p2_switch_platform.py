"""Tests for switch.py – PresenceEntitySwitch and AutoReEnableSwitch."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
from homeassistant.const import STATE_ON, STATE_OFF

from custom_components.presence_based_lighting.const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_DISABLE_ON_EXTERNAL_CONTROL,
    CONF_ENTITY_ID,
    CONF_INITIAL_PRESENCE_ALLOWED,
    CONF_RESPECTS_PRESENCE_ALLOWED,
    CONF_ROOM_NAME,
    DOMAIN,
    ICON,
    ICON_AUTO_REENABLE,
)
from custom_components.presence_based_lighting.switch import (
    async_setup_entry,
    PresenceEntitySwitch,
    AutoReEnableSwitch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(mock_hass, entry, paused=False, presence_allowed=True, automation_state="idle"):
    """Return a mock coordinator with the methods that switches call."""
    coord = MagicMock()
    coord.hass = mock_hass
    coord.entry = entry
    coord.get_presence_allowed = MagicMock(return_value=presence_allowed)
    coord.async_set_presence_allowed = AsyncMock()
    coord.get_automation_paused = MagicMock(return_value=paused)
    coord.get_entity_automation_state = MagicMock(return_value=automation_state)
    coord.register_presence_switch = MagicMock(return_value=lambda: None)
    coord.get_auto_reenable_tracking_info = MagicMock(return_value={
        "is_tracking": False,
        "vacancy_threshold_percent": 80,
    })
    coord.set_auto_reenable_enabled = MagicMock()
    return coord


def _make_entry(room="Living Room"):
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {
        CONF_ROOM_NAME: room,
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: "light.living_room",
                CONF_RESPECTS_PRESENCE_ALLOWED: True,
                CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
                CONF_INITIAL_PRESENCE_ALLOWED: True,
            }
        ],
    }
    return entry


def _make_entity_config(entity_id="light.living_room"):
    return {
        CONF_ENTITY_ID: entity_id,
        CONF_RESPECTS_PRESENCE_ALLOWED: True,
        CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
        CONF_INITIAL_PRESENCE_ALLOWED: True,
    }


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_creates_switches_and_calls_add_entities(self):
        hass = MagicMock()
        entry = _make_entry()
        coord = _make_coordinator(hass, entry)
        hass.data = {DOMAIN: {entry.entry_id: coord}}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        add_entities.assert_called_once()
        entities = add_entities.call_args[0][0]
        # One PresenceEntitySwitch per entity + one AutoReEnableSwitch
        assert len(entities) == 2
        assert isinstance(entities[0], PresenceEntitySwitch)
        assert isinstance(entities[1], AutoReEnableSwitch)

    @pytest.mark.asyncio
    async def test_multiple_entities(self):
        hass = MagicMock()
        entry = _make_entry()
        entry.data[CONF_CONTROLLED_ENTITIES].append(_make_entity_config("light.kitchen"))
        coord = _make_coordinator(hass, entry)
        hass.data = {DOMAIN: {entry.entry_id: coord}}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        entities = add_entities.call_args[0][0]
        assert len(entities) == 3  # 2 presence switches + 1 auto-reenable


# ---------------------------------------------------------------------------
# PresenceEntitySwitch
# ---------------------------------------------------------------------------

class TestPresenceEntitySwitch:
    def test_init_sets_basic_attrs(self):
        entry = _make_entry()
        hass = MagicMock()
        coord = _make_coordinator(hass, entry)
        cfg = _make_entity_config()

        switch = PresenceEntitySwitch(coord, entry, cfg)

        assert switch._entity_id == "light.living_room"
        assert "Presence Allowed" in switch._attr_name
        assert switch._attr_icon == ICON
        assert entry.entry_id in switch._attr_unique_id

    def test_format_switch_name(self):
        entry = _make_entry(room="Office")
        coord = _make_coordinator(MagicMock(), entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())
        name = switch._format_switch_name("light.desk_lamp")
        assert "Office" in name
        assert "light.desk_lamp" in name

    def test_device_info(self):
        entry = _make_entry(room="Bedroom")
        coord = _make_coordinator(MagicMock(), entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())
        info = switch.device_info
        assert (DOMAIN, entry.entry_id) in info["identifiers"]
        assert "Bedroom" in info["name"]

    def test_is_on_delegates_to_coordinator(self):
        entry = _make_entry()
        hass = MagicMock()
        coord = _make_coordinator(hass, entry, presence_allowed=True)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())
        assert switch.is_on is True

        coord.get_presence_allowed.return_value = False
        assert switch.is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_on(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())

        await switch.async_turn_on()

        coord.async_set_presence_allowed.assert_awaited_once_with("light.living_room", True)

    @pytest.mark.asyncio
    async def test_async_turn_off(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())

        await switch.async_turn_off()

        coord.async_set_presence_allowed.assert_awaited_once_with("light.living_room", False)

    def test_extra_state_attributes(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry, paused=True, automation_state="PAUSED")
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())

        attrs = switch.extra_state_attributes
        assert attrs["controlled_entity"] == "light.living_room"
        assert attrs[CONF_RESPECTS_PRESENCE_ALLOWED] is True
        assert attrs[CONF_DISABLE_ON_EXTERNAL_CONTROL] is True
        assert attrs["automation_paused"] is True
        assert attrs["automation_state"] == "PAUSED"

    def test_derive_friendly_name_from_state(self):
        """Friendly name from hass.states."""
        entry = _make_entry()
        hass = MagicMock()
        state_obj = MagicMock()
        state_obj.attributes = {"friendly_name": "Living Room Lamp"}
        hass.states.get.return_value = state_obj

        coord = _make_coordinator(hass, entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())
        assert switch._derive_target_friendly_name() == "Living Room Lamp"

    def test_derive_friendly_name_from_registry(self):
        """Friendly name falls through to entity registry."""
        entry = _make_entry()
        hass = MagicMock()
        # hass.states returns None for state
        hass.states.get.return_value = None

        coord = _make_coordinator(hass, entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())
        # No hass attribute → falls through to slugified fallback
        switch.hass = None
        name = switch._derive_target_friendly_name()
        # Last part of entity_id, title-cased
        assert name == "Living Room"

    def test_derive_friendly_name_fallback(self):
        """Friendly name last resort: entity_id slug."""
        entry = _make_entry()
        hass = MagicMock()
        hass.states = None  # no states object

        coord = _make_coordinator(hass, entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config("light.desk_lamp"))
        switch.hass = None
        name = switch._derive_target_friendly_name()
        assert name == "Desk Lamp"

    def test_desired_entity_id(self):
        entry = _make_entry(room="Office")
        coord = _make_coordinator(MagicMock(), entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())
        eid = switch._desired_entity_id("Desk Lamp")
        assert eid.startswith("switch.")
        assert "office" in eid.lower()

    @pytest.mark.asyncio
    async def test_async_added_to_hass_with_last_state_on(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())

        last_state = MagicMock()
        last_state.state = STATE_ON

        with patch.object(switch, "async_get_last_state", new_callable=AsyncMock, return_value=last_state):
            switch.hass = MagicMock()
            switch._attr_entity_id = None
            await switch.async_added_to_hass()

        coord.register_presence_switch.assert_called_once()
        _, initial_state, _ = coord.register_presence_switch.call_args[0]
        assert initial_state is True

    @pytest.mark.asyncio
    async def test_async_added_to_hass_with_last_state_off(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())

        last_state = MagicMock()
        last_state.state = STATE_OFF

        with patch.object(switch, "async_get_last_state", new_callable=AsyncMock, return_value=last_state):
            switch.hass = MagicMock()
            switch._attr_entity_id = None
            await switch.async_added_to_hass()

        _, initial_state, _ = coord.register_presence_switch.call_args[0]
        assert initial_state is False

    @pytest.mark.asyncio
    async def test_async_added_to_hass_no_last_state(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())

        with patch.object(switch, "async_get_last_state", new_callable=AsyncMock, return_value=None):
            switch.hass = MagicMock()
            switch._attr_entity_id = None
            await switch.async_added_to_hass()

        _, initial_state, _ = coord.register_presence_switch.call_args[0]
        assert initial_state is True  # default from config

    @pytest.mark.asyncio
    async def test_async_will_remove_from_hass(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())

        remove_fn = MagicMock()
        switch._remove_listener = remove_fn

        await switch.async_will_remove_from_hass()

        remove_fn.assert_called_once()
        assert switch._remove_listener is None

    @pytest.mark.asyncio
    async def test_async_will_remove_from_hass_no_listener(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())
        switch._remove_listener = None

        await switch.async_will_remove_from_hass()  # should not raise

    def test_handle_coordinator_update(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())
        switch.async_write_ha_state = MagicMock()

        switch._handle_coordinator_update()
        switch.async_write_ha_state.assert_called_once()

    def test_update_display_metadata_no_hass(self):
        """_update_display_metadata with no hass set should still update name."""
        entry = _make_entry()
        hass = MagicMock()
        hass.states.get.return_value = None
        coord = _make_coordinator(hass, entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())
        switch.hass = None
        switch._update_display_metadata()
        # Should have set name without crashing
        assert switch._attr_name is not None

    def test_derive_friendly_name_from_registry_entry_name(self):
        """Friendly name from entity registry entry.name."""
        from tests.conftest import _MockRegistryEntry, _MockEntityRegistry
        entry = _make_entry()
        hass = MagicMock()
        hass.states.get.return_value = None  # No state
        coord = _make_coordinator(hass, entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())
        # Set up entity registry with name
        reg = _MockEntityRegistry()
        reg._entries["light.living_room"] = _MockRegistryEntry(
            entity_id="light.living_room", name="My Lamp"
        )
        switch.hass = MagicMock()
        switch.hass._entity_registry = reg
        name = switch._derive_target_friendly_name()
        assert name == "My Lamp"

    def test_derive_friendly_name_from_registry_original_name(self):
        """Friendly name from entity registry entry.original_name when name is None."""
        from tests.conftest import _MockRegistryEntry, _MockEntityRegistry
        entry = _make_entry()
        hass = MagicMock()
        hass.states.get.return_value = None
        coord = _make_coordinator(hass, entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())
        reg = _MockEntityRegistry()
        reg._entries["light.living_room"] = _MockRegistryEntry(
            entity_id="light.living_room", name=None, original_name="Original Lamp"
        )
        switch.hass = MagicMock()
        switch.hass._entity_registry = reg
        name = switch._derive_target_friendly_name()
        assert name == "Original Lamp"

    def test_update_display_metadata_renames_entity(self):
        """_update_display_metadata renames via registry when entity_id differs."""
        from tests.conftest import _MockRegistryEntry, _MockEntityRegistry
        entry = _make_entry(room="Office")
        hass = MagicMock()
        hass.states.get.return_value = None
        coord = _make_coordinator(hass, entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())

        reg = _MockEntityRegistry()
        reg._entries["light.living_room"] = _MockRegistryEntry(
            entity_id="light.living_room", name=None
        )
        reg.async_update_entity = MagicMock()
        # Give switch an entity_id so the rename path is hit
        switch.hass = MagicMock()
        switch.hass._entity_registry = reg
        switch.entity_id = "switch.old_id"
        reg._entries["switch.old_id"] = _MockRegistryEntry(
            entity_id="switch.old_id", name=None
        )
        switch._update_display_metadata()
        # Registry rename should have been attempted
        reg.async_update_entity.assert_called_once()

    def test_update_display_metadata_rename_value_error(self):
        """_update_display_metadata gracefully handles ValueError on rename."""
        from tests.conftest import _MockRegistryEntry, _MockEntityRegistry
        entry = _make_entry(room="Office")
        hass = MagicMock()
        hass.states.get.return_value = None
        coord = _make_coordinator(hass, entry)
        switch = PresenceEntitySwitch(coord, entry, _make_entity_config())

        reg = _MockEntityRegistry()
        reg.async_update_entity = MagicMock(side_effect=ValueError("conflict"))
        switch.hass = MagicMock()
        switch.hass._entity_registry = reg
        switch.entity_id = "switch.old_id"
        reg._entries["switch.old_id"] = _MockRegistryEntry(
            entity_id="switch.old_id", name=None
        )
        # Should not raise
        switch._update_display_metadata()


# ---------------------------------------------------------------------------
# AutoReEnableSwitch
# ---------------------------------------------------------------------------

class TestAutoReEnableSwitch:
    def test_init(self):
        entry = _make_entry(room="Bedroom")
        coord = _make_coordinator(MagicMock(), entry)
        switch = AutoReEnableSwitch(coord, entry)

        assert "Auto Re-Enable" in switch._attr_name
        assert "Bedroom" in switch._attr_name
        assert switch._attr_icon == ICON_AUTO_REENABLE
        assert switch._is_on is False

    def test_device_info(self):
        entry = _make_entry(room="Kitchen")
        coord = _make_coordinator(MagicMock(), entry)
        switch = AutoReEnableSwitch(coord, entry)
        info = switch.device_info
        assert (DOMAIN, entry.entry_id) in info["identifiers"]
        assert "Kitchen" in info["name"]

    def test_is_on(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = AutoReEnableSwitch(coord, entry)
        assert switch.is_on is False
        switch._is_on = True
        assert switch.is_on is True

    def test_extra_state_attributes(self):
        entry = _make_entry(room="Den")
        coord = _make_coordinator(MagicMock(), entry)
        switch = AutoReEnableSwitch(coord, entry)
        attrs = switch.extra_state_attributes
        assert attrs["room"] == "Den"
        assert "is_tracking" in attrs  # from tracking info

    def test_extra_state_attributes_no_tracking_method(self):
        entry = _make_entry(room="Den")
        coord = MagicMock(spec=[])  # no attributes at all
        switch = AutoReEnableSwitch(coord, entry)
        attrs = switch.extra_state_attributes
        assert attrs["room"] == "Den"

    @pytest.mark.asyncio
    async def test_async_turn_on(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = AutoReEnableSwitch(coord, entry)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        assert switch._is_on is True
        coord.set_auto_reenable_enabled.assert_called_once_with(True)
        switch.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_off(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = AutoReEnableSwitch(coord, entry)
        switch._is_on = True
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()

        assert switch._is_on is False
        coord.set_auto_reenable_enabled.assert_called_once_with(False)

    @pytest.mark.asyncio
    async def test_async_added_to_hass_restores_on(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = AutoReEnableSwitch(coord, entry)

        last_state = MagicMock()
        last_state.state = STATE_ON

        with patch.object(switch, "async_get_last_state", new_callable=AsyncMock, return_value=last_state):
            await switch.async_added_to_hass()

        assert switch._is_on is True
        coord.set_auto_reenable_enabled.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_async_added_to_hass_restores_off(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = AutoReEnableSwitch(coord, entry)

        last_state = MagicMock()
        last_state.state = STATE_OFF

        with patch.object(switch, "async_get_last_state", new_callable=AsyncMock, return_value=last_state):
            await switch.async_added_to_hass()

        assert switch._is_on is False
        coord.set_auto_reenable_enabled.assert_called_once_with(False)

    @pytest.mark.asyncio
    async def test_async_added_to_hass_no_last_state(self):
        entry = _make_entry()
        coord = _make_coordinator(MagicMock(), entry)
        switch = AutoReEnableSwitch(coord, entry)

        with patch.object(switch, "async_get_last_state", new_callable=AsyncMock, return_value=None):
            await switch.async_added_to_hass()

        assert switch._is_on is False
        coord.set_auto_reenable_enabled.assert_called_once_with(False)

    @pytest.mark.asyncio
    async def test_turn_on_off_without_coordinator_methods(self):
        """Switches shouldn't crash if coordinator lacks auto-reenable methods."""
        entry = _make_entry()
        coord = MagicMock(spec=[])  # no set_auto_reenable_enabled
        switch = AutoReEnableSwitch(coord, entry)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()
        assert switch._is_on is True

        await switch.async_turn_off()
        assert switch._is_on is False
