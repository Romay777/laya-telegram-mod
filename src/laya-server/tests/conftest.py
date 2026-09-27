import pytest
from fastapi.testclient import TestClient

from app import build_router
from tests.fixtures import QUESTION_SPEC, SPEC_LABELS, FakeMultilingualAgent

__all__ = ["QUESTION_SPEC", "SPEC_LABELS", "FakeMultilingualAgent"]


@pytest.fixture()
def question_spec():
    return QUESTION_SPEC


@pytest.fixture()
def spec_labels():
    return SPEC_LABELS


@pytest.fixture()
def client():
    router = build_router(agent_factory=FakeMultilingualAgent)
    from laya.serve import create_app

    with TestClient(create_app(router=router)) as test_client:
        yield test_client
