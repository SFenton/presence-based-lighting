"""No-op service call behavior for per-entity presence automation."""

import asyncio

import pytest
from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.presence_based_lighting import PresenceBasedLightingCoordinator
from tests.conftest import setup_entity_states


def _service_event(domain, service, entity_id, context_id="external"):
    if isinstance(entity_id, list):
        entity_data = entity_id
    else:
        entity_data = entity_id
    return type(
        "Event",
        (),
        {
            "data": {
                "domain": domain,
                "service": service,
                "service_data": {"entity_id": entity_data},
            },
            "context": type("Ctx", (), {"id": context_id, "parent_id": None})(),
        },
    )()


class TestNoOpServiceCalls:
    """Verify service call monitoring respects per-entity automation flags."""

    entity = "light.living_room"

    @pytest.mark.asyncio
    async def test_external_turn_off_disables_presence(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_service_call(_service_event("light", "turn_off", [self.entity]))
        assert coordinator.get_presence_allowed(self.entity) is False

    @pytest.mark.asyncio
    async def test_external_turn_on_keeps_presence_allowed(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_service_call(_service_event("light", "turn_on", [self.entity]))
        assert coordinator.get_presence_allowed(self.entity) is True

    @pytest.mark.asyncio
    async def test_bulk_routine_disables_targeted_entity(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_service_call(
            _service_event("light", "turn_off", [self.entity, "light.kitchen", "light.hallway"])
        )
        assert coordinator.get_presence_allowed(self.entity) is False

    @pytest.mark.asyncio
    async def test_other_domains_ignored(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_service_call(_service_event("switch", "turn_off", "switch.living_room"))
        assert coordinator.get_presence_allowed(self.entity) is True

    @pytest.mark.asyncio
    async def test_other_entities_ignored(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_service_call(_service_event("light", "turn_off", "light.bedroom"))
        assert coordinator.get_presence_allowed(self.entity) is True

    @pytest.mark.asyncio
    async def test_manual_state_change_still_disables(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        mock_hass.states.set(
            self.entity,
            STATE_OFF,
            context=type("Ctx", (), {"id": "manual", "parent_id": None})(),
        )
        event = type(
            "Event",
            (),
            {
                "data": {
                    "entity_id": self.entity,
                    "old_state": type("State", (), {"state": STATE_ON, "context": None})(),
                    "new_state": mock_hass.states.get(self.entity),
                }
            },
        )()
        await coordinator._handle_controlled_entity_change(event)

        assert coordinator.get_presence_allowed(self.entity) is False

    @pytest.mark.asyncio
    async def test_integration_contexts_are_ignored(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        our_context = "integration_ctx"
        coordinator._entity_states[self.entity]["contexts"].append(our_context)

        await coordinator._handle_service_call(
            _service_event("light", "turn_off", self.entity, context_id=our_context)
        )
        assert coordinator.get_presence_allowed(self.entity) is True

    @pytest.mark.asyncio
    async def test_detected_service_resets_allowance(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_OFF, occupancy_state=STATE_OFF)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_service_call(_service_event("light", "turn_off", self.entity))
        assert coordinator.get_presence_allowed(self.entity) is False

        await coordinator._handle_service_call(_service_event("light", "turn_on", self.entity))
        assert coordinator.get_presence_allowed(self.entity) is True

    @pytest.mark.asyncio
    async def test_timer_turn_off_uses_own_context(self, mock_hass, mock_config_entry):
        setup_entity_states(mock_hass, lights_state=STATE_ON, occupancy_state=STATE_ON)
        coordinator = PresenceBasedLightingCoordinator(mock_hass, mock_config_entry)
        await coordinator.async_start()

        await coordinator._handle_presence_change(
            type(
                "Event",
                (),
                {
                    "data": {
                        "entity_id": "binary_sensor.living_room_motion",
                        "old_state": type("State", (), {"state": STATE_ON, "context": None})(),
                        "new_state": type("State", (), {"state": STATE_OFF, "context": None})(),
                    }
                },
            )()
        )

        await asyncio.sleep(1.1)
        assert coordinator.get_presence_allowed(self.entity) is True

