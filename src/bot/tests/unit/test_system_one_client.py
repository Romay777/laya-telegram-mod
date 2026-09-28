"""The SystemOneClient against recorded fixtures on an httpx mock transport (§17).

Seam: `classify(state, spec)`. The client owns the transport facts — the
pinned `model`, the `/systemone` path, the Bearer key, the timeout — and
maps every backend failure onto `SystemOneError` (timeouts included).
"""

import json

import httpx
import pytest

from app.classifiers.client import SystemOneClient, SystemOneError, SystemOneTimeoutError
from app.classifiers.spec import QUESTION_SPEC, LAYA_MODEL

from .system_one_fixtures import CLEAN_ANSWER, SPAM_ANSWER, assert_all_labels, without_label

STATE = {"message": "Buy cheap crypto now, DM me", "urls": ["https://t.me/+abc"]}


def make_client(handler, **kwargs: object) -> SystemOneClient:
    return SystemOneClient(
        "http://laya:8000/v1",
        model=LAYA_MODEL,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def test_a_laya_request_pins_the_model_and_posts_state_and_spec() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(200, json=CLEAN_ANSWER)

    client = make_client(handler)
    probabilities = await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()

    assert seen["url"] == "http://laya:8000/v1/systemone"
    body = json.loads(seen["body"])
    assert body["model"] == LAYA_MODEL  # every Laya request pins `multilingual`
    assert body["state"] == STATE
    assert body["questions"] == QUESTION_SPEC
    assert_all_labels(probabilities)
    assert probabilities["clean"] == pytest.approx(0.94)


async def test_an_api_key_travels_as_a_bearer_token() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=CLEAN_ANSWER)

    client = SystemOneClient(
        "http://jev.internal/v1",
        model="jev-1.13.0",
        api_key="secret-key",
        transport=httpx.MockTransport(handler),
    )
    await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()

    assert seen["auth"] == "Bearer secret-key"


async def test_probabilities_are_extracted_from_the_recorded_answer() -> None:
    client = make_client(lambda request: httpx.Response(200, json=SPAM_ANSWER))
    probabilities = await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()

    assert probabilities == {
        "spam": pytest.approx(0.97),
        "ads": pytest.approx(0.01),
        "insult": pytest.approx(0.01),
        "clean": pytest.approx(0.01),
    }


async def test_an_answer_missing_a_label_is_a_backend_error() -> None:
    broken = without_label(SPAM_ANSWER, "clean")

    client = make_client(lambda request: httpx.Response(200, json=broken))
    with pytest.raises(SystemOneError):
        await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()


async def test_an_http_error_status_is_a_backend_error() -> None:
    client = make_client(lambda request: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(SystemOneError):
        await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()


async def test_a_timeout_is_reported_as_its_own_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out while reading")

    client = make_client(handler)
    with pytest.raises(SystemOneTimeoutError):
        await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()


async def test_the_call_carries_the_configured_timeout() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json=CLEAN_ANSWER)

    client = make_client(handler, timeout_s=1.5)
    await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()

    assert seen["timeout"] == {"connect": 1.5, "read": 1.5, "write": 1.5, "pool": 1.5}
