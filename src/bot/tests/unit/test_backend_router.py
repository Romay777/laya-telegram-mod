"""The BackendRouter with two backends (§5): per-chat choice, fallback, skips.

Seam: `check(backend, state) -> BackendCheck`. The router owns the transport
facts — which client serves which chat, the global `max_concurrency`
semaphore, Jev→Laya fallback when Laya is deployed and healthy, and the
failure record the pipeline turns into incidents. What it never does is
touch the DB or send alerts: those belong to the pipeline.
"""

import asyncio

import pytest
from app.classifiers.client import Probabilities, SystemOneError, SystemOneTimeoutError
from app.classifiers.router import BackendCheck, BackendRouter, CheckSkip

CLEAN: Probabilities = {"spam": 0.02, "ads": 0.03, "insult": 0.01, "clean": 0.94}
SPAMMY: Probabilities = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}
STATE = {"message": "hello there friends", "urls": []}


class StubClient:
    """A SystemOneClient stand-in: scripted outcome, optional slowness."""

    def __init__(
        self, outcome: Probabilities | Exception | None = None, delay_s: float = 0
    ) -> None:
        self.outcome = outcome if outcome is not None else CLEAN
        self.delay_s = delay_s
        self.calls: list[dict] = []

    async def classify(self, state: dict, spec: dict) -> Probabilities:
        self.calls.append(state)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class StubHealth:
    def __init__(self, *, deployed: bool, healthy: bool) -> None:
        self.deployed = deployed
        self.healthy = healthy


def healthy_router(laya: StubClient, jev: StubClient, **kwargs) -> BackendRouter:
    return BackendRouter(
        laya=laya, jev=jev, health=StubHealth(deployed=True, healthy=True), **kwargs
    )


async def test_each_chat_is_checked_by_its_own_backend() -> None:
    laya, jev = StubClient(CLEAN), StubClient(SPAMMY)
    router = healthy_router(laya, jev)

    laya_check = await router.check("laya", STATE)
    jev_check = await router.check("jev", STATE)

    assert isinstance(laya_check, BackendCheck)
    assert laya_check.served_by == "laya"
    assert laya_check.outcome == CLEAN
    assert laya_check.failure is None
    assert jev_check.served_by == "jev"
    assert jev_check.outcome == SPAMMY
    assert jev_check.failure is None
    assert laya.calls == [STATE]
    assert jev.calls == [STATE]


async def test_a_check_over_capacity_is_skipped_not_queued() -> None:
    router = healthy_router(StubClient(delay_s=0.05), StubClient(), max_concurrency=1)

    first, second = await asyncio.gather(router.check("laya", STATE), router.check("laya", STATE))

    assert first.outcome == CLEAN  # the one slot was free for exactly one check
    assert second.outcome is CheckSkip.OVERLOAD
    assert second.failure is None  # overload is saturation, not a backend failure


async def test_a_freed_slot_becomes_reusable() -> None:
    """After the rush, a later check goes through again instead of skipping."""
    laya = StubClient(delay_s=0.01)
    router = healthy_router(laya, StubClient(), max_concurrency=1)
    await asyncio.gather(router.check("laya", STATE), router.check("laya", STATE))

    outcome = await router.check("laya", STATE)

    assert outcome.outcome == CLEAN
    assert len(laya.calls) == 2  # the skipped rush never reached the backend


async def test_a_laya_timeout_is_skipped_timeout_with_the_failure_recorded() -> None:
    router = healthy_router(StubClient(SystemOneTimeoutError("read timed out")), StubClient())

    check = await router.check("laya", STATE)

    assert check.outcome is CheckSkip.TIMEOUT
    assert check.served_by == "laya"
    assert check.failure is not None
    assert (check.failure.backend, check.failure.reason) == ("laya", "timed out")


async def test_a_laya_error_is_skipped_unavailable_with_the_failure_recorded() -> None:
    router = healthy_router(StubClient(SystemOneError("500 boom")), StubClient())

    check = await router.check("laya", STATE)

    assert check.outcome is CheckSkip.UNAVAILABLE
    assert check.failure is not None
    assert check.failure.backend == "laya"


async def test_a_jev_failure_falls_back_to_laya_with_layas_answer() -> None:
    laya, jev = StubClient(SPAMMY), StubClient(SystemOneError("denied", reason="rate limited"))
    router = healthy_router(laya, jev)

    check = await router.check("jev", STATE)

    assert check.served_by == "laya"  # the fallback served it
    assert check.outcome == SPAMMY
    assert check.failure is not None  # ...but Jev's failure is still recorded
    assert (check.failure.backend, check.failure.reason) == ("jev", "rate limited")


async def test_a_jev_timeout_falls_back_to_laya() -> None:
    laya, jev = StubClient(CLEAN), StubClient(SystemOneTimeoutError("slow"))
    router = healthy_router(laya, jev)

    check = await router.check("jev", STATE)

    assert check.served_by == "laya"
    assert check.outcome == CLEAN
    assert check.failure is not None
    assert check.failure.backend == "jev"


@pytest.mark.parametrize("healthy", [True, False])
async def test_no_fallback_when_laya_is_not_deployed(healthy: bool) -> None:
    """Laya must be deployed *and* healthy; either gap skips the check (§5)."""
    laya, jev = StubClient(CLEAN), StubClient(SystemOneError("down"))
    router = BackendRouter(laya=laya, jev=jev, health=StubHealth(deployed=False, healthy=healthy))

    check = await router.check("jev", STATE)

    assert check.outcome is CheckSkip.UNAVAILABLE
    assert check.served_by == "jev"  # nothing served it
    assert laya.calls == []  # Laya was never tried
    assert check.failure is not None
    assert check.failure.backend == "jev"


async def test_no_fallback_when_laya_is_unhealthy() -> None:
    laya, jev = StubClient(CLEAN), StubClient(SystemOneError("down"))
    router = BackendRouter(laya=laya, jev=jev, health=StubHealth(deployed=True, healthy=False))

    check = await router.check("jev", STATE)

    assert check.outcome is CheckSkip.UNAVAILABLE
    assert laya.calls == []
    assert check.failure is not None


async def test_a_failed_fallback_is_skipped_unavailable() -> None:
    """Jev failed and Laya failed too: the check is skipped, both recorded."""
    laya = StubClient(SystemOneError("laya down"))
    jev = StubClient(SystemOneError("jev down"))
    router = healthy_router(laya, jev)

    check = await router.check("jev", STATE)

    assert check.outcome is CheckSkip.UNAVAILABLE
    assert check.served_by == "jev"
    assert check.failure is not None
    assert check.failure.backend == "jev"


async def test_a_missing_jev_client_skips_without_a_failure() -> None:
    """No key, no client: the Menu disables Jev, and a stale chat choice
    skips quietly — absence is not an outage, so no incident opens (§5)."""
    router = BackendRouter(
        laya=StubClient(), jev=None, health=StubHealth(deployed=True, healthy=True)
    )

    check = await router.check("jev", STATE)

    assert check.outcome is CheckSkip.UNAVAILABLE
    assert check.failure is None


async def test_a_missing_laya_client_skips_without_a_failure() -> None:
    """The Jev-only Instance (§5, §16): a stale laya choice skips quietly."""
    router = BackendRouter(laya=None, jev=StubClient(), health=None)

    check = await router.check("laya", STATE)

    assert check.outcome is CheckSkip.UNAVAILABLE
    assert check.failure is None


async def test_no_fallback_without_a_health_prober() -> None:
    """Without the prober Laya's state is unknown: Jev failures skip (§5)."""
    laya, jev = StubClient(CLEAN), StubClient(SystemOneError("down"))
    router = BackendRouter(laya=laya, jev=jev, health=None)

    check = await router.check("jev", STATE)

    assert check.outcome is CheckSkip.UNAVAILABLE
    assert laya.calls == []


def test_the_router_reports_which_backends_are_available() -> None:
    """The Menu's `disabled` buttons read these facts (§5)."""
    router = healthy_router(StubClient(), StubClient())

    assert router.laya_deployed is True
    assert router.laya_healthy is True
    assert router.jev_available is True


def test_the_router_reports_an_undeployed_laya() -> None:
    router = BackendRouter(
        laya=StubClient(), jev=StubClient(), health=StubHealth(deployed=False, healthy=False)
    )

    assert router.laya_deployed is False
    assert router.laya_healthy is False
    assert router.jev_available is True


def test_the_router_reports_absence_as_unavailability() -> None:
    """No client at all: neither backend is available (§5)."""
    router = BackendRouter(laya=None, jev=None, health=None)

    assert router.laya_deployed is False
    assert router.laya_healthy is False
    assert router.jev_available is False
