"""The BackendRouter: one semaphore and mapped skip outcomes around a client (§5).

Seam: `check(state)`. The router is what makes a check skippable instead of
queued: when `max_concurrency` is exhausted the check is `skipped_overload`
at once; a client timeout is `skipped_timeout`; any other backend failure is
`skipped_unavailable`. No Jev and no fallback here — that is ticket #14.
"""

import asyncio

from app.classifiers.client import SystemOneError, SystemOneTimeoutError
from app.classifiers.router import CheckSkip, BackendRouter

CLEAN = {"spam": 0.02, "ads": 0.03, "insult": 0.01, "clean": 0.94}
STATE = {"message": "hello there friends", "urls": []}


class StubClient:
    """A SystemOneClient stand-in: scripted outcome, optional slowness."""

    def __init__(self, *, delay_s: float = 0) -> None:
        self.delay_s = delay_s
        self.calls: list[dict] = []

    async def classify(self, state: dict, spec: dict) -> dict[str, float]:
        self.calls.append(state)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return CLEAN


class FailingClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def classify(self, state: dict, spec: dict) -> dict[str, float]:
        raise self.error


async def test_a_check_returns_the_backend_probabilities() -> None:
    router = BackendRouter(StubClient(), max_concurrency=1)

    outcome = await router.check(STATE)

    assert outcome == CLEAN


async def test_a_check_over_capacity_is_skipped_not_queued() -> None:
    router = BackendRouter(StubClient(delay_s=0.05), max_concurrency=1)

    first, second = await asyncio.gather(router.check(STATE), router.check(STATE))

    assert first == CLEAN  # the one slot was free for exactly one check
    assert second is CheckSkip.OVERLOAD


async def test_a_backend_timeout_is_skipped_timeout() -> None:
    router = BackendRouter(
        FailingClient(SystemOneTimeoutError("read timed out")), max_concurrency=1
    )

    assert await router.check(STATE) is CheckSkip.TIMEOUT


async def test_a_backend_error_is_skipped_unavailable() -> None:
    router = BackendRouter(FailingClient(SystemOneError("500 boom")), max_concurrency=1)

    assert await router.check(STATE) is CheckSkip.UNAVAILABLE


async def test_a_freed_slot_becomes_reusable() -> None:
    """After the rush, a later check goes through again instead of skipping."""
    client = StubClient(delay_s=0.01)
    router = BackendRouter(client, max_concurrency=1)
    await asyncio.gather(router.check(STATE), router.check(STATE))

    outcome = await router.check(STATE)

    assert outcome == CLEAN
    assert len(client.calls) == 2  # the skipped rush never reached the backend
