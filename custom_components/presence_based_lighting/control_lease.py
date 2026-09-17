"""Token-guarded temporary control leases for PBL-managed entities."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from collections import Counter
from collections import defaultdict
from collections import deque
from collections import OrderedDict
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
from datetime import timedelta
from time import monotonic
from typing import Any
from typing import Callable
from typing import TYPE_CHECKING

from homeassistant.core import Context
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .command_context import get_command_context_registry
from .entity_targeting import expand_structural_targets
from .const import CONTROL_LEASE_ALLOWED_OWNERS
from .const import CONTROL_LEASE_CONFIRMATION_WINDOW_SECONDS
from .const import CONTROL_LEASE_CONTEXT_TTL_SECONDS
from .const import CONTROL_LEASE_MAX_OCCURRENCE_IDS
from .const import CONTROL_LEASE_MAX_TARGET_ENTITY_IDS
from .const import CONTROL_LEASE_MAX_TTL_SECONDS
from .const import CONTROL_LEASE_MIN_TTL_SECONDS
from .const import CONTROL_LEASE_MODE_ENFORCE
from .const import CONTROL_LEASE_MODE_OBSERVE
from .const import CONTROL_LEASE_MODE_OFF
from .const import CONTROL_LEASE_RECOVERY_GRACE_SECONDS
from .const import CONTROL_LEASE_RELEASE_CAUSE_EXTERNAL_OFF
from .const import CONTROL_LEASE_SCHEMA_VERSION
from .const import CONTROL_LEASE_STORE_KEY
from .const import CONTROL_LEASE_TERMINAL_LIMIT
from .const import DEFAULT_CONTROL_LEASE_BLOCKERS
from .const import DEFAULT_CONTROL_LEASE_CORRECT_LATE_ON
from .const import DEFAULT_CONTROL_LEASE_MODE
from .const import DOMAIN
from .const import EVENT_CONTROL_LEASE_REVOKED
from .const import EVENT_CONTROL_TRANSITION

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__package__)

MANAGER_KEY = "_control_lease_manager"
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
_UNAVAILABLE_STATES = {"unknown", "unavailable"}
_ALLOWED_LEASE_SERVICE_DATA = {
    "brightness",
    "brightness_pct",
    "color_temp_kelvin",
    "transition",
}
_BRIGHTNESS_AUTHORITY_KEYS = {
    "brightness",
    "brightness_pct",
    "brightness_step",
    "brightness_step_pct",
    "profile",
    "white",
}
_OFF_BREAK_CAUSES = {
    "manual_group_off",
    "bulk_off",
    "controlled_entity_off",
    "target_unavailable",
}


@dataclass(frozen=True)
class BaselineSuppressionFingerprint:
    """Immutable admitted pre-lease asleep/manual-off suppression."""

    entry_id: str
    override_source: str
    override_policy: str
    override_created_at: str
    pause_source: str
    pause_paused_at: str


@dataclass(frozen=True)
class ControlLeaseRecord:
    """One root-scoped lease owned by one external controller."""

    root_entity_id: str
    lease_id: str
    controller_id: str
    request_id: str
    owner: str
    occurrence_ids: tuple[str, ...]
    target_entity_ids: tuple[str, ...]
    known_target_entity_ids: tuple[str, ...]
    acquired_dt: datetime
    expires_dt: datetime
    acquired_monotonic: float
    expires_monotonic: float
    status: str
    generation: int
    baseline_suppressions: tuple[BaselineSuppressionFingerprint, ...] = ()
    baseline_outcome: str = "none"
    recovery_deadline_dt: datetime | None = None
    recovery_deadline_monotonic: float | None = None
    last_transition: str = "granted"
    last_outcome: str = "acquired"
    last_context_classification: str = "owner_service"
    previous_generation: int | None = None

    @property
    def acquired_at(self) -> str:
        return self.acquired_dt.isoformat()

    @property
    def expires_at(self) -> str:
        return self.expires_dt.isoformat()

    @property
    def recovery_deadline_at(self) -> str | None:
        if self.recovery_deadline_dt is None:
            return None
        return self.recovery_deadline_dt.isoformat()

    @property
    def released_entity_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(set(self.known_target_entity_ids) - set(self.target_entity_ids))
        )


@dataclass(frozen=True)
class EntityLeaseRegistration:
    """One config entry's control-lease policy for a shared root."""

    entry_id: str
    mode: str
    blocker_entity_ids: tuple[str, ...]
    correct_late_on: bool
    listener: Callable[[str, dict[str, Any]], None] | None
    blocker_callback: Callable[[], list[str]] | None
    baseline_callback: Callable[[], BaselineSuppressionFingerprint | None] | None
    completion_callback: (
        Callable[
            [str, tuple[BaselineSuppressionFingerprint, ...]],
            str,
        ]
        | None
    )


@dataclass
class LeaseCommandRecord:
    """One exact context issued for a lease-owned light command."""

    context_id: str
    root_entity_id: str
    lease_id: str
    controller_id: str
    generation: int
    target_entity_ids: tuple[str, ...]
    direction: str
    command_id: str
    created_monotonic: float
    late_echo_reported: bool = False


@dataclass(frozen=True)
class GuardDecision:
    """Pre-dispatch decision for a lease-owned command."""

    owned: bool
    allowed: bool
    target_entity_ids: tuple[str, ...] = ()
    reason: str | None = None


@dataclass
class ManualAuthorityContextRecord:
    """One foreign command that pre-revoked room brightness authority."""

    created_monotonic: float
    root_entity_ids: set[str]
    bypass_entity_ids: set[str]
    handled_root_entity_ids: set[str]
    preserve_baseline_root_entity_ids: set[str]


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt_util.as_utc(parsed)


class ControlLeaseManager:
    """Coordinate root-scoped control leases across every PBL config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        monotonic_source: Callable[[], float] | None = None,
        utcnow_source: Callable[[], datetime] | None = None,
        store: Store | None = None,
    ) -> None:
        self.hass = hass
        self._monotonic = monotonic_source or monotonic
        self._utcnow = utcnow_source or dt_util.utcnow
        self._store = store or Store(
            hass,
            CONTROL_LEASE_SCHEMA_VERSION,
            CONTROL_LEASE_STORE_KEY,
        )
        self._leases: dict[str, ControlLeaseRecord] = {}
        self._observed: dict[str, ControlLeaseRecord] = {}
        self._registrations: dict[str, dict[str, EntityLeaseRegistration]] = (
            defaultdict(dict)
        )
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._generations: dict[str, int] = defaultdict(int)
        self._terminal: deque[dict[str, Any]] = deque(
            maxlen=CONTROL_LEASE_TERMINAL_LIMIT
        )
        self._terminal_by_id: dict[str, dict[str, Any]] = {}
        self._last_transition: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, Counter] = defaultdict(Counter)
        self._contexts: OrderedDict[str, LeaseCommandRecord] = OrderedDict()
        self._in_flight_context_ids: set[str] = set()
        self._manual_authority_contexts: OrderedDict[
            str, ManualAuthorityContextRecord
        ] = OrderedDict()
        self._expiry_tasks: dict[str, asyncio.Task] = {}
        self._corrected_breaks: deque[tuple[str, int]] = deque(maxlen=256)
        self._initialized = False
        self._initialize_lock = asyncio.Lock()
        self._enforcement_available = False
        self._guard: Any = None
        self._correlation_salt = secrets.token_hex(16)

    # ------------------------------------------------------------------
    # Registration and mode
    # ------------------------------------------------------------------

    def register_entity(
        self,
        entry_id: str,
        root_entity_id: str,
        *,
        mode: str = DEFAULT_CONTROL_LEASE_MODE,
        blocker_entity_ids: list[str] | tuple[str, ...] | None = None,
        correct_late_on: bool = DEFAULT_CONTROL_LEASE_CORRECT_LATE_ON,
        listener: Callable[[str, dict[str, Any]], None] | None = None,
        blocker_callback: Callable[[], list[str]] | None = None,
        baseline_callback: (
            Callable[[], BaselineSuppressionFingerprint | None] | None
        ) = None,
        completion_callback: (
            Callable[
                [str, tuple[BaselineSuppressionFingerprint, ...]],
                str,
            ]
            | None
        ) = None,
    ) -> None:
        """Register one entry's policy and transition listener for a root."""
        self._registrations[root_entity_id][entry_id] = EntityLeaseRegistration(
            entry_id=entry_id,
            mode=mode,
            blocker_entity_ids=tuple(
                sorted(set(blocker_entity_ids or DEFAULT_CONTROL_LEASE_BLOCKERS))
            ),
            correct_late_on=bool(correct_late_on),
            listener=listener,
            blocker_callback=blocker_callback,
            baseline_callback=baseline_callback,
            completion_callback=completion_callback,
        )
        if (
            root_entity_id in self._leases
            and self.effective_mode(root_entity_id) != CONTROL_LEASE_MODE_ENFORCE
        ):
            self.break_nowait(
                root_entity_id,
                cause="control_lease_mode_changed",
                direction="none",
                context_classification="admin",
            )
            return
        record = self._leases.get(root_entity_id)
        if record is not None and listener is not None:
            listener(
                root_entity_id,
                self._transition_data(
                    record,
                    action="restored",
                    outcome=record.status,
                    cause="entry_registered",
                ),
            )

    def unregister_entry(self, entry_id: str) -> None:
        """Remove one config entry's lease policy/listener registrations."""
        for root_entity_id in list(self._registrations):
            self._registrations[root_entity_id].pop(entry_id, None)
            if not self._registrations[root_entity_id]:
                self._registrations.pop(root_entity_id, None)

    def effective_mode(self, root_entity_id: str) -> str:
        """Return the least behavior-changing mode across shared entries."""
        modes = {
            registration.mode
            for registration in self._registrations.get(root_entity_id, {}).values()
        }
        if not modes or CONTROL_LEASE_MODE_OFF in modes:
            return CONTROL_LEASE_MODE_OFF
        if CONTROL_LEASE_MODE_OBSERVE in modes:
            return CONTROL_LEASE_MODE_OBSERVE
        return CONTROL_LEASE_MODE_ENFORCE

    def set_enforcement_available(self, available: bool) -> None:
        """Set whether exact pre-dispatch context enforcement is active."""
        self._enforcement_available = bool(available)

    @property
    def enforcement_available(self) -> bool:
        return self._enforcement_available

    def attach_enforcement_guard(self, guard: Any) -> bool:
        """Attach the one domain-wide pre-dispatch enforcement guard."""
        if self._guard is not None:
            return self._enforcement_available
        self._guard = guard
        self.set_enforcement_available(bool(guard.setup()))
        return self._enforcement_available

    def has_suppression(self, root_entity_id: str) -> bool:
        """Return whether an active or recovering lease suppresses PBL."""
        record = self._leases.get(root_entity_id)
        return bool(record and record.status in {"active", "recovering"})

    def has_brightness_authority(self, entity_id: str) -> bool:
        """Return whether an enforced room lease owns this root or target."""
        return any(
            record.status in {"active", "recovering", "persisting"}
            and (
                record.root_entity_id == entity_id
                or entity_id in record.target_entity_ids
            )
            for record in self._leases.values()
        )

    def has_candidate(self, root_entity_id: str) -> bool:
        """Return whether enforce or observe mode is tracking a lease request."""
        return root_entity_id in self._leases or root_entity_id in self._observed

    def has_any_candidate(self) -> bool:
        """Return whether any enforce or observe lease may need target matching."""
        return bool(self._leases or self._observed)

    def has_any_enforced_candidate(self) -> bool:
        """Return whether target-expansion failure could violate an enforced lease."""
        return bool(self._leases)

    def get(self, root_entity_id: str) -> ControlLeaseRecord | None:
        return self._leases.get(root_entity_id)

    # ------------------------------------------------------------------
    # Persistence and recovery
    # ------------------------------------------------------------------

    async def async_initialize(self) -> None:
        """Load the central lease store once and restore fail-dark recovery."""
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            try:
                data = await self._store.async_load() or {}
            except Exception as err:
                _LOGGER.error("Control lease store load failed: %s", err)
                data = {}
            stored_salt = data.get("correlation_salt")
            if isinstance(stored_salt, str) and stored_salt:
                self._correlation_salt = stored_salt

            now_dt = self._utcnow()
            now_monotonic = self._monotonic()
            for payload in (data.get("leases") or {}).values():
                record = self._record_from_storage(
                    payload,
                    now_dt=now_dt,
                    now_monotonic=now_monotonic,
                )
                if record is None:
                    continue
                if record.expires_dt <= now_dt:
                    self._append_terminal(
                        self._terminal_summary(
                            record,
                            action="expired",
                            outcome="expired_while_stopped",
                            cause="expiry",
                            terminal_at=now_dt,
                        )
                    )
                    continue
                recovery_seconds = min(
                    (record.expires_dt - now_dt).total_seconds(),
                    CONTROL_LEASE_RECOVERY_GRACE_SECONDS,
                )
                recovering = replace(
                    record,
                    status="recovering",
                    recovery_deadline_dt=now_dt + timedelta(seconds=recovery_seconds),
                    recovery_deadline_monotonic=now_monotonic + recovery_seconds,
                    last_transition="restored",
                    last_outcome="recovering",
                    last_context_classification="restart",
                )
                self._leases[record.root_entity_id] = recovering
                self._generations[record.root_entity_id] = max(
                    self._generations[record.root_entity_id],
                    record.generation,
                )

            for summary in data.get("terminal") or []:
                if isinstance(summary, dict):
                    self._append_terminal(dict(summary))
                    root_entity_id = summary.get("root_entity_id")
                    if isinstance(root_entity_id, str):
                        self._generations[root_entity_id] = max(
                            self._generations[root_entity_id],
                            int(summary.get("generation") or 0),
                        )

            self._initialized = True
            for root_entity_id, record in self._leases.items():
                self._schedule_expiry(root_entity_id, record)
                transition = self._record_transition(
                    record,
                    action="restored",
                    outcome="recovering",
                    cause="home_assistant_restart",
                    context_classification="restart",
                    level=logging.INFO,
                )
                self._notify(root_entity_id, transition)

    async def _async_save(
        self, records: dict[str, ControlLeaseRecord] | None = None
    ) -> None:
        active_records = records if records is not None else self._leases
        payload = {
            "schema_version": CONTROL_LEASE_SCHEMA_VERSION,
            "correlation_salt": self._correlation_salt,
            "leases": {
                root_entity_id: self._record_to_storage(record)
                for root_entity_id, record in active_records.items()
                if record.status in {"active", "recovering"}
            },
            "terminal": list(self._terminal),
        }
        await self._store.async_save(payload)

    def _schedule_save(self) -> None:
        async def _save() -> None:
            try:
                await self._async_save()
            except Exception as err:
                _LOGGER.error("Control lease store save failed: %s", err)

        self._create_task(_save())

    # ------------------------------------------------------------------
    # Acquisition, release, and break
    # ------------------------------------------------------------------

    async def async_acquire(
        self,
        *,
        root_entity_id: str,
        lease_id: str,
        controller_id: str,
        request_id: str,
        owner: str,
        occurrence_ids: list[str] | tuple[str, ...],
        ttl_seconds: float,
        target_entity_ids: list[str] | tuple[str, ...],
    ) -> dict[str, Any]:
        """Acquire, update, or recover one caller-supplied root lease."""
        await self.async_initialize()
        validation_errors = self._validate_request(
            root_entity_id=root_entity_id,
            lease_id=lease_id,
            controller_id=controller_id,
            request_id=request_id,
            owner=owner,
            occurrence_ids=occurrence_ids,
            ttl_seconds=ttl_seconds,
            target_entity_ids=target_entity_ids,
        )
        if validation_errors:
            return self._deny(
                root_entity_id,
                lease_id=lease_id,
                controller_id=controller_id,
                request_id=request_id,
                owner=owner,
                occurrence_ids=occurrence_ids,
                target_entity_ids=target_entity_ids,
                blockers=validation_errors,
                outcome="invalid_request",
            )
        if self._correlation_ref(lease_id) in self._terminal_by_id:
            return self._deny(
                root_entity_id,
                lease_id=lease_id,
                controller_id=controller_id,
                request_id=request_id,
                owner=owner,
                occurrence_ids=occurrence_ids,
                target_entity_ids=target_entity_ids,
                blockers=["retired_lease_id"],
                outcome="stale_lease",
            )

        occurrence_tuple = tuple(sorted(set(occurrence_ids)))
        target_tuple = tuple(sorted(set(target_entity_ids)))
        mode = self.effective_mode(root_entity_id)
        blockers = self._acquisition_blockers(
            root_entity_id,
            target_tuple,
            require_enforcement=mode == CONTROL_LEASE_MODE_ENFORCE,
        )
        if mode == CONTROL_LEASE_MODE_OFF:
            blockers.insert(0, "control_lease_mode_off")
        if blockers:
            return self._deny(
                root_entity_id,
                lease_id=lease_id,
                controller_id=controller_id,
                request_id=request_id,
                owner=owner,
                occurrence_ids=occurrence_tuple,
                target_entity_ids=target_tuple,
                blockers=blockers,
                outcome="denied",
            )

        baseline_suppressions = self._current_baseline_suppressions(root_entity_id)
        now_dt = self._utcnow()
        now_monotonic = self._monotonic()
        if mode == CONTROL_LEASE_MODE_OBSERVE:
            record = ControlLeaseRecord(
                root_entity_id=root_entity_id,
                lease_id=lease_id,
                controller_id=controller_id,
                request_id=request_id,
                owner=owner,
                occurrence_ids=occurrence_tuple,
                target_entity_ids=target_tuple,
                known_target_entity_ids=target_tuple,
                acquired_dt=now_dt,
                expires_dt=now_dt + timedelta(seconds=float(ttl_seconds)),
                acquired_monotonic=now_monotonic,
                expires_monotonic=now_monotonic + float(ttl_seconds),
                status="observing",
                generation=self._next_generation(root_entity_id),
                baseline_suppressions=baseline_suppressions,
                last_transition="would_grant",
                last_outcome="observe",
            )
            self._observed[root_entity_id] = record
            transition = self._record_transition(
                record,
                action="would_grant",
                outcome="observe",
                cause="control_lease_mode_observe",
                level=logging.INFO,
            )
            self._notify(root_entity_id, transition)
            self._schedule_expiry(root_entity_id, record)
            return self._result(record, outcome="would_grant")

        lock = self._locks[root_entity_id]
        previous: ControlLeaseRecord | None = None
        initial = False
        transition_action = "granted"
        transition_outcome = "acquired"
        async with lock:
            existing = self._leases.get(root_entity_id)
            if existing is not None:
                if (
                    existing.lease_id != lease_id
                    or existing.controller_id != controller_id
                    or existing.owner != owner
                ):
                    return self._deny(
                        root_entity_id,
                        lease_id=lease_id,
                        controller_id=controller_id,
                        request_id=request_id,
                        owner=owner,
                        occurrence_ids=occurrence_tuple,
                        target_entity_ids=target_tuple,
                        blockers=["lease_conflict"],
                        outcome="conflict",
                        conflict=existing.lease_id,
                    )
                previous = existing
                changed = (
                    existing.occurrence_ids != occurrence_tuple
                    or existing.target_entity_ids != target_tuple
                    or existing.status == "recovering"
                )
                known_targets = self._bounded_known_targets(
                    existing.known_target_entity_ids,
                    target_tuple,
                )
                generation = (
                    self._next_generation(root_entity_id)
                    if changed
                    else existing.generation
                )
                record = replace(
                    existing,
                    request_id=request_id,
                    occurrence_ids=occurrence_tuple,
                    target_entity_ids=target_tuple,
                    known_target_entity_ids=known_targets,
                    status="active",
                    generation=generation,
                    recovery_deadline_dt=None,
                    recovery_deadline_monotonic=None,
                    last_transition=(
                        "recovered"
                        if existing.status == "recovering"
                        else "updated" if changed else "duplicate"
                    ),
                    last_outcome=(
                        "recovered"
                        if existing.status == "recovering"
                        else "updated" if changed else "duplicate"
                    ),
                    last_context_classification="owner_service",
                )
                self._leases[root_entity_id] = record
                transition_action = record.last_transition
                transition_outcome = record.last_outcome
            else:
                initial = True
                generation = self._next_generation(root_entity_id)
                record = ControlLeaseRecord(
                    root_entity_id=root_entity_id,
                    lease_id=lease_id,
                    controller_id=controller_id,
                    request_id=request_id,
                    owner=owner,
                    occurrence_ids=occurrence_tuple,
                    target_entity_ids=target_tuple,
                    known_target_entity_ids=target_tuple,
                    acquired_dt=now_dt,
                    expires_dt=now_dt + timedelta(seconds=float(ttl_seconds)),
                    acquired_monotonic=now_monotonic,
                    expires_monotonic=now_monotonic + float(ttl_seconds),
                    status="persisting",
                    generation=generation,
                    baseline_suppressions=baseline_suppressions,
                )
                self._leases[root_entity_id] = record

        persisted_record = replace(record, status="active")
        save_records = dict(self._leases)
        save_records[root_entity_id] = persisted_record
        try:
            await self._async_save(save_records)
        except Exception as err:
            async with lock:
                current = self._leases.get(root_entity_id)
                if current is not None and current.generation == record.generation:
                    if previous is None:
                        self._leases.pop(root_entity_id, None)
                    else:
                        self._leases[root_entity_id] = previous
            self._counters[root_entity_id]["persistence_error"] += 1
            _LOGGER.error(
                "Control lease persistence failed for %s: %s",
                root_entity_id,
                err,
            )
            return self._deny(
                root_entity_id,
                lease_id=lease_id,
                controller_id=controller_id,
                request_id=request_id,
                owner=owner,
                occurrence_ids=occurrence_tuple,
                target_entity_ids=target_tuple,
                blockers=["persistence_error"],
                outcome="persistence_error",
            )

        if initial:
            race_blockers = self._acquisition_blockers(
                root_entity_id,
                target_tuple,
                require_enforcement=True,
            )
            if (
                self._current_baseline_suppressions(root_entity_id)
                != record.baseline_suppressions
            ):
                race_blockers.append("baseline_suppression_changed")
            if race_blockers:
                async with lock:
                    current = self._leases.get(root_entity_id)
                    if current is not None and current.generation == record.generation:
                        self._leases.pop(root_entity_id, None)
                self._schedule_save()
                return self._deny(
                    root_entity_id,
                    lease_id=lease_id,
                    controller_id=controller_id,
                    request_id=request_id,
                    owner=owner,
                    occurrence_ids=occurrence_tuple,
                    target_entity_ids=target_tuple,
                    blockers=race_blockers,
                    outcome="denied_after_persist",
                )

        if self._utcnow() >= record.expires_dt:
            await self.async_break(
                root_entity_id,
                cause="expiry",
                direction="none",
                context_classification="watchdog",
            )
            return self._deny(
                root_entity_id,
                lease_id=lease_id,
                controller_id=controller_id,
                request_id=request_id,
                owner=owner,
                occurrence_ids=occurrence_tuple,
                target_entity_ids=target_tuple,
                blockers=["lease_expired_during_acquire"],
                outcome="expired",
            )

        async with lock:
            current = self._leases.get(root_entity_id)
            if current is None or current.generation != record.generation:
                self._schedule_save()
                return self._deny(
                    root_entity_id,
                    lease_id=lease_id,
                    controller_id=controller_id,
                    request_id=request_id,
                    owner=owner,
                    occurrence_ids=occurrence_tuple,
                    target_entity_ids=target_tuple,
                    blockers=["lease_changed_during_acquire"],
                    outcome="conflict",
                )
            active_record = replace(current, status="active")
            self._leases[root_entity_id] = active_record

        self._schedule_expiry(root_entity_id, active_record)
        level = (
            logging.DEBUG
            if transition_action in {"duplicate", "updated", "recovered"}
            else logging.INFO
        )
        transition = self._record_transition(
            active_record,
            action=transition_action,
            outcome=transition_outcome,
            cause="owner_request",
            level=level,
        )
        self._notify(root_entity_id, transition)
        return self._result(active_record, outcome=transition_outcome)

    async def async_release(
        self,
        *,
        root_entity_id: str,
        lease_id: str,
        controller_id: str,
        expected_generation: int,
        request_id: str,
        owner: str,
        outcome: str,
        cause: str,
    ) -> dict[str, Any]:
        """Release one exact lease; registered PBL listeners own external policy."""
        external_off = cause == CONTROL_LEASE_RELEASE_CAUSE_EXTERNAL_OFF
        if external_off and outcome != "cancelled":
            return {
                "outcome": "invalid_release",
                "reason": "external_off_requires_cancelled",
            }
        await self.async_initialize()
        observed = self._observed.get(root_entity_id)
        if observed is not None:
            if (
                observed.lease_id != lease_id
                or observed.controller_id != controller_id
                or observed.owner != owner
                or observed.generation != expected_generation
            ):
                return self._token_mismatch(
                    root_entity_id,
                    lease_id=lease_id,
                    request_id=request_id,
                    outcome="token_mismatch",
                )
            self._observed.pop(root_entity_id, None)
            self._cancel_expiry(root_entity_id)
            transition = self._record_transition(
                observed,
                action="would_release",
                outcome="observe",
                cause=cause,
                level=logging.DEBUG,
            )
            self._notify(root_entity_id, transition)
            return self._result(observed, outcome="would_release")

        lock = self._locks[root_entity_id]
        async with lock:
            record = self._leases.get(root_entity_id)
            if record is None:
                terminal = self._terminal_by_id.get(self._correlation_ref(lease_id))
                if terminal is not None:
                    return {
                        "outcome": "already_terminal",
                        "lease_id": lease_id,
                        "root_entity_id": terminal.get("root_entity_id"),
                        "terminal_outcome": terminal.get("outcome"),
                        "terminal_cause": terminal.get("cause"),
                    }
                return self._token_mismatch(
                    root_entity_id,
                    lease_id=lease_id,
                    request_id=request_id,
                    outcome="stale_lease",
                )
            if (
                record.lease_id != lease_id
                or record.controller_id != controller_id
                or record.owner != owner
                or record.generation != expected_generation
            ):
                return self._token_mismatch(
                    root_entity_id,
                    lease_id=lease_id,
                    request_id=request_id,
                    outcome="token_mismatch",
                )
            terminal_record, summary = self._terminalize_locked(
                record,
                action="released",
                outcome=outcome,
                cause=cause,
                context_classification="owner_service",
            )

        baseline_outcome = "none"
        if terminal_record.baseline_suppressions:
            if outcome == "completed" and cause == "hold_complete":
                baseline_outcome = self._complete_admitted_baseline(terminal_record)
            else:
                baseline_outcome = "preserved_nonclean_release"
        terminal_record = replace(
            terminal_record,
            baseline_outcome=baseline_outcome,
        )
        summary["baseline_outcome"] = baseline_outcome
        self._cancel_expiry(root_entity_id)
        transition = None
        if external_off:
            # Apply ordinary PBL external suppression before persistence yields.
            transition = self._record_transition(
                terminal_record,
                action="released",
                outcome=outcome,
                cause=cause,
                level=logging.INFO,
            )
            self._notify(root_entity_id, transition)
        persistence_error = None
        try:
            await self._async_save()
        except Exception as err:
            persistence_error = "persistence_error"
            self._counters[root_entity_id]["persistence_error"] += 1
            _LOGGER.error(
                "Released control lease could not be persisted for %s: %s",
                root_entity_id,
                err,
            )
        if transition is None:
            transition = self._record_transition(
                terminal_record,
                action="released",
                outcome=outcome,
                cause=cause,
                level=logging.INFO,
            )
            self._notify(root_entity_id, transition)
        return {
            **self._result(terminal_record, outcome="released"),
            "terminal_outcome": summary["outcome"],
            "terminal_cause": summary["cause"],
            "persistence_error": persistence_error,
            "baseline_outcome": baseline_outcome,
        }

    async def async_break(
        self,
        root_entity_id: str,
        *,
        cause: str,
        direction: str = "off",
        context_classification: str = "external",
    ) -> bool:
        """Break a lease before existing manual/admin semantics proceed."""
        await self.async_initialize()
        lock = self._locks[root_entity_id]
        async with lock:
            broken = self._break_locked(
                root_entity_id,
                cause=cause,
                direction=direction,
                context_classification=context_classification,
            )
        if broken is None:
            return False
        record, transition = broken
        self._cancel_expiry(root_entity_id)
        self._schedule_save()
        level = (
            logging.WARNING
            if direction == "off" and context_classification == "unknown"
            else logging.INFO
        )
        self._emit_recorded_transition(record, transition, level)
        self._notify(root_entity_id, transition)
        return True

    def break_nowait(
        self,
        root_entity_id: str,
        *,
        cause: str,
        direction: str = "off",
        context_classification: str = "external",
    ) -> bool:
        """Break from synchronous HA callbacks using one event-loop mutation."""
        broken = self._break_locked(
            root_entity_id,
            cause=cause,
            direction=direction,
            context_classification=context_classification,
        )
        if broken is None:
            return False
        record, transition = broken
        self._cancel_expiry(root_entity_id)
        self._schedule_save()
        self._emit_recorded_transition(record, transition, logging.INFO)
        self._notify(root_entity_id, transition)
        return True

    def _break_locked(
        self,
        root_entity_id: str,
        *,
        cause: str,
        direction: str,
        context_classification: str,
    ) -> tuple[ControlLeaseRecord, dict[str, Any]] | None:
        record = self._leases.get(root_entity_id)
        if record is None:
            observed = self._observed.pop(root_entity_id, None)
            if observed is None:
                return None
            transition = self._transition_data(
                observed,
                action="would_break",
                outcome="observe",
                cause=cause,
                context_classification=context_classification,
            )
            return observed, transition
        terminal_record, _summary = self._terminalize_locked(
            record,
            action="revoked",
            outcome="revoked",
            cause=cause,
            context_classification=context_classification,
            direction=direction,
        )
        transition = self._transition_data(
            terminal_record,
            action="revoked",
            outcome="revoked",
            cause=cause,
            context_classification=context_classification,
        )
        return terminal_record, transition

    async def async_expire_due(self, root_entity_id: str | None = None) -> int:
        """Expire active/recovering leases whose watchdog deadline elapsed."""
        await self.async_initialize()
        roots = (
            [root_entity_id]
            if root_entity_id is not None
            else sorted(set(self._leases) | set(self._observed))
        )
        expired = 0
        now = self._monotonic()
        for root in roots:
            observed = self._observed.get(root)
            if observed is not None:
                if now < observed.expires_monotonic:
                    continue
                lock = self._locks[root]
                async with lock:
                    current = self._observed.get(root)
                    if current is None or current.generation != observed.generation:
                        continue
                    self._observed.pop(root, None)
                    terminal_record = replace(
                        observed,
                        status="expired",
                        generation=self._next_generation(root),
                        previous_generation=observed.generation,
                        last_transition="observe_expired",
                        last_outcome="expired",
                        baseline_outcome=(
                            "preserved_observe_expiry"
                            if observed.baseline_suppressions
                            else "none"
                        ),
                    )
                    summary = self._terminal_summary(
                        terminal_record,
                        action="observe_expired",
                        outcome="expired",
                        cause="observe_expiry",
                        terminal_at=self._utcnow(),
                    )
                    self._append_terminal(summary)
                self._cancel_expiry(root)
                self._schedule_save()
                transition = self._record_transition(
                    terminal_record,
                    action="observe_expired",
                    outcome="expired",
                    cause="observe_expiry",
                    level=logging.INFO,
                )
                self._notify(root, transition)
                expired += 1
                continue
            record = self._leases.get(root)
            if record is None:
                continue
            runtime_cause = self._runtime_break_cause(record)
            if runtime_cause is not None:
                if await self.async_break(
                    root,
                    cause=runtime_cause,
                    direction=(
                        "off" if runtime_cause == "target_unavailable" else "none"
                    ),
                    context_classification="watchdog",
                ):
                    expired += 1
                continue
            due = (
                record.recovery_deadline_monotonic
                if record.status == "recovering"
                else record.expires_monotonic
            )
            if due is None or now < due:
                continue
            cause = "recovery_timeout" if record.status == "recovering" else "expiry"
            if await self.async_break(
                root,
                cause=cause,
                direction="off" if cause == "recovery_timeout" else "none",
                context_classification="watchdog",
            ):
                expired += 1
                self._counters[root]["expiry"] += 1
                _LOGGER.warning(
                    "Control lease expired for %s (%s)",
                    root,
                    cause,
                )
        return expired

    # ------------------------------------------------------------------
    # Lease-owned command dispatch and context enforcement
    # ------------------------------------------------------------------

    async def async_call_with_control_lease(
        self,
        *,
        root_entity_id: str,
        lease_id: str,
        controller_id: str,
        expected_generation: int,
        command_id: str,
        target_entity_ids: list[str] | tuple[str, ...],
        service_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch one explicit-brightness light command under an active lease."""
        await self.async_initialize()
        if not isinstance(command_id, str) or not _OPAQUE_ID_RE.fullmatch(command_id):
            return {
                "outcome": "blocked",
                "blockers": ["invalid_command_id"],
            }
        if (
            not target_entity_ids
            or len(set(target_entity_ids)) > CONTROL_LEASE_MAX_TARGET_ENTITY_IDS
            or any(
                not isinstance(entity_id, str) or not entity_id.startswith("light.")
                for entity_id in target_entity_ids
            )
        ):
            return {
                "outcome": "blocked",
                "blockers": ["invalid_target_entity_ids"],
            }
        if not self._service_data_is_bounded(service_data):
            return {
                "outcome": "blocked",
                "blockers": ["invalid_service_data"],
            }
        if not self._enforcement_available:
            self._counters[root_entity_id]["blocked_command"] += 1
            await self.async_break(
                root_entity_id,
                cause="context_enforcement_unavailable",
                direction="none",
                context_classification="control_lease",
            )
            return {
                "outcome": "blocked",
                "blockers": ["context_enforcement_unavailable"],
            }
        if not self._has_absolute_brightness(service_data):
            self._counters[root_entity_id]["blocked_command"] += 1
            self._record_simple_transition(
                root_entity_id,
                action="command_blocked",
                outcome="bare_turn_on",
                cause="absolute_brightness_required",
                context_classification="control_lease",
                level=logging.WARNING,
            )
            return {
                "outcome": "blocked",
                "blockers": ["absolute_brightness_required"],
            }

        requested_targets = tuple(sorted(set(target_entity_ids)))
        lock = self._locks[root_entity_id]
        broken_transition: tuple[ControlLeaseRecord, dict[str, Any]] | None = None
        broken_reason: str | None = None
        async with lock:
            record = self._leases.get(root_entity_id)
            if (
                record is None
                or record.status != "active"
                or record.lease_id != lease_id
                or record.controller_id != controller_id
                or record.generation != expected_generation
            ):
                self._counters[root_entity_id]["blocked_command"] += 1
                return {
                    "outcome": "blocked",
                    "blockers": ["inactive_or_mismatched_lease"],
                }
            runtime_cause = (
                "expiry" if self._monotonic() >= record.expires_monotonic
                else self._runtime_break_cause(record)
            )
            if runtime_cause is not None:
                broken_reason = runtime_cause
                broken_transition = self._break_locked(
                    root_entity_id,
                    cause=broken_reason,
                    direction=(
                        "off" if broken_reason == "target_unavailable" else "none"
                    ),
                    context_classification="control_lease",
                )
            else:
                allowed_targets = tuple(
                    target
                    for target in requested_targets
                    if target in set(record.target_entity_ids)
                )
                if not allowed_targets:
                    self._counters[root_entity_id]["blocked_command"] += 1
                    return {
                        "outcome": "blocked",
                        "blockers": ["no_authorized_targets"],
                    }
                if len(self._in_flight_context_ids) >= 256:
                    return {"outcome": "blocked", "blockers": ["command_capacity_exceeded"]}
                context = Context()
                command_record = LeaseCommandRecord(
                    context_id=context.id,
                    root_entity_id=root_entity_id,
                    lease_id=lease_id,
                    controller_id=controller_id,
                    generation=record.generation,
                    target_entity_ids=allowed_targets,
                    direction="on",
                    command_id=command_id,
                    created_monotonic=self._monotonic(),
                )
                self._register_context(command_record, context)
                self._in_flight_context_ids.add(context.id)

        if broken_transition is not None:
            broken_record, transition = broken_transition
            self._cancel_expiry(root_entity_id)
            self._schedule_save()
            self._emit_recorded_transition(broken_record, transition, logging.WARNING)
            self._notify(root_entity_id, transition)
            return {
                "outcome": "blocked",
                "blockers": [broken_reason],
            }

        call_data = dict(service_data)
        call_data["entity_id"] = list(allowed_targets)
        try:
            await self.hass.services.async_call(
                "light",
                "turn_on",
                call_data,
                blocking=True,
                context=context,
            )
        finally:
            self._in_flight_context_ids.discard(context.id)
        current = self._leases.get(root_entity_id)
        still_current = bool(
            current
            and current.status == "active"
            and current.lease_id == lease_id
            and current.generation == command_record.generation
        )
        return {
            "outcome": "dispatched" if still_current else "revoked_in_flight",
            "lease_id": lease_id,
            "generation": command_record.generation,
            "target_entity_ids": list(allowed_targets),
        }

    def guard_control_lease_command(
        self,
        context: Context | None,
        *,
        service: str,
        target_entity_ids: list[str] | tuple[str, ...],
        service_data: dict[str, Any],
    ) -> GuardDecision | None:
        """Validate one exact owner context immediately before service dispatch."""
        command = self._context_for(context)
        if command is None:
            return None
        record = self._leases.get(command.root_entity_id)
        if record is not None and self._monotonic() >= record.expires_monotonic:
            self.break_nowait(
                command.root_entity_id, cause="expiry", direction="none",
                context_classification="watchdog",
            )
            record = None
        if (
            service != "turn_on"
            or command.direction != "on"
            or record is None
            or record.status != "active"
            or record.lease_id != command.lease_id
            or record.generation != command.generation
        ):
            self._counters[command.root_entity_id]["blocked_command"] += 1
            return GuardDecision(
                owned=True,
                allowed=False,
                reason="stale_control_lease_context",
            )
        if not self._has_absolute_brightness(service_data):
            self._counters[command.root_entity_id]["blocked_command"] += 1
            return GuardDecision(
                owned=True,
                allowed=False,
                reason="absolute_brightness_required",
            )
        allowed = tuple(
            target
            for target in target_entity_ids
            if target in set(record.target_entity_ids)
            and target in set(command.target_entity_ids)
        )
        if not allowed:
            self._counters[command.root_entity_id]["blocked_command"] += 1
            return GuardDecision(
                owned=True,
                allowed=False,
                reason="no_authorized_targets",
            )
        return GuardDecision(owned=True, allowed=True, target_entity_ids=allowed)

    async def async_break_for_off_targets(
        self,
        target_entity_ids: list[str] | tuple[str, ...],
        *,
        context: Context | None,
    ) -> list[str]:
        """Break leases when a foreign OFF addresses a root or every target leaf."""
        targets = expand_structural_targets(self.hass, target_entity_ids)
        broken: list[str] = []
        candidates = {**self._observed, **self._leases}
        for root_entity_id, record in list(candidates.items()):
            if self._context_is_pbl_owned(
                root_entity_id,
                context,
                expected_target_state="off",
                additional_entity_ids=record.target_entity_ids,
            ):
                continue
            if "all" not in targets and root_entity_id not in targets and not set(
                record.target_entity_ids
            ).issubset(targets):
                continue
            classification = self.classify_external_context(context)
            if await self.async_break(
                root_entity_id,
                cause="manual_group_off",
                direction="off",
                context_classification=classification,
            ):
                broken.append(root_entity_id)
                if classification == "unknown":
                    _LOGGER.warning(
                        "Unknown-source OFF revoked control lease for %s",
                        root_entity_id,
                    )
        return broken

    async def async_break_for_brightness_targets(
        self,
        target_entity_ids: list[str] | tuple[str, ...] | set[str],
        *,
        service_data: dict[str, Any],
        context: Context | None,
    ) -> list[str]:
        """Revoke a room lease before a foreign explicit brightness command."""
        if not self.service_data_has_brightness_authority(service_data):
            return []

        targets = expand_structural_targets(self.hass, target_entity_ids)
        broken: list[str] = []
        candidates = {**self._observed, **self._leases}
        for root_entity_id, record in list(candidates.items()):
            if "all" not in targets and root_entity_id not in targets and not targets.intersection(
                record.target_entity_ids
            ):
                continue
            if self._context_is_pbl_owned(
                root_entity_id,
                context,
                expected_target_state="on",
                additional_entity_ids=record.target_entity_ids,
            ):
                continue
            enforced = root_entity_id in self._leases
            classification = self.classify_external_context(context)
            if await self.async_break(
                root_entity_id,
                cause="manual_brightness_control",
                direction="on",
                context_classification=classification,
            ):
                broken.append(root_entity_id)
                if enforced:
                    self._remember_manual_authority_context(
                        root_entity_id,
                        context,
                        additional_entity_ids=record.target_entity_ids,
                        preserve_baseline=bool(record.baseline_suppressions),
                    )
        return broken

    def is_manual_authority_context(
        self,
        root_entity_id: str,
        context: Context | None,
    ) -> bool:
        """Return whether this call just pre-revoked a lease for user authority."""
        record = self._manual_authority_record_for_context(context)
        return bool(record and root_entity_id in record.bypass_entity_ids)

    def manual_authority_roots(
        self,
        context: Context | None,
    ) -> tuple[str, ...]:
        """Return room roots whose leases this foreign context pre-revoked."""
        record = self._manual_authority_record_for_context(context)
        if record is None:
            return ()
        return tuple(sorted(record.root_entity_ids))

    def claim_manual_authority_policy(
        self,
        root_entity_id: str,
        context: Context | None,
    ) -> str | None:
        """Claim the one service-path policy action for a pre-revoked root."""
        record = self._manual_authority_record_for_context(context)
        if (
            record is None
            or root_entity_id not in record.root_entity_ids
            or root_entity_id in record.handled_root_entity_ids
        ):
            return None
        record.handled_root_entity_ids.add(root_entity_id)
        if root_entity_id in record.preserve_baseline_root_entity_ids:
            return "preserve_baseline"
        return "apply"

    def note_controlled_state(
        self,
        root_entity_id: str,
        state: str,
        context: Context | None,
    ) -> None:
        """Record a late exact owner ON echo after an off-direction revocation."""
        if state != "on":
            return
        command = self._context_for(context)
        if command is None or command.root_entity_id != root_entity_id:
            return
        if command.late_echo_reported:
            return
        terminal = self._terminal_by_id.get(self._correlation_ref(command.lease_id))
        if terminal is None or terminal.get("direction") != "off":
            return
        if terminal.get("cause") not in _OFF_BREAK_CAUSES:
            return
        if (
            self._monotonic() - command.created_monotonic
            > CONTROL_LEASE_CONFIRMATION_WINDOW_SECONDS
        ):
            return
        command.late_echo_reported = True
        self._counters[root_entity_id]["late_owner_echo"] += 1
        _LOGGER.warning(
            "Late lease-owned ON converged after revoke for %s (lease_ref=%s)",
            root_entity_id,
            self._correlation_ref(command.lease_id),
        )
        self._record_simple_transition(
            root_entity_id,
            action="late_owner_echo",
            outcome="observed",
            cause=str(terminal.get("cause")),
            context_classification="control_lease",
            level=logging.WARNING,
            record=command,
        )
        if not self._corrective_off_enabled(root_entity_id):
            return
        break_key = (
            self._correlation_ref(command.lease_id) or "",
            int(terminal.get("generation") or 0),
        )
        if break_key in self._corrected_breaks:
            return
        self._corrected_breaks.append(break_key)
        targets = [
            entity_id
            for entity_id in command.target_entity_ids
            if getattr(self.hass.states.get(entity_id), "state", None) == "on"
        ]
        if not targets:
            return

        async def _correct() -> None:
            correction_context = Context(parent_id=command.context_id)
            registry = get_command_context_registry(self.hass)
            registry.register(
                correction_context.id,
                f"{DOMAIN}:control_lease_correction",
                root_entity_id,
                "off",
            )
            await self.hass.services.async_call(
                "light",
                "turn_off",
                {"entity_id": targets},
                blocking=True,
                context=correction_context,
            )

        self._create_task(_correct())

    # ------------------------------------------------------------------
    # Attributes and diagnostics
    # ------------------------------------------------------------------

    def attributes_for(self, root_entity_id: str) -> dict[str, Any]:
        """Return bounded state attributes for one controlled root."""
        record = self._leases.get(root_entity_id) or self._observed.get(root_entity_id)
        last = self._last_transition.get(root_entity_id, {})
        counters = self._counters[root_entity_id]
        return {
            "control_lease_mode": self.effective_mode(root_entity_id),
            "control_lease_state": record.status if record else "inactive",
            "control_lease_owner": record.owner if record else last.get("owner"),
            "control_lease_id": (
                self._correlation_ref(record.lease_id)
                if record
                else last.get("lease_ref")
            ),
            "control_lease_controller_id": (
                self._correlation_ref(record.controller_id)
                if record
                else last.get("controller_ref")
            ),
            "control_lease_occurrence_ids": (
                self._occurrence_refs(record.occurrence_ids)
                if record
                else list(last.get("occurrence_refs") or [])
            ),
            "control_lease_request_id": (
                self._correlation_ref(record.request_id)
                if record
                else last.get("request_ref")
            ),
            "control_lease_acquired_at": (
                record.acquired_at if record else last.get("acquired_at")
            ),
            "control_lease_expires_at": (
                record.expires_at if record else last.get("expires_at")
            ),
            "control_lease_target_entity_ids": (
                list(record.target_entity_ids)
                if record
                else list(last.get("target_entity_ids") or [])
            ),
            "control_lease_released_entity_ids": (
                list(record.released_entity_ids)
                if record
                else list(last.get("released_entity_ids") or [])
            ),
            "control_lease_generation": (
                record.generation
                if record
                else last.get("generation", self._generations[root_entity_id])
            ),
            "control_lease_last_transition": last.get("action"),
            "control_lease_last_outcome": last.get("outcome"),
            "control_lease_last_context": last.get("context_classification"),
            "control_lease_baseline_admitted": (
                bool(record.baseline_suppressions)
                if record
                else bool(last.get("baseline_admitted"))
            ),
            "control_lease_baseline_outcome": (
                record.baseline_outcome if record else last.get("baseline_outcome")
            ),
            "control_lease_watchdog_state": (
                "recovery_grace"
                if record and record.status == "recovering"
                else "armed" if record and record.status == "active" else "inactive"
            ),
            "control_lease_watchdog_due_at": (
                record.recovery_deadline_at
                if record and record.status == "recovering"
                else record.expires_at if record and record.status == "active" else None
            ),
            "control_lease_active_count": (
                1 if record and record.status in {"active", "recovering"} else 0
            ),
            "control_lease_denial_count": counters["denial"],
            "control_lease_token_mismatch_count": counters["token_mismatch"],
            "control_lease_blocked_command_count": counters["blocked_command"],
            "control_lease_target_unavailable_count": counters["target_unavailable"],
        }

    def diagnostics_for_entry(self, entry_id: str) -> dict[str, Any]:
        """Return redacted bounded diagnostics for roots owned by one entry."""
        roots = [
            root
            for root, registrations in self._registrations.items()
            if entry_id in registrations
        ]
        return {
            "schema_version": CONTROL_LEASE_SCHEMA_VERSION,
            "roots": {root: self.attributes_for(root) for root in sorted(roots)},
            "terminal": [
                {
                    key: value
                    for key, value in summary.items()
                    if key not in {"context_id"}
                }
                for summary in self._terminal
                if summary.get("root_entity_id") in roots
            ],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_request(
        self,
        *,
        root_entity_id: str,
        lease_id: str,
        controller_id: str,
        request_id: str,
        owner: str,
        occurrence_ids: list[str] | tuple[str, ...],
        ttl_seconds: float,
        target_entity_ids: list[str] | tuple[str, ...],
    ) -> list[str]:
        errors: list[str] = []
        for value, error in (
            (lease_id, "invalid_lease_id"),
            (controller_id, "invalid_controller_id"),
            (request_id, "invalid_request_id"),
        ):
            if not isinstance(value, str) or not _OPAQUE_ID_RE.fullmatch(value):
                errors.append(error)
        if owner not in CONTROL_LEASE_ALLOWED_OWNERS:
            errors.append("invalid_owner")
        if not isinstance(root_entity_id, str) or not root_entity_id.startswith(
            "light."
        ):
            errors.append("invalid_root_entity_id")
        try:
            occurrence_set = set(occurrence_ids)
        except (TypeError, ValueError):
            occurrence_set = set()
        if (
            not occurrence_set
            or len(occurrence_set) > CONTROL_LEASE_MAX_OCCURRENCE_IDS
            or any(
                not isinstance(value, str) or not _OPAQUE_ID_RE.fullmatch(value)
                for value in occurrence_set
            )
        ):
            errors.append("invalid_occurrence_ids")
        try:
            target_set = set(target_entity_ids)
        except (TypeError, ValueError):
            target_set = set()
        if (
            not target_set
            or len(target_set) > CONTROL_LEASE_MAX_TARGET_ENTITY_IDS
            or any(
                not isinstance(value, str) or not value.startswith("light.")
                for value in target_set
            )
        ):
            errors.append("invalid_target_entity_ids")
        try:
            ttl = float(ttl_seconds)
        except (TypeError, ValueError):
            ttl = 0
        if not CONTROL_LEASE_MIN_TTL_SECONDS <= ttl <= CONTROL_LEASE_MAX_TTL_SECONDS:
            errors.append("invalid_ttl")
        return sorted(set(errors))

    def _acquisition_blockers(
        self,
        root_entity_id: str,
        targets: tuple[str, ...],
        *,
        require_enforcement: bool,
    ) -> list[str]:
        blockers: list[str] = []
        if require_enforcement and not self._enforcement_available:
            blockers.append("context_enforcement_unavailable")
        members, membership_error = self._resolve_members(root_entity_id)
        if membership_error:
            blockers.append(membership_error)
        elif not set(targets).issubset(members):
            blockers.append("target_membership_mismatch")
        unavailable = self._unavailable_targets(targets)
        if unavailable:
            blockers.extend(
                f"target_unavailable:{entity_id}" for entity_id in unavailable
            )
            self._counters[root_entity_id]["target_unavailable"] += len(unavailable)
        for registration in self._registrations.get(root_entity_id, {}).values():
            if registration.blocker_callback is not None:
                blockers.extend(registration.blocker_callback())
            for blocker_entity_id in registration.blocker_entity_ids:
                state = self.hass.states.get(blocker_entity_id)
                if state is None or state.state != "off":
                    blockers.append(f"external_blocker:{blocker_entity_id}")
        return sorted(set(blockers))

    def _runtime_break_cause(
        self,
        record: ControlLeaseRecord,
    ) -> str | None:
        if record.status == "active" and not self._enforcement_available:
            return "context_enforcement_unavailable"
        members, membership_error = self._resolve_members(record.root_entity_id)
        if membership_error is not None:
            return membership_error
        if not set(record.target_entity_ids).issubset(members):
            return "target_membership_mismatch"
        if self._unavailable_targets(record.target_entity_ids):
            self._counters[record.root_entity_id]["target_unavailable"] += 1
            return "target_unavailable"
        for registration in self._registrations.get(record.root_entity_id, {}).values():
            if registration.blocker_callback is not None:
                blockers = registration.blocker_callback()
                if blockers:
                    return "admin_conflict"
            for blocker_entity_id in registration.blocker_entity_ids:
                blocker = self.hass.states.get(blocker_entity_id)
                if blocker is None or blocker.state != "off":
                    return "external_blocker_active"
        if (
            self._current_baseline_suppressions(record.root_entity_id)
            != record.baseline_suppressions
        ):
            return "baseline_suppression_changed"
        return None

    def _current_baseline_suppressions(
        self,
        root_entity_id: str,
    ) -> tuple[BaselineSuppressionFingerprint, ...]:
        fingerprints = []
        for registration in self._registrations.get(root_entity_id, {}).values():
            if registration.baseline_callback is None:
                continue
            fingerprint = registration.baseline_callback()
            if fingerprint is not None:
                fingerprints.append(fingerprint)
        return tuple(sorted(fingerprints, key=lambda item: item.entry_id))

    def _complete_admitted_baseline(self, record: ControlLeaseRecord) -> str:
        for entry_id in sorted(self._registrations.get(record.root_entity_id, {})):
            callback = self._registrations[record.root_entity_id][
                entry_id
            ].completion_callback
            if callback is None:
                continue
            try:
                return callback(
                    record.root_entity_id,
                    record.baseline_suppressions,
                )
            except Exception as err:
                _LOGGER.exception(
                    "Control lease baseline completion failed for %s: %s",
                    record.root_entity_id,
                    err,
                )
                return "baseline_preserved_error"
        return "baseline_preserved_no_handler"

    def _resolve_members(self, root_entity_id: str) -> tuple[set[str], str | None]:
        root = self.hass.states.get(root_entity_id)
        if root is None or root.state in _UNAVAILABLE_STATES:
            return set(), "root_unavailable"
        leaves: set[str] = set()
        visiting: set[str] = set()
        resolved: set[str] = set()

        def _walk(entity_id: str) -> bool:
            if entity_id in resolved:
                return True
            if entity_id in visiting:
                return False
            visiting.add(entity_id)
            state = self.hass.states.get(entity_id)
            if state is None:
                visiting.discard(entity_id)
                return False
            attributes = getattr(state, "attributes", None) or {}
            members: list[str] = []
            for key in ("entity_id", "group_entities"):
                value = attributes.get(key)
                if isinstance(value, str):
                    members.append(value)
                elif isinstance(value, (list, tuple, set)):
                    members.extend(item for item in value if isinstance(item, str))
            if not members:
                leaves.add(entity_id)
                visiting.discard(entity_id)
                resolved.add(entity_id)
                return True
            valid = all(_walk(member) for member in members)
            visiting.discard(entity_id)
            if valid:
                resolved.add(entity_id)
            return valid

        if not _walk(root_entity_id):
            return set(), "membership_ambiguous"
        return leaves, None

    def _unavailable_targets(self, target_entity_ids: tuple[str, ...]) -> list[str]:
        return [
            entity_id
            for entity_id in target_entity_ids
            if (
                self.hass.states.get(entity_id) is None
                or self.hass.states.get(entity_id).state in _UNAVAILABLE_STATES
            )
        ]

    @staticmethod
    def _has_absolute_brightness(service_data: dict[str, Any]) -> bool:
        for key in ("brightness", "brightness_pct"):
            value = service_data.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return True
        return False

    @staticmethod
    def service_data_has_brightness_authority(service_data: Any) -> bool:
        """Return whether turn-on data explicitly claims brightness authority."""
        if not isinstance(service_data, dict):
            return False
        return any(
            key in service_data and service_data.get(key) is not None
            for key in _BRIGHTNESS_AUTHORITY_KEYS
        )

    @staticmethod
    def _service_data_is_bounded(service_data: Any) -> bool:
        if not isinstance(service_data, dict):
            return False
        if set(service_data) - _ALLOWED_LEASE_SERVICE_DATA:
            return False
        limits = {
            "brightness": (1, 255),
            "brightness_pct": (1, 100),
            "transition": (0, 60),
            "color_temp_kelvin": (1000, 10000),
        }
        for key, value in service_data.items():
            if not isinstance(value, (int, float)):
                return False
            minimum, maximum = limits[key]
            if not minimum <= value <= maximum:
                return False
        return True

    def _register_context(
        self,
        record: LeaseCommandRecord,
        context: Context,
    ) -> None:
        self._purge_contexts()
        self._contexts[record.context_id] = record
        while len(self._contexts) > 256:
            disposable = next(
                (key for key in self._contexts if key not in self._in_flight_context_ids),
                None,
            )
            if disposable is None:
                break
            self._contexts.pop(disposable)
        registry = get_command_context_registry(self.hass)
        registry.register_control_lease(
            context.id,
            record.lease_id,
            record.root_entity_id,
            "on",
            record.generation,
        )
        for target_entity_id in record.target_entity_ids:
            registry.register_control_lease(
                context.id,
                record.lease_id,
                target_entity_id,
                "on",
                record.generation,
            )

    def _context_for(self, context: Context | None) -> LeaseCommandRecord | None:
        if context is None:
            return None
        self._purge_contexts()
        for context_id in (
            getattr(context, "id", None),
            getattr(context, "parent_id", None),
        ):
            if context_id and context_id in self._contexts:
                return self._contexts[context_id]
        return None

    def _purge_contexts(self) -> None:
        cutoff = self._monotonic() - CONTROL_LEASE_CONTEXT_TTL_SECONDS
        for context_id, record in list(self._contexts.items()):
            if record.created_monotonic >= cutoff:
                break
            if context_id in self._in_flight_context_ids:
                continue
            self._contexts.pop(context_id, None)

    def _context_is_pbl_owned(
        self,
        root_entity_id: str,
        context: Context | None,
        *,
        expected_target_state: str,
        additional_entity_ids: tuple[str, ...] = (),
    ) -> bool:
        registry = get_command_context_registry(self.hass)
        return any(
            registry.record_for(
                entity_id,
                context,
                include_parent=True,
                expected_target_state=expected_target_state,
            )
            is not None
            for entity_id in {root_entity_id, *additional_entity_ids}
        )

    @staticmethod
    def classify_external_context(context: Context | None) -> str:
        if context is None:
            return "unknown"
        if getattr(context, "user_id", None):
            return "user"
        if getattr(context, "parent_id", None):
            return "automation_or_system"
        return "unknown"

    def _corrective_off_enabled(self, root_entity_id: str) -> bool:
        return any(
            registration.correct_late_on
            for registration in self._registrations.get(root_entity_id, {}).values()
        )

    def _next_generation(self, root_entity_id: str) -> int:
        self._generations[root_entity_id] += 1
        return self._generations[root_entity_id]

    def _remember_manual_authority_context(
        self,
        root_entity_id: str,
        context: Context | None,
        *,
        additional_entity_ids: tuple[str, ...] = (),
        preserve_baseline: bool = False,
    ) -> None:
        context_id = getattr(context, "id", None) if context is not None else None
        if not context_id:
            return
        self._purge_manual_authority_contexts()
        record = self._manual_authority_contexts.pop(context_id, None)
        if record is None:
            record = ManualAuthorityContextRecord(
                created_monotonic=self._monotonic(),
                root_entity_ids=set(),
                bypass_entity_ids=set(),
                handled_root_entity_ids=set(),
                preserve_baseline_root_entity_ids=set(),
            )
        record.created_monotonic = self._monotonic()
        record.root_entity_ids.add(root_entity_id)
        record.bypass_entity_ids.add(root_entity_id)
        record.bypass_entity_ids.update(additional_entity_ids)
        if preserve_baseline:
            record.preserve_baseline_root_entity_ids.add(root_entity_id)
        self._manual_authority_contexts[context_id] = record
        while len(self._manual_authority_contexts) > 256:
            self._manual_authority_contexts.popitem(last=False)

    def _manual_authority_record_for_context(
        self,
        context: Context | None,
    ) -> ManualAuthorityContextRecord | None:
        if context is None:
            return None
        self._purge_manual_authority_contexts()
        for context_id in (
            getattr(context, "id", None),
            getattr(context, "parent_id", None),
        ):
            if not context_id:
                continue
            record = self._manual_authority_contexts.get(context_id)
            if record is not None:
                return record
        return None

    def _purge_manual_authority_contexts(self) -> None:
        cutoff = self._monotonic() - CONTROL_LEASE_CONTEXT_TTL_SECONDS
        for context_id, record in list(self._manual_authority_contexts.items()):
            if record.created_monotonic >= cutoff:
                break
            self._manual_authority_contexts.pop(context_id, None)

    def _correlation_ref(self, value: str | None) -> str | None:
        if not value:
            return None
        digest = hashlib.sha256(
            f"{self._correlation_salt}:{value}".encode("utf-8")
        ).hexdigest()
        return digest[:12]

    def _occurrence_refs(self, values: tuple[str, ...]) -> list[str]:
        return [
            correlation
            for value in values
            if (correlation := self._correlation_ref(value)) is not None
        ]

    def _terminalize_locked(
        self,
        record: ControlLeaseRecord,
        *,
        action: str,
        outcome: str,
        cause: str,
        context_classification: str,
        direction: str = "none",
    ) -> tuple[ControlLeaseRecord, dict[str, Any]]:
        self._leases.pop(record.root_entity_id, None)
        generation = self._next_generation(record.root_entity_id)
        terminal_record = replace(
            record,
            status=action,
            generation=generation,
            previous_generation=record.generation,
            last_transition=action,
            last_outcome=outcome,
            last_context_classification=context_classification,
            baseline_outcome=(
                "baseline_preserved_revoke"
                if action == "revoked" and record.baseline_suppressions
                else record.baseline_outcome
            ),
        )
        summary = self._terminal_summary(
            terminal_record,
            action=action,
            outcome=outcome,
            cause=cause,
            terminal_at=self._utcnow(),
            direction=direction,
        )
        self._append_terminal(summary)
        return terminal_record, summary

    def _append_terminal(self, summary: dict[str, Any]) -> None:
        if len(self._terminal) == self._terminal.maxlen and self._terminal:
            oldest = self._terminal[0]
            oldest_ref = oldest.get("lease_ref")
            if oldest_ref:
                self._terminal_by_id.pop(oldest_ref, None)
        self._terminal.append(summary)
        lease_ref = summary.get("lease_ref")
        if lease_ref:
            self._terminal_by_id[lease_ref] = summary

    def _terminal_summary(
        self,
        record: ControlLeaseRecord,
        *,
        action: str,
        outcome: str,
        cause: str,
        terminal_at: datetime,
        direction: str = "none",
    ) -> dict[str, Any]:
        return {
            "lease_ref": self._correlation_ref(record.lease_id),
            "root_entity_id": record.root_entity_id,
            "controller_ref": self._correlation_ref(record.controller_id),
            "owner": record.owner,
            "occurrence_refs": self._occurrence_refs(record.occurrence_ids),
            "request_ref": self._correlation_ref(record.request_id),
            "target_entity_ids": list(record.target_entity_ids),
            "released_entity_ids": list(record.released_entity_ids),
            "acquired_at": record.acquired_at,
            "expires_at": record.expires_at,
            "terminal_at": terminal_at.isoformat(),
            "action": action,
            "outcome": outcome,
            "cause": cause,
            "direction": direction,
            "generation": record.generation,
            "previous_generation": record.previous_generation,
            "context_classification": record.last_context_classification,
            "baseline_admitted": bool(record.baseline_suppressions),
            "baseline_outcome": record.baseline_outcome,
        }

    def _record_to_storage(self, record: ControlLeaseRecord) -> dict[str, Any]:
        return {
            "root_entity_id": record.root_entity_id,
            "lease_id": record.lease_id,
            "controller_id": record.controller_id,
            "request_id": record.request_id,
            "owner": record.owner,
            "occurrence_ids": list(record.occurrence_ids),
            "target_entity_ids": list(record.target_entity_ids),
            "known_target_entity_ids": list(record.known_target_entity_ids),
            "baseline_suppressions": [
                {
                    "entry_id": fingerprint.entry_id,
                    "override_source": fingerprint.override_source,
                    "override_policy": fingerprint.override_policy,
                    "override_created_at": fingerprint.override_created_at,
                    "pause_source": fingerprint.pause_source,
                    "pause_paused_at": fingerprint.pause_paused_at,
                }
                for fingerprint in record.baseline_suppressions
            ],
            "acquired_at": record.acquired_at,
            "expires_at": record.expires_at,
            "generation": record.generation,
        }

    def _record_from_storage(
        self,
        payload: dict[str, Any],
        *,
        now_dt: datetime,
        now_monotonic: float,
    ) -> ControlLeaseRecord | None:
        acquired_dt = _parse_datetime(payload.get("acquired_at"))
        expires_dt = _parse_datetime(payload.get("expires_at"))
        if acquired_dt is None or expires_dt is None:
            return None
        original_ttl = max(0.0, (expires_dt - acquired_dt).total_seconds())
        remaining = min(
            original_ttl,
            max(0.0, (expires_dt - now_dt).total_seconds()),
        )
        elapsed = max(0.0, (now_dt - acquired_dt).total_seconds())
        try:
            return ControlLeaseRecord(
                root_entity_id=str(payload["root_entity_id"]),
                lease_id=str(payload["lease_id"]),
                controller_id=str(payload["controller_id"]),
                request_id=str(payload["request_id"]),
                owner=str(payload["owner"]),
                occurrence_ids=tuple(sorted(set(payload["occurrence_ids"]))),
                target_entity_ids=tuple(sorted(set(payload["target_entity_ids"]))),
                known_target_entity_ids=tuple(
                    sorted(
                        set(
                            payload.get("known_target_entity_ids")
                            or payload["target_entity_ids"]
                        )
                    )
                ),
                baseline_suppressions=tuple(
                    BaselineSuppressionFingerprint(
                        entry_id=str(item["entry_id"]),
                        override_source=str(item["override_source"]),
                        override_policy=str(item["override_policy"]),
                        override_created_at=str(item["override_created_at"]),
                        pause_source=str(item["pause_source"]),
                        pause_paused_at=str(item["pause_paused_at"]),
                    )
                    for item in payload.get("baseline_suppressions") or []
                ),
                acquired_dt=acquired_dt,
                expires_dt=expires_dt,
                acquired_monotonic=now_monotonic - elapsed,
                expires_monotonic=now_monotonic + remaining,
                status="recovering",
                generation=int(payload.get("generation") or 1),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _record_transition(
        self,
        record: ControlLeaseRecord,
        *,
        action: str,
        outcome: str,
        cause: str,
        level: int,
        conflict: str | None = None,
        context_classification: str | None = None,
    ) -> dict[str, Any]:
        transition = self._transition_data(
            record,
            action=action,
            outcome=outcome,
            cause=cause,
            conflict=conflict,
            context_classification=context_classification,
        )
        self._emit_recorded_transition(record, transition, level)
        return transition

    def _transition_data(
        self,
        record: ControlLeaseRecord,
        *,
        action: str,
        outcome: str,
        cause: str,
        conflict: str | None = None,
        context_classification: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": CONTROL_LEASE_SCHEMA_VERSION,
            "action": action,
            "outcome": outcome,
            "cause": cause,
            "conflict_ref": self._correlation_ref(conflict),
            "lease_ref": self._correlation_ref(record.lease_id),
            "controller_ref": self._correlation_ref(record.controller_id),
            "owner": record.owner,
            "occurrence_refs": self._occurrence_refs(record.occurrence_ids),
            "request_ref": self._correlation_ref(record.request_id),
            "root_entity_id": record.root_entity_id,
            "target_entity_ids": list(record.target_entity_ids),
            "released_entity_ids": list(record.released_entity_ids),
            "acquired_at": record.acquired_at,
            "expires_at": record.expires_at,
            "generation": record.generation,
            "previous_generation": record.previous_generation,
            "context_classification": context_classification
            or record.last_context_classification,
            "baseline_admitted": bool(record.baseline_suppressions),
            "baseline_outcome": record.baseline_outcome,
            "active_count": (
                1 if record.status in {"active", "recovering", "persisting"} else 0
            ),
        }

    def _emit_recorded_transition(
        self,
        record: ControlLeaseRecord,
        transition: dict[str, Any],
        level: int,
    ) -> None:
        self._last_transition[record.root_entity_id] = dict(transition)
        self._fire(EVENT_CONTROL_TRANSITION, transition)
        if transition["action"] == "revoked":
            self._fire(
                EVENT_CONTROL_LEASE_REVOKED,
                {
                    "schema_version": CONTROL_LEASE_SCHEMA_VERSION,
                    "lease_ref": transition["lease_ref"],
                    "controller_ref": transition["controller_ref"],
                    "occurrence_refs": transition["occurrence_refs"],
                    "root_entity_id": record.root_entity_id,
                    "cause": transition["cause"],
                    "generation": transition["generation"],
                    "previous_generation": transition["previous_generation"],
                    "context_classification": transition["context_classification"],
                    "authority_policy": (
                        "conservative_external_control"
                        if transition["cause"] in {
                            "manual_group_off", "manual_brightness_control",
                            "bulk_off", "controlled_entity_off",
                        }
                        else "owner_safety"
                    ),
                    "baseline_admitted": transition["baseline_admitted"],
                    "baseline_outcome": transition["baseline_outcome"],
                },
            )
        _LOGGER.log(
            level,
            "Control lease %s for %s: action=%s outcome=%s cause=%s",
            transition["lease_ref"],
            record.root_entity_id,
            transition["action"],
            transition["outcome"],
            transition["cause"],
        )

    def _record_simple_transition(
        self,
        root_entity_id: str,
        *,
        action: str,
        outcome: str,
        cause: str,
        context_classification: str,
        level: int,
        record: LeaseCommandRecord | None = None,
    ) -> None:
        lease = self._leases.get(root_entity_id)
        if lease is not None:
            transition = self._record_transition(
                lease,
                action=action,
                outcome=outcome,
                cause=cause,
                context_classification=context_classification,
                level=level,
            )
        else:
            terminal = (
                self._terminal_by_id.get(self._correlation_ref(record.lease_id))
                if record is not None
                else None
            )
            transition = {
                "schema_version": CONTROL_LEASE_SCHEMA_VERSION,
                "action": action,
                "outcome": outcome,
                "cause": cause,
                "conflict_ref": None,
                "lease_ref": self._correlation_ref(record.lease_id) if record else None,
                "controller_ref": (
                    self._correlation_ref(record.controller_id) if record else None
                ),
                "owner": terminal.get("owner") if terminal else None,
                "occurrence_refs": (
                    terminal.get("occurrence_refs", []) if terminal else []
                ),
                "request_ref": terminal.get("request_ref") if terminal else None,
                "root_entity_id": root_entity_id,
                "target_entity_ids": list(record.target_entity_ids) if record else [],
                "released_entity_ids": (
                    terminal.get("released_entity_ids", []) if terminal else []
                ),
                "acquired_at": terminal.get("acquired_at") if terminal else None,
                "expires_at": terminal.get("expires_at") if terminal else None,
                "generation": record.generation if record else None,
                "previous_generation": (
                    terminal.get("previous_generation") if terminal else None
                ),
                "context_classification": context_classification,
                "baseline_admitted": (
                    terminal.get("baseline_admitted", False) if terminal else False
                ),
                "baseline_outcome": (
                    terminal.get("baseline_outcome", "none") if terminal else "none"
                ),
                "active_count": 0,
            }
            self._last_transition[root_entity_id] = dict(transition)
            self._fire(EVENT_CONTROL_TRANSITION, transition)
            _LOGGER.log(
                level,
                "Control lease command for %s: action=%s outcome=%s cause=%s",
                root_entity_id,
                action,
                outcome,
                cause,
            )
        self._notify(root_entity_id, transition)

    def _deny(
        self,
        root_entity_id: str,
        *,
        lease_id: str,
        controller_id: str,
        request_id: str,
        owner: str,
        occurrence_ids: Any,
        target_entity_ids: Any,
        blockers: list[str],
        outcome: str,
        conflict: str | None = None,
    ) -> dict[str, Any]:
        self._counters[root_entity_id]["denial"] += 1
        now_dt = self._utcnow()
        record = ControlLeaseRecord(
            root_entity_id=root_entity_id,
            lease_id=lease_id,
            controller_id=controller_id,
            request_id=request_id,
            owner=owner,
            occurrence_ids=self._safe_string_tuple(occurrence_ids),
            target_entity_ids=self._safe_string_tuple(target_entity_ids),
            known_target_entity_ids=self._safe_string_tuple(target_entity_ids),
            acquired_dt=now_dt,
            expires_dt=now_dt,
            acquired_monotonic=self._monotonic(),
            expires_monotonic=self._monotonic(),
            status="denied",
            generation=self._generations[root_entity_id],
            last_transition="denied",
            last_outcome=outcome,
        )
        transition = self._record_transition(
            record,
            action="denied",
            outcome=outcome,
            cause=blockers[0] if blockers else "unknown",
            conflict=conflict,
            level=logging.WARNING,
        )
        self._notify(root_entity_id, transition)
        return {
            "outcome": outcome,
            "lease_id": None,
            "requested_lease_id": lease_id,
            "root_entity_id": root_entity_id,
            "controller_id": controller_id,
            "request_id": request_id,
            "blockers": blockers,
            "conflict": conflict,
        }

    @staticmethod
    def _safe_string_tuple(values: Any) -> tuple[str, ...]:
        if not isinstance(values, (list, tuple, set)):
            return ()
        return tuple(sorted({value for value in values if isinstance(value, str)}))

    @staticmethod
    def _bounded_known_targets(
        existing: tuple[str, ...],
        current: tuple[str, ...],
    ) -> tuple[str, ...]:
        values = list(current)
        values.extend(value for value in existing if value not in set(current))
        return tuple(values[:CONTROL_LEASE_MAX_TARGET_ENTITY_IDS])

    def _token_mismatch(
        self,
        root_entity_id: str,
        *,
        lease_id: str,
        request_id: str,
        outcome: str,
    ) -> dict[str, Any]:
        self._counters[root_entity_id]["token_mismatch"] += 1
        _LOGGER.warning(
            "Control lease token mismatch for %s (lease_ref=%s)",
            root_entity_id,
            self._correlation_ref(lease_id),
        )
        self._record_simple_transition(
            root_entity_id,
            action="release_denied",
            outcome=outcome,
            cause="token_mismatch",
            context_classification="owner_service",
            level=logging.WARNING,
        )
        return {
            "outcome": outcome,
            "lease_id": lease_id,
            "root_entity_id": root_entity_id,
            "request_id": request_id,
        }

    @staticmethod
    def _result(record: ControlLeaseRecord, *, outcome: str) -> dict[str, Any]:
        return {
            "outcome": outcome,
            "lease_id": record.lease_id,
            "root_entity_id": record.root_entity_id,
            "controller_id": record.controller_id,
            "owner": record.owner,
            "request_id": record.request_id,
            "occurrence_ids": list(record.occurrence_ids),
            "target_entity_ids": list(record.target_entity_ids),
            "released_entity_ids": list(record.released_entity_ids),
            "acquired_at": record.acquired_at,
            "expires_at": record.expires_at,
            "generation": record.generation,
            "baseline_admitted": bool(record.baseline_suppressions),
            "baseline_outcome": record.baseline_outcome,
            "blockers": [],
        }

    def _notify(self, root_entity_id: str, transition: dict[str, Any]) -> None:
        for registration in list(self._registrations.get(root_entity_id, {}).values()):
            if registration.listener is None:
                continue
            try:
                registration.listener(root_entity_id, transition)
            except Exception as err:
                _LOGGER.exception(
                    "Control lease listener failed for %s: %s",
                    root_entity_id,
                    err,
                )

    def _fire(self, event_type: str, event_data: dict[str, Any]) -> None:
        try:
            result = self.hass.bus.async_fire(event_type, event_data)
            if asyncio.iscoroutine(result):
                result.close()
        except Exception as err:
            _LOGGER.debug("Could not fire %s: %s", event_type, err)

    def _schedule_expiry(
        self,
        root_entity_id: str,
        record: ControlLeaseRecord,
    ) -> None:
        self._cancel_expiry(root_entity_id)
        due = (
            record.recovery_deadline_monotonic
            if record.status == "recovering"
            else record.expires_monotonic
        )
        if due is None:
            return
        delay = max(0.0, due - self._monotonic())

        async def _expire() -> None:
            try:
                await asyncio.sleep(delay)
                await self.async_expire_due(root_entity_id)
            except asyncio.CancelledError:
                return

        self._expiry_tasks[root_entity_id] = self._create_task(_expire())

    def _cancel_expiry(self, root_entity_id: str) -> None:
        task = self._expiry_tasks.pop(root_entity_id, None)
        if task is not None:
            task.cancel()

    def _create_task(self, coroutine) -> asyncio.Task:
        create_task = getattr(self.hass, "async_create_task", None)
        if create_task is not None:
            return create_task(coroutine)
        return asyncio.create_task(coroutine)


def get_control_lease_manager(hass: HomeAssistant) -> ControlLeaseManager:
    """Return the domain-scoped control lease manager."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager = domain_data.get(MANAGER_KEY)
    if not isinstance(manager, ControlLeaseManager):
        manager = ControlLeaseManager(hass)
        domain_data[MANAGER_KEY] = manager
    return manager


__all__ = [
    "ControlLeaseManager",
    "ControlLeaseRecord",
    "GuardDecision",
    "get_control_lease_manager",
]
