"""The SystemOneClient against recorded fixtures on an httpx mock transport (§17).

Seam: `classify(state, spec)`. The client owns the transport facts — the
pinned `model`, the `/systemone` path, the Bearer key, the timeout — and
maps every backend failure onto `SystemOneError` (timeouts included).
"""

import json

import httpx
import pytest
from app.classifiers.client import SystemOneClient, SystemOneError, SystemOneTimeoutError
from app.classifiers.spec import JEV_MODEL_DEFAULT, LAYA_MODEL, QUESTION_SPEC

from .system_one_fixtures import (
    CLEAN_ANSWER,
    SPAM_ANSWER,
    assert_all_labels,
    with_extra_top_level_fields,
    without_label,
)

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


# --- Jev (ticket #14): the same client against the TypeSafe / OpenRouter shape.


def make_jev_client(handler, base_url: str = "https://api.typesafe.ai/v1") -> SystemOneClient:
    return SystemOneClient(
        base_url,
        model=JEV_MODEL_DEFAULT,
        api_key="jev-secret",
        transport=httpx.MockTransport(handler),
    )


async def test_a_jev_request_appends_systemone_to_the_typesafe_base_url() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=CLEAN_ANSWER)

    client = make_jev_client(handler)
    await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()

    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"


async def test_an_openrouter_base_url_is_used_verbatim() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=CLEAN_ANSWER)

    client = make_jev_client(handler, base_url="https://openrouter.ai/api/v1")
    await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()

    assert seen["url"] == "https://openrouter.ai/api/v1/systemone"


async def test_a_jev_request_pins_its_model_and_sends_the_bearer_key() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.read())
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=CLEAN_ANSWER)

    client = make_jev_client(handler)
    await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()

    assert seen["body"]["model"] == JEV_MODEL_DEFAULT  # pinned, never `jev-latest` (ADR-0002)
    assert seen["body"]["state"] == STATE
    assert seen["body"]["questions"] == QUESTION_SPEC
    assert seen["auth"] == "Bearer jev-secret"


async def test_a_jev_answer_with_extra_top_level_fields_is_extracted() -> None:
    """TypeSafe / OpenRouter wrap their own metadata around `answers` (§17)."""
    client = make_jev_client(
        lambda request: httpx.Response(200, json=with_extra_top_level_fields(SPAM_ANSWER))
    )
    probabilities = await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()

    assert_all_labels(probabilities)
    assert probabilities["spam"] == pytest.approx(0.97)


@pytest.mark.parametrize("status", [401, 403])
async def test_an_auth_failure_names_authentication_as_the_reason(status: int) -> None:
    client = make_jev_client(lambda request: httpx.Response(status, json={"error": "denied"}))

    with pytest.raises(SystemOneError) as raised:
        await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()

    assert raised.value.reason == "authentication failed"


async def test_a_rate_limit_names_the_limit_as_the_reason() -> None:
    client = make_jev_client(lambda request: httpx.Response(429, json={"error": "slow down"}))

    with pytest.raises(SystemOneError) as raised:
        await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()

    assert raised.value.reason == "rate limited"


async def test_a_server_error_names_the_status_as_the_reason() -> None:
    client = make_jev_client(lambda request: httpx.Response(503, json={"error": "down"}))

    with pytest.raises(SystemOneError) as raised:
        await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()

    assert raised.value.reason == "backend answered 503"


async def test_a_timeout_names_the_timeout_as_the_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    client = make_jev_client(handler)
    with pytest.raises(SystemOneTimeoutError) as raised:
        await client.classify(STATE, QUESTION_SPEC)
    await client.aclose()

    assert raised.value.reason == "timed out"
