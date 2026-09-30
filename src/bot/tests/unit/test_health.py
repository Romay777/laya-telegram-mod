"""The Laya health prober (§5): deployed and healthy, off one GET /health.

Laya counts as deployed once its /health has answered at least once since
the bot started, and healthy while the last probe succeeded. The prober owns
that bookkeeping; the router and the Menu ask it, the asyncio task that
calls `probe_forever` paces it at `health_interval_s`.
"""

import httpx
from app.classifiers.health import LayaHealth


def make_health(handler) -> LayaHealth:
    return LayaHealth(
        "http://laya:8000/v1",
        transport=httpx.MockTransport(handler),
    )


async def test_an_unprobed_laya_is_neither_deployed_nor_healthy() -> None:
    health = make_health(lambda request: httpx.Response(200))

    assert health.deployed is False
    assert health.healthy is False
    await health.aclose()


async def test_one_successful_answer_deploys_laya_and_makes_it_healthy() -> None:
    health = make_health(lambda request: httpx.Response(200))

    await health.probe_once()

    assert health.deployed is True
    assert health.healthy is True
    await health.aclose()


async def test_the_probe_hits_the_health_path() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        return httpx.Response(200)

    health = make_health(handler)
    await health.probe_once()
    await health.aclose()

    # The /v1 base URL is the classifier's; the probe still hits the root.
    assert seen["url"] == "http://laya:8000/health"
    assert seen["method"] == "GET"


async def test_a_failed_probe_keeps_the_deployment_but_loses_the_health() -> None:
    """Deployed stays true forever once answered; healthy follows the last probe."""
    responses = iter([httpx.Response(200), httpx.Response(503), httpx.Response(200)])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    health = make_health(handler)
    await health.probe_once()
    await health.probe_once()
    assert health.deployed is True
    assert health.healthy is False

    await health.probe_once()
    assert health.healthy is True
    await health.aclose()


async def test_a_transport_error_is_an_unhealthy_probe_not_a_crash() -> None:
    calls = iter([False, True])

    def handler(request: httpx.Request) -> httpx.Response:
        if not next(calls):
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200)

    health = make_health(handler)
    await health.probe_once()
    assert health.deployed is False
    assert health.healthy is False

    await health.probe_once()
    assert health.deployed is True
    await health.aclose()
