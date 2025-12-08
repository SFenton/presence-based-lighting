"""Hass-interceptor integration for Presence Based Lighting.

This module provides optional integration with hass-interceptor to proactively
block service calls that conflict with presence state (Presence Lock mode).

When hass-interceptor is not installed, the integration falls back to the
event-based approach (listening to EVENT_CALL_SERVICE and reverting state).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from .const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_ENTITY_ID,
    CONF_PRESENCE_CLEARED_SERVICE,
    CONF_PRESENCE_DETECTED_SERVICE,
    CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
    CONF_REQUIRE_VACANCY_FOR_CLEARED,
    CONF_USE_INTERCEPTOR,
    DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED,
    DEFAULT_REQUIRE_VACANCY_FOR_CLEARED,
    DEFAULT_USE_INTERCEPTOR,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

# Priority for presence-based-lighting interceptors
# Lower = runs earlier, can block before other integrations modify
INTERCEPTOR_PRIORITY = 50

# Try to import hass-interceptor
try:
    from hass_interceptor import register_interceptor, InterceptResult
    HAS_INTERCEPTOR = True
except ImportError:
    HAS_INTERCEPTOR = False
    _LOGGER.debug("hass-interceptor not installed, using fallback event-based approach")


def is_interceptor_available() -> bool:
    """Check if hass-interceptor is available."""
    return HAS_INTERCEPTOR


class PresenceLockInterceptor:
    """Manages interceptors for Presence Lock mode.
    
    When entities are configured with Presence Lock mode, this class registers
    interceptors with hass-interceptor to block conflicting service calls:
    
    - If require_occupancy_for_detected is True: blocks turn_on when room is empty
    - If require_vacancy_for_cleared is True: blocks turn_off when room is occupied
    """
    
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        is_occupied_func: Callable[[], bool],
    ) -> None:
        """Initialize the interceptor manager.
        
        Args:
            hass: Home Assistant instance
            entry: Config entry for this room
            is_occupied_func: Callable that returns True if any presence sensor is on
        """
        self.hass = hass
        self.entry = entry
        self._is_occupied = is_occupied_func
        self._unregister_funcs: list[Callable[[], None]] = []
        self._registered_services: set[tuple[str, str]] = set()
    
    def setup(self) -> bool:
        """Set up interceptors for all presence-lock entities.
        
        Returns:
            True if interceptors were registered, False if hass-interceptor
            is not available (fallback mode).
        """
        if not HAS_INTERCEPTOR:
            _LOGGER.debug("hass-interceptor not available, skipping interceptor setup")
            return False
        
        controlled_entities = self.entry.data.get(CONF_CONTROLLED_ENTITIES, [])
        
        for entity_config in controlled_entities:
            entity_id = entity_config.get(CONF_ENTITY_ID)
            if not entity_id:
                continue
            
            # Check if interceptor is enabled for this entity
            use_interceptor = entity_config.get(CONF_USE_INTERCEPTOR, DEFAULT_USE_INTERCEPTOR)
            if not use_interceptor:
                _LOGGER.debug(
                    "Interceptor disabled for entity %s, skipping registration",
                    entity_id,
                )
                continue
            
            require_occ = entity_config.get(
                CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
                DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED,
            )
            require_vac = entity_config.get(
                CONF_REQUIRE_VACANCY_FOR_CLEARED,
                DEFAULT_REQUIRE_VACANCY_FOR_CLEARED,
            )
            
            # Only register if presence lock is enabled for this entity
            if not require_occ and not require_vac:
                continue
            
            domain = entity_id.split(".")[0]
            
            # Register interceptor for detected service (e.g., turn_on)
            if require_occ:
                detected_service = entity_config.get(CONF_PRESENCE_DETECTED_SERVICE)
                if detected_service and detected_service != "none":
                    self._register_for_service(
                        domain,
                        detected_service,
                        entity_id,
                        block_when_empty=True,
                    )
            
            # Register interceptor for cleared service (e.g., turn_off)
            if require_vac:
                cleared_service = entity_config.get(CONF_PRESENCE_CLEARED_SERVICE)
                if cleared_service and cleared_service != "none":
                    self._register_for_service(
                        domain,
                        cleared_service,
                        entity_id,
                        block_when_empty=False,
                    )
        
        if self._unregister_funcs:
            _LOGGER.info(
                "Registered %d presence-lock interceptors for entry %s",
                len(self._unregister_funcs),
                self.entry.entry_id,
            )
        
        return len(self._unregister_funcs) > 0
    
    def _register_for_service(
        self,
        domain: str,
        service: str,
        entity_id: str,
        block_when_empty: bool,
    ) -> None:
        """Register an interceptor for a specific domain.service.
        
        Args:
            domain: Service domain (e.g., "light")
            service: Service name (e.g., "turn_on")
            entity_id: The entity to protect
            block_when_empty: If True, block when room is empty; if False, block when occupied
        """
        service_key = (domain, service)
        
        # We may register multiple entities for the same service
        # The handler will check the entity_id in the service data
        
        # Create a handler that captures the entity_id and block condition
        async def presence_lock_handler(call: ServiceCall, data: dict):
            """Handler that blocks calls based on presence state."""
            target_entities = data.get("entity_id", [])
            if isinstance(target_entities, str):
                target_entities = [target_entities]
            
            # Check if our protected entity is in the call
            if entity_id not in target_entities:
                return InterceptResult.ALLOW
            
            # Check presence state
            is_occupied = self._is_occupied()
            
            if block_when_empty and not is_occupied:
                # Block turn_on when room is empty
                _LOGGER.debug(
                    "Presence Lock: Blocking %s.%s for %s (room is empty)",
                    domain, service, entity_id,
                )
                # Remove the protected entity from the call
                remaining = [e for e in target_entities if e != entity_id]
                if not remaining:
                    return InterceptResult.BLOCK
                data["entity_id"] = remaining
                return InterceptResult.ALLOW
            
            if not block_when_empty and is_occupied:
                # Block turn_off when room is occupied
                _LOGGER.debug(
                    "Presence Lock: Blocking %s.%s for %s (room is occupied)",
                    domain, service, entity_id,
                )
                # Remove the protected entity from the call
                remaining = [e for e in target_entities if e != entity_id]
                if not remaining:
                    return InterceptResult.BLOCK
                data["entity_id"] = remaining
                return InterceptResult.ALLOW
            
            return InterceptResult.ALLOW
        
        try:
            unregister = register_interceptor(
                self.hass,
                domain=domain,
                service=service,
                handler=presence_lock_handler,
                priority=INTERCEPTOR_PRIORITY,
                integration=DOMAIN,
            )
            self._unregister_funcs.append(unregister)
            self._registered_services.add(service_key)
            _LOGGER.debug(
                "Registered presence-lock interceptor for %s.%s protecting %s",
                domain, service, entity_id,
            )
        except RuntimeError as err:
            _LOGGER.warning(
                "Failed to register interceptor for %s.%s: %s",
                domain, service, err,
            )
    
    def teardown(self) -> None:
        """Unregister all interceptors."""
        for unregister in self._unregister_funcs:
            try:
                unregister()
            except Exception as err:
                _LOGGER.warning("Error unregistering interceptor: %s", err)
        
        self._unregister_funcs.clear()
        self._registered_services.clear()
        _LOGGER.debug("Cleaned up presence-lock interceptors for entry %s", self.entry.entry_id)
