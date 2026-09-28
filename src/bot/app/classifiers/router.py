"""The BackendRouter: one semaphore and mapped skip outcomes around a client (§5).

Ticket #6 uses the local Laya only — no Jev, no fallback, no incidents
(that is ticket #14). What a bare client cannot do, and this router adds,
is the global `max_concurrency` semaphore: a check that cannot acquire it
straight away is `skipped_overload`, never queued (ADR-0002), and client
failures become the skip outcomes of §4 step 6.
"""

import asyncio
from enum import StrEnum
from typing import Any, Protocol

from app.classifiers.client import Probabilities, SystemOneError, SystemOneTimeoutError
from app.classifiers.spec import QUESTION_SPEC


class CheckSkip(StrEnum):
    """The §4 step 6 outcomes of a check that never produced probabilities."""

    TIMEOUT = "skipped_timeout"
    OVERLOAD = "skipped_overload"
    UNAVAILABLE = "skipped_unavailable"


class ClassifierBackend(Protocol):
    """What the moderation pipeline asks for: one check, one outcome."""

    async def check(self, state: dict[str, Any]) -> Probabilities | CheckSkip: ...


class BackendRouter:
    def __init__(
        self,
        client: Any,
        *,
        max_concurrency: int = 4,
        spec: dict[str, Any] | None = None,
    ) -> None:
        self._client = client
        self._spec = spec if spec is not None else QUESTION_SPEC
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def check(self, state: dict[str, Any]) -> Probabilities | CheckSkip:
        """One check: probabilities, or a skip outcome instead of waiting (§5)."""
        if self._semaphore.locked():
            return CheckSkip.OVERLOAD  # never queue: a stale check is useless
        async with self._semaphore:
            try:
                return await self._client.classify(state, self._spec)
            except SystemOneTimeoutError:
                return CheckSkip.TIMEOUT
            except SystemOneError:
                return CheckSkip.UNAVAILABLE
