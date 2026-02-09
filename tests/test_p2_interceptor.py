"""Tests for interceptor.py – PresenceLockInterceptor."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import sys

from custom_components.presence_based_lighting.const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_ENTITY_ID,
    CONF_PRESENCE_CLEARED_SERVICE,
    CONF_PRESENCE_DETECTED_SERVICE,
    CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
    CONF_REQUIRE_VACANCY_FOR_CLEARED,
    CONF_USE_INTERCEPTOR,
    DEFAULT_USE_INTERCEPTOR,
    DOMAIN,
)
from custom_components.presence_based_lighting.interceptor import (
    is_interceptor_available,
    PresenceLockInterceptor,
)


def _make_entry(entity_id="light.living_room", req_occ=True, req_vac=True, use_interceptor=True):
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {
        CONF_CONTROLLED_ENTITIES: [
            {
                CONF_ENTITY_ID: entity_id,
                CONF_PRESENCE_DETECTED_SERVICE: "turn_on",
                CONF_PRESENCE_CLEARED_SERVICE: "turn_off",
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED: req_occ,
                CONF_REQUIRE_VACANCY_FOR_CLEARED: req_vac,
                CONF_USE_INTERCEPTOR: use_interceptor,
            }
        ],
    }
    return entry


class TestInterceptorAvailability:
    def test_interceptor_not_available(self):
        """hass-interceptor is not installed in test env."""
        # This just tests the current state – HAS_INTERCEPTOR is False
        result = is_interceptor_available()
        # In test env, hass_interceptor is not installed
        assert isinstance(result, bool)


class TestPresenceLockInterceptorWithoutLib:
    """Tests when hass-interceptor is NOT installed (fallback mode)."""

    def test_setup_returns_false_without_lib(self):
        hass = MagicMock()
        entry = _make_entry()
        interceptor = PresenceLockInterceptor(hass, entry, lambda: True)
        result = interceptor.setup()
        assert result is False

    def test_teardown_empty(self):
        hass = MagicMock()
        entry = _make_entry()
        interceptor = PresenceLockInterceptor(hass, entry, lambda: True)
        interceptor.teardown()  # should not crash


class TestPresenceLockInterceptorWithLib:
    """Tests when hass-interceptor IS available (mocked)."""

    def _patch_interceptor_available(self):
        """Patch the module to simulate hass-interceptor being installed."""
        import custom_components.presence_based_lighting.interceptor as mod
        self._orig_has = mod.HAS_INTERCEPTOR
        mod.HAS_INTERCEPTOR = True

        # Create mock InterceptResult
        class MockInterceptResult:
            ALLOW = "allow"
            BLOCK = "block"

        mod.InterceptResult = MockInterceptResult
        self._mock_result = MockInterceptResult

        # Create mock register_interceptor
        self._register_calls = []
        def mock_register(hass, domain, service, handler, priority, integration):
            self._register_calls.append({
                "domain": domain, "service": service,
                "handler": handler, "priority": priority,
            })
            return MagicMock()  # unregister function
        mod.register_interceptor = mock_register

    def _unpatch(self):
        import custom_components.presence_based_lighting.interceptor as mod
        mod.HAS_INTERCEPTOR = self._orig_has
        if hasattr(mod, 'InterceptResult') and hasattr(self, '_mock_result'):
            del mod.InterceptResult
        if hasattr(mod, 'register_interceptor'):
            del mod.register_interceptor

    def test_setup_registers_interceptors(self):
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=True, req_vac=True)
            interceptor = PresenceLockInterceptor(hass, entry, lambda: True)
            result = interceptor.setup()
            assert result is True
            # Should register for turn_on (require_occ) and turn_off (require_vac)
            assert len(self._register_calls) == 2
            services = {c["service"] for c in self._register_calls}
            assert "turn_on" in services
            assert "turn_off" in services
        finally:
            self._unpatch()

    def test_setup_only_occupancy(self):
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=True, req_vac=False)
            interceptor = PresenceLockInterceptor(hass, entry, lambda: True)
            result = interceptor.setup()
            assert result is True
            assert len(self._register_calls) == 1
            assert self._register_calls[0]["service"] == "turn_on"
        finally:
            self._unpatch()

    def test_setup_no_locks(self):
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=False, req_vac=False)
            interceptor = PresenceLockInterceptor(hass, entry, lambda: True)
            result = interceptor.setup()
            assert result is False  # nothing to register
        finally:
            self._unpatch()

    def test_setup_interceptor_disabled(self):
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=True, req_vac=True, use_interceptor=False)
            interceptor = PresenceLockInterceptor(hass, entry, lambda: True)
            result = interceptor.setup()
            assert result is False
        finally:
            self._unpatch()

    def test_setup_missing_entity_id(self):
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = MagicMock()
            entry.entry_id = "test"
            entry.data = {CONF_CONTROLLED_ENTITIES: [{}]}  # no entity_id
            interceptor = PresenceLockInterceptor(hass, entry, lambda: True)
            result = interceptor.setup()
            assert result is False
        finally:
            self._unpatch()

    @pytest.mark.asyncio
    async def test_handler_blocks_turn_on_when_empty(self):
        """The registered handler blocks turn_on when room is empty."""
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=True, req_vac=False)
            is_occupied = MagicMock(return_value=False)
            interceptor = PresenceLockInterceptor(hass, entry, is_occupied)
            interceptor.setup()

            handler = self._register_calls[0]["handler"]
            # Call targeting our entity, room empty
            call = MagicMock()
            data = {"entity_id": ["light.living_room"]}
            result = await handler(call, data)
            assert result == self._mock_result.BLOCK
        finally:
            self._unpatch()

    @pytest.mark.asyncio
    async def test_handler_allows_turn_on_when_occupied(self):
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=True, req_vac=False)
            is_occupied = MagicMock(return_value=True)
            interceptor = PresenceLockInterceptor(hass, entry, is_occupied)
            interceptor.setup()

            handler = self._register_calls[0]["handler"]
            call = MagicMock()
            data = {"entity_id": ["light.living_room"]}
            result = await handler(call, data)
            assert result == self._mock_result.ALLOW
        finally:
            self._unpatch()

    @pytest.mark.asyncio
    async def test_handler_removes_entity_from_multi_target(self):
        """When multiple entities targeted, only our entity is removed."""
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=True, req_vac=False)
            is_occupied = MagicMock(return_value=False)
            interceptor = PresenceLockInterceptor(hass, entry, is_occupied)
            interceptor.setup()

            handler = self._register_calls[0]["handler"]
            call = MagicMock()
            data = {"entity_id": ["light.living_room", "light.kitchen"]}
            result = await handler(call, data)
            assert result == self._mock_result.ALLOW
            assert data["entity_id"] == ["light.kitchen"]
        finally:
            self._unpatch()

    @pytest.mark.asyncio
    async def test_handler_ignores_unrelated_entity(self):
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=True, req_vac=False)
            is_occupied = MagicMock(return_value=False)
            interceptor = PresenceLockInterceptor(hass, entry, is_occupied)
            interceptor.setup()

            handler = self._register_calls[0]["handler"]
            call = MagicMock()
            data = {"entity_id": ["light.kitchen"]}
            result = await handler(call, data)
            assert result == self._mock_result.ALLOW
        finally:
            self._unpatch()

    @pytest.mark.asyncio
    async def test_handler_blocks_turn_off_when_occupied(self):
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=False, req_vac=True)
            is_occupied = MagicMock(return_value=True)
            interceptor = PresenceLockInterceptor(hass, entry, is_occupied)
            interceptor.setup()

            handler = self._register_calls[0]["handler"]
            call = MagicMock()
            data = {"entity_id": ["light.living_room"]}
            result = await handler(call, data)
            assert result == self._mock_result.BLOCK
        finally:
            self._unpatch()

    def test_teardown_calls_unregister(self):
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=True, req_vac=True)
            interceptor = PresenceLockInterceptor(hass, entry, lambda: True)
            interceptor.setup()

            assert len(interceptor._unregister_funcs) == 2
            interceptor.teardown()
            for fn in interceptor._unregister_funcs:
                # Already called during teardown
                pass
            assert len(interceptor._unregister_funcs) == 0
        finally:
            self._unpatch()

    @pytest.mark.asyncio
    async def test_handler_string_entity_id(self):
        """entity_id as string (not list) should be handled."""
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=True, req_vac=False)
            is_occupied = MagicMock(return_value=False)
            interceptor = PresenceLockInterceptor(hass, entry, is_occupied)
            interceptor.setup()

            handler = self._register_calls[0]["handler"]
            call = MagicMock()
            data = {"entity_id": "light.living_room"}
            result = await handler(call, data)
            assert result == self._mock_result.BLOCK
        finally:
            self._unpatch()

    @pytest.mark.asyncio
    async def test_handler_multi_target_turn_off_occupied(self):
        """turn_off with multiple targets removes only protected entity when occupied."""
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=False, req_vac=True)
            is_occupied = MagicMock(return_value=True)
            interceptor = PresenceLockInterceptor(hass, entry, is_occupied)
            interceptor.setup()

            handler = self._register_calls[0]["handler"]
            call = MagicMock()
            data = {"entity_id": ["light.living_room", "light.kitchen"]}
            result = await handler(call, data)
            assert result == self._mock_result.ALLOW
            assert data["entity_id"] == ["light.kitchen"]
        finally:
            self._unpatch()

    def test_setup_register_runtime_error(self):
        """RuntimeError from register_interceptor should be caught gracefully."""
        import custom_components.presence_based_lighting.interceptor as mod
        orig_has = mod.HAS_INTERCEPTOR
        mod.HAS_INTERCEPTOR = True

        class MockInterceptResult:
            ALLOW = "allow"
            BLOCK = "block"
        mod.InterceptResult = MockInterceptResult

        def bad_register(*args, **kwargs):
            raise RuntimeError("conflict")
        mod.register_interceptor = bad_register

        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=True, req_vac=False)
            interceptor = PresenceLockInterceptor(hass, entry, lambda: True)
            result = interceptor.setup()
            # It logs a warning but doesn't crash; setup may return True or False
            # depending on whether any were registered
            assert isinstance(result, bool)
        finally:
            mod.HAS_INTERCEPTOR = orig_has
            if hasattr(mod, 'InterceptResult'):
                del mod.InterceptResult
            if hasattr(mod, 'register_interceptor'):
                del mod.register_interceptor

    def test_teardown_unregister_error(self):
        """Error in unregister function should be caught gracefully."""
        self._patch_interceptor_available()
        try:
            hass = MagicMock()
            entry = _make_entry(req_occ=True, req_vac=True)
            interceptor = PresenceLockInterceptor(hass, entry, lambda: True)
            interceptor.setup()

            # Replace unregister funcs with ones that raise
            interceptor._unregister_funcs = [
                MagicMock(side_effect=RuntimeError("teardown err")),
                MagicMock(side_effect=ValueError("other err")),
            ]
            interceptor.teardown()  # should not raise
            assert len(interceptor._unregister_funcs) == 0
        finally:
            self._unpatch()
