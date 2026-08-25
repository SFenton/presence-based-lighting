"""Hass-interceptor integration for Presence Based Lighting.

This module provides optional integration with hass-interceptor to proactively
normalize plain light turn-ons and block service calls that conflict with
presence state (Presence Lock mode).

When hass-interceptor is not installed, the integration falls back to the
event-based approach (listening to EVENT_CALL_SERVICE and reverting state).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from .command_context import CommandOrigin
from .const import (
    CONF_CONTROLLED_ENTITIES,
    CONF_ENTITY_ID,
    CONF_MANUAL_DISABLE_STATES,
    CONF_NORMALIZE_EXTERNAL_PLAIN_ON,
    CONF_PRESENCE_CLEARED_SERVICE,
    CONF_PRESENCE_CLEARED_STATE,
    CONF_PRESENCE_DETECTED_BRIGHTNESS_PCT,
    CONF_PRESENCE_DETECTED_SERVICE,
    CONF_PRESENCE_DETECTED_STATE,
    CONF_PRESENCE_DETECTED_TRANSITION,
    CONF_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE,
    CONF_REQUIRE_OCCUPANCY_FOR_DETECTED,
    CONF_REQUIRE_VACANCY_FOR_CLEARED,
    CONF_USE_INTERCEPTOR,
    DEFAULT_CLEARED_STATE,
    DEFAULT_DETECTED_SERVICE,
    DEFAULT_DETECTED_STATE,
    DEFAULT_MANUAL_DISABLE_STATES,
    DEFAULT_NORMALIZE_EXTERNAL_PLAIN_ON,
    DEFAULT_PRESENCE_DETECTED_BRIGHTNESS_PCT,
    DEFAULT_PRESENCE_DETECTED_TRANSITION,
    DEFAULT_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE,
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
NORMALIZER_INTERCEPTOR_PRIORITY = 200
NORMALIZER_INTEGRATION = f"{DOMAIN}_plain_on_normalizer"
NORMALIZER_MANAGER_KEY = "_light_turn_on_normalizer"

_EXPLICIT_BRIGHTNESS_KEYS = {
    "brightness",
    "brightness_pct",
    "brightness_step",
    "brightness_step_pct",
    "profile",
}

# Try to import hass-interceptor
try:
    from custom_components.hass_interceptor import (
        InterceptResult,
        register_interceptor,
    )
    HAS_INTERCEPTOR = True
except ImportError:
    try:
        from hass_interceptor import InterceptResult, register_interceptor
        HAS_INTERCEPTOR = True
    except ImportError:
        HAS_INTERCEPTOR = False
        _LOGGER.debug(
            "hass-interceptor not installed, using fallback event-based approach"
        )


def is_interceptor_available() -> bool:
    """Check if hass-interceptor is available."""
    return HAS_INTERCEPTOR


def _presence_lock_respects_manual_override(entity_config: dict) -> bool:
    return entity_config.get(
        CONF_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE,
        DEFAULT_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE,
    )


def _cleared_state_is_manual_override(entity_config: dict) -> bool:
    cleared_state = entity_config.get(CONF_PRESENCE_CLEARED_STATE, DEFAULT_CLEARED_STATE)
    manual_disable_states = entity_config.get(
        CONF_MANUAL_DISABLE_STATES,
        DEFAULT_MANUAL_DISABLE_STATES,
    )
    return cleared_state in manual_disable_states


@dataclass(frozen=True)
class LightNormalizationPolicy:
    """One entry's external bare-turn-on policy for a light."""

    entry_id: str
    entity_id: str
    brightness_pct: int
    transition: float
    is_active: Callable[[], bool]


class LightTurnOnNormalizer:
    """Own one domain-wide light.turn_on interceptor for all PBL entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._policies: dict[str, dict[str, LightNormalizationPolicy]] = {}
        self._unregister: Callable[[], None] | None = None
        self._warned_conflicts: set[str] = set()

    def register_policy(
        self,
        entry_id: str,
        entity_config: dict,
        is_active: Callable[[], bool],
    ) -> Callable[[], None] | None:
        """Register or replace one entry's policy for a controlled light."""
        entity_id = entity_config[CONF_ENTITY_ID]
        self._policies.setdefault(entity_id, {})[entry_id] = LightNormalizationPolicy(
            entry_id=entry_id,
            entity_id=entity_id,
            brightness_pct=int(
                entity_config.get(
                    CONF_PRESENCE_DETECTED_BRIGHTNESS_PCT,
                    DEFAULT_PRESENCE_DETECTED_BRIGHTNESS_PCT,
                )
            ),
            transition=float(
                entity_config.get(
                    CONF_PRESENCE_DETECTED_TRANSITION,
                    DEFAULT_PRESENCE_DETECTED_TRANSITION,
                )
            ),
            is_active=is_active,
        )
        try:
            self._ensure_registered()
        except RuntimeError as err:
            _LOGGER.warning(
                "Failed to register plain-on normalizer for %s: %s",
                entity_id,
                err,
            )
            self._policies[entity_id].pop(entry_id, None)
            if not self._policies[entity_id]:
                self._policies.pop(entity_id, None)
            return None

        def _unregister_policy() -> None:
            policies = self._policies.get(entity_id)
            if policies is None:
                return
            policies.pop(entry_id, None)
            if not policies:
                self._policies.pop(entity_id, None)
                self._warned_conflicts.discard(entity_id)
            if not self._policies and self._unregister is not None:
                self._unregister()
                self._unregister = None

        return _unregister_policy

    def _ensure_registered(self) -> None:
        if self._unregister is not None:
            return
        self._unregister = register_interceptor(
            self.hass,
            domain="light",
            service="turn_on",
            handler=self._normalize,
            priority=NORMALIZER_INTERCEPTOR_PRIORITY,
            integration=NORMALIZER_INTEGRATION,
        )

    def _resolve_policy(self, entity_id: str) -> LightNormalizationPolicy | None:
        policies = list(self._policies.get(entity_id, {}).values())
        if not policies:
            return None
        active = [policy for policy in policies if policy.is_active()]
        candidates = active or policies
        action_values = {
            (policy.brightness_pct, policy.transition)
            for policy in candidates
        }
        if len(action_values) > 1:
            if entity_id not in self._warned_conflicts:
                _LOGGER.warning(
                    "Conflicting plain-on policies for %s across entries %s; "
                    "leaving external command unchanged",
                    entity_id,
                    sorted(policy.entry_id for policy in candidates),
                )
                self._warned_conflicts.add(entity_id)
            return None
        self._warned_conflicts.discard(entity_id)
        return min(candidates, key=lambda policy: policy.entry_id)

    async def _normalize(self, call: ServiceCall, data: dict):
        target_entities = data.get("entity_id", [])
        if isinstance(target_entities, str):
            target_entities = [target_entities]
        if len(target_entities) != 1:
            return InterceptResult.ALLOW
        policy = self._resolve_policy(target_entities[0])
        if policy is None:
            return InterceptResult.ALLOW

        params = data.get("params")
        if not isinstance(params, dict):
            params = data
        if any(params.get(key) is not None for key in _EXPLICIT_BRIGHTNESS_KEYS):
            return InterceptResult.ALLOW

        params["brightness"] = round(255 * policy.brightness_pct / 100)
        params.setdefault("transition", policy.transition)
        _LOGGER.debug(
            "Normalized plain light.turn_on for %s to %s%% over %ss",
            policy.entity_id,
            policy.brightness_pct,
            params["transition"],
        )
        return InterceptResult.ALLOW


def get_light_turn_on_normalizer(hass: HomeAssistant) -> LightTurnOnNormalizer:
    """Return the domain-wide PBL light turn-on normalizer."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    normalizer = domain_data.get(NORMALIZER_MANAGER_KEY)
    if not isinstance(normalizer, LightTurnOnNormalizer):
        normalizer = LightTurnOnNormalizer(hass)
        domain_data[NORMALIZER_MANAGER_KEY] = normalizer
    return normalizer


class PresenceLockInterceptor:
    """Manages plain-on normalization and Presence Lock interceptors.

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
        entity_may_enforce_func: Callable[[str], bool] | None = None,
        entry_is_active_func: Callable[[], bool] | None = None,
        is_clearing_authority_occupied_func: Callable[[], bool] | None = None,
        classify_command_context_func: Callable[
            [str, object | None, str | None], CommandOrigin
        ] | None = None,
        handle_blocked_command_func: Callable[
            [str, str, object | None, dict], bool
        ] | None = None,
    ) -> None:
        """Initialize the interceptor manager.

        Args:
            hass: Home Assistant instance
            entry: Config entry for this room
            is_occupied_func: Callable that returns True if any presence sensor is on
            entity_may_enforce_func: Callable that returns whether this entry may
                currently enforce Presence Lock for an entity
            entry_is_active_func: Callable that returns whether this entry's
                activation conditions currently permit control
            is_clearing_authority_occupied_func: Callable that returns whether
                the configured clearing authority is positively occupied
            classify_command_context_func: Classifies a service-call context
                relative to this entry and target entity
            handle_blocked_command_func: Records a blocked external command and
                returns True when an already-confirmed batch should pass
        """
        self.hass = hass
        self.entry = entry
        self._is_occupied = is_occupied_func
        self._is_clearing_authority_occupied = (
            is_clearing_authority_occupied_func
            or self._is_occupied
        )
        self._classify_command_context = (
            classify_command_context_func
            or (lambda _entity_id, _context, _target_state: CommandOrigin.EXTERNAL)
        )
        self._handle_blocked_command = handle_blocked_command_func
        self._entity_may_enforce = entity_may_enforce_func or (lambda _entity_id: True)
        self._entry_is_active = entry_is_active_func or (lambda: True)
        self._unregister_funcs: list[Callable[[], None]] = []
        self._registered_services: set[tuple[str, str]] = set()
        self._presence_lock_registration_count = 0

    @property
    def has_presence_lock_interceptors(self) -> bool:
        """Return whether this entry registered proactive lock handlers."""
        return self._presence_lock_registration_count > 0

    def handles_presence_lock(self, entity_id: str, service: str | None) -> bool:
        """Return whether this entry registered a lock for this target/service."""
        return bool(service and (entity_id, service) in self._registered_services)

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

            use_interceptor = entity_config.get(CONF_USE_INTERCEPTOR, DEFAULT_USE_INTERCEPTOR)
            normalize_plain_on = entity_config.get(
                CONF_NORMALIZE_EXTERNAL_PLAIN_ON,
                DEFAULT_NORMALIZE_EXTERNAL_PLAIN_ON,
            )
            if not use_interceptor and not normalize_plain_on:
                _LOGGER.debug(
                    "Pre-dispatch handling disabled for entity %s",
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

            domain = entity_id.split(".")[0]
            detected_service = entity_config.get(CONF_PRESENCE_DETECTED_SERVICE)

            # Register interceptor for detected service (e.g., turn_on)
            if use_interceptor and require_occ:
                if detected_service and detected_service != "none":
                    self._register_for_service(
                        domain,
                        detected_service,
                        entity_id,
                        entity_config.get(
                            CONF_PRESENCE_DETECTED_STATE,
                            DEFAULT_DETECTED_STATE,
                        ),
                        block_when_empty=True,
                    )

            # Register interceptor for cleared service (e.g., turn_off)
            if use_interceptor and require_vac:
                cleared_service = entity_config.get(CONF_PRESENCE_CLEARED_SERVICE)
                if cleared_service and cleared_service != "none":
                    if (
                        _presence_lock_respects_manual_override(entity_config)
                        and _cleared_state_is_manual_override(entity_config)
                    ):
                        _LOGGER.debug(
                            "Skipping cleared-state interceptor for %s because manual override is allowed",
                            entity_id,
                        )
                    else:
                        self._register_for_service(
                            domain,
                            cleared_service,
                            entity_id,
                            entity_config.get(
                                CONF_PRESENCE_CLEARED_STATE,
                                DEFAULT_CLEARED_STATE,
                            ),
                            block_when_empty=False,
                        )

            if (
                normalize_plain_on
                and domain == "light"
                and detected_service == DEFAULT_DETECTED_SERVICE
            ):
                unregister = get_light_turn_on_normalizer(
                    self.hass
                ).register_policy(
                    self.entry.entry_id,
                    entity_config,
                    self._entry_is_active,
                )
                if unregister is not None:
                    self._unregister_funcs.append(unregister)

        if self._unregister_funcs:
            _LOGGER.info(
                "Registered %d service interceptors for entry %s",
                len(self._unregister_funcs),
                self.entry.entry_id,
            )

        return len(self._unregister_funcs) > 0

    def _register_for_service(
        self,
        domain: str,
        service: str,
        entity_id: str,
        target_state: str,
        block_when_empty: bool,
    ) -> None:
        """Register an interceptor for a specific domain.service.

        Args:
            domain: Service domain (e.g., "light")
            service: Service name (e.g., "turn_on")
            entity_id: The entity to protect
            target_state: State expected after the service completes
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

            origin = self._classify_command_context(
                entity_id,
                getattr(call, "context", None),
                target_state,
            )
            if origin in (CommandOrigin.OWN, CommandOrigin.SIBLING):
                return InterceptResult.ALLOW

            if not self._entity_may_enforce(entity_id):
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

            if not block_when_empty and self._is_clearing_authority_occupied():
                # Block turn_off while the configured clearing authority is occupied.
                if (
                    self._handle_blocked_command is not None
                    and self._handle_blocked_command(
                        entity_id,
                        service,
                        getattr(call, "context", None),
                        data,
                    )
                ):
                    return InterceptResult.ALLOW
                _LOGGER.debug(
                    "Presence Lock: Blocking %s.%s for %s (clearing authority occupied)",
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
            self._registered_services.add((entity_id, service))
            self._presence_lock_registration_count += 1
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
        self._presence_lock_registration_count = 0
        _LOGGER.debug("Cleaned up presence-lock interceptors for entry %s", self.entry.entry_id)
