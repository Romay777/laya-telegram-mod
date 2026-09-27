"""Seam 2: the router registration contract.

Only `multilingual` is attached to the router, and a request naming any other
model errors without ever resolving to an English checkpoint — no English
download ever starts.
"""

import huggingface_hub
import pytest

from app import build_router
from tests.fixtures import FakeMultilingualAgent


def test_only_multilingual_is_attached_to_the_router():
    router = build_router(agent_factory=FakeMultilingualAgent)

    assert router.loaded == ["multilingual"]


@pytest.mark.parametrize("model", ["english", "typed-decisions"])
def test_request_for_any_other_model_errors(client, model):
    response = client.post(
        "/v1/systemone",
        json={
            "model": model,
            "state": {"message": "I was charged twice", "urls": []},
            "questions": {
                "category": {
                    "type": "choice",
                    "instructions": "Choose the label.",
                    "criteria": {"spam": "spam", "clean": "clean"},
                }
            },
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "inference failed"


@pytest.mark.parametrize("model", ["english", "typed-decisions"])
def test_request_for_any_other_model_never_starts_a_download(
    client, monkeypatch, question_spec, model
):
    def refuse_download(*args, **kwargs):
        raise AssertionError("a Hugging Face download was attempted")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", refuse_download)

    response = client.post(
        "/v1/systemone",
        json={
            "model": model,
            "state": {"message": "Hello there", "urls": []},
            "questions": question_spec,
        },
    )

    assert response.status_code == 500


def test_english_checkpoint_never_becomes_resident_after_foreign_requests(client):
    for model in ("english", "typed-decisions", "multilingual"):
        client.post(
            "/v1/systemone",
            json={
                "model": model,
                "state": {"message": "Hola, ¿cómo estás?", "urls": []},
                "questions": {
                    "category": {
                        "type": "choice",
                        "instructions": "Choose the label.",
                        "criteria": {"spam": "spam", "clean": "clean"},
                    }
                },
            },
        )

    health = client.get("/health").json()
    assert health["loaded"] == ["multilingual"]
