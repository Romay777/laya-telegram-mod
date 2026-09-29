"""The BackendRouter: two clients, one semaphore, Jev→Laya fallback (§5).

Each chat checks with the backend it chose (`chat.backend`); this router
decides which client actually serves the check and what to do when that
backend fails:

- A single semaphore across all backends limits concurrent checks to
  `max_concurrency`. A check that cannot acquire it straight away is
  `skipped_overload`, never queued (ADR-0002) — saturation is not a
  backend failure, so no incident comes of it.
- If Jev fails (auth, 429, 5xx, timeout) and Laya is deployed *and*
  healthy, the check falls back to Laya (§5); the Verdict is then judged
  against Laya's thresholds — the pipeline reads `served_by` for that.
- With Laya unavailable too, the check is `skipped_unavailable` (§5).
- A backend that is not part of this Instance at all (no Jev key, or the
  laya profile removed) also skips, but records no failure: absence is not
  an outage, and the Menu's `disabled` buttons and the never-available
  status line are what steer Admins away from it.

Failures travel to the pipeline on `BackendCheck.failure` — backend, reason
— and become `backend_incident` rows and Admin Alerts there; the router
itself touches no database and sends nothing (§5).
"""

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.classifiers.client import Probabilities, SystemOneError
from app.classifiers.spec import QUESTION_SPEC
from app.domain.backends import JEV, LAYA, fallback_eligible


class CheckSkip(StrEnum):
    """The §4 step 6 outcomes of a check that never produced probabilities."""

    TIMEOUT = "skipped_timeout"
    OVERLOAD = "skipped_overload"
    UNAVAILABLE = "skipped_unavailable"


class SystemOneClient_(Protocol):
    """What the router asks of a client: one classify, raise on failure."""

    async def classify(self, state: dict[str, Any], spec: dict[str, Any]) -> Probabilities: ...


class HealthProtocol(Protocol):
    """The two §5 flags the Laya health prober keeps."""

    @property
    def deployed(self) -> bool: ...

    @property
    def healthy(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class BackendFailure:
    """One failed call, quoted in the incident alert (§5): who and why."""

    backend: str
    reason: str


@dataclass(frozen=True, slots=True)
class BackendCheck:
    """What one check came back as: who served it, what it said, what broke.

    `served_by` names the backend whose answer (or skip outcome) this is —
    after a fallback it is `laya`, so the Verdict is judged with Laya's
    thresholds and recorded with Laya's model (§5). `failure` is set when
    the chosen backend failed, whether or not a fallback rescued the check.
    """

    served_by: str
    outcome: Probabilities | CheckSkip
    failure: BackendFailure | None = None


class ClassifierBackend(Protocol):
    """What the moderation pipeline asks for: one check, one outcome."""

    async def check(self, backend: str, state: dict[str, Any]) -> BackendCheck: ...


class BackendRouter:
    def __init__(
        self,
        *,
        laya: SystemOneClient_ | None,
        jev: SystemOneClient_ | None,
        health: HealthProtocol | None = None,
        max_concurrency: int = 4,
        spec: dict[str, Any] | None = None,
    ) -> None:
        self._clients: dict[str, SystemOneClient_ | None] = {LAYA: laya, JEV: jev}
        self._health = health
        self._spec = spec if spec is not None else QUESTION_SPEC
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def check(self, backend: str, state: dict[str, Any]) -> BackendCheck:
        """One check for a chat's chosen backend (§5), never queued."""
        if self._semaphore.locked():
            return BackendCheck(backend, CheckSkip.OVERLOAD)
        async with self._semaphore:
            return await self._check_unguarded(backend, state)

    async def _check_unguarded(self, backend: str, state: dict[str, Any]) -> BackendCheck:
        client = self._clients.get(backend)
        if client is None:
            # Not part of this deployment: the Menu never offers it and the
            # status screen says so; a stale choice skips without an incident.
            return BackendCheck(backend, CheckSkip.UNAVAILABLE)
        try:
            return BackendCheck(backend, await client.classify(state, self._spec))
        except SystemOneError as error:
            return await self._after_failure(backend, error.reason, state)

    async def _after_failure(
        self, backend: str, reason: str, state: dict[str, Any]
    ) -> BackendCheck:
        failure = BackendFailure(backend=backend, reason=reason)
        if backend != JEV or not self._laya_rescues():
            return BackendCheck(backend, self._skip_of(backend, failure), failure)
        laya = self._clients[LAYA]
        assert laya is not None  # _laya_rescues guarantees the client exists
        try:
            probabilities = await laya.classify(state, self._spec)
        except SystemOneError:
            return BackendCheck(backend, CheckSkip.UNAVAILABLE, failure)
        return BackendCheck(LAYA, probabilities, failure)

    def _laya_rescues(self) -> bool:
        """Whether a Jev failure may fall back to Laya right now (§5)."""
        if self._health is None or self._clients.get(LAYA) is None:
            return False
        return fallback_eligible(deployed=self._health.deployed, healthy=self._health.healthy)

    def _skip_of(self, backend: str, failure: BackendFailure) -> CheckSkip:
        """The §4 step 6 outcome of a failure that no fallback rescued (§5)."""
        if backend == JEV:
            return CheckSkip.UNAVAILABLE  # a Jev failure is unavailable, however it failed
        return CheckSkip.TIMEOUT if failure.reason == "timed out" else CheckSkip.UNAVAILABLE
