"""Wiring (§2): a `message_check` row records the classifier's own models.

`run()` wires the dispatcher with the router it built and passes no models
of its own — the models come from the classifier. Both backends are named
even when Jev has no key: a stale chat choice (§5) still records its checks
and skips against Jev, and a missing model must never KeyError the
pipeline (§12).
"""

from app.classifiers.router import BackendRouter
from app.classifiers.spec import JEV_MODEL_DEFAULT, LAYA_MODEL
from app.main import DEFAULT_MODELS, build_models, models_of

from tests.support.backend import FakeBackend


def test_build_models_names_both_backends_with_the_configured_model() -> None:
    assert build_models("jev-2.0.0") == {"laya": LAYA_MODEL, "jev": "jev-2.0.0"}


def test_the_default_models_pin_both_defaults() -> None:
    assert DEFAULT_MODELS == {"laya": LAYA_MODEL, "jev": JEV_MODEL_DEFAULT}


def test_models_of_reads_the_models_the_router_was_built_with() -> None:
    router = BackendRouter(laya=FakeBackend(), jev=FakeBackend(), models={"laya": "laya-9"})
    assert models_of(router) == {"laya": "laya-9"}


def test_models_of_reads_the_router_without_models_of_its_own() -> None:
    router = BackendRouter(laya=FakeBackend(), jev=FakeBackend())
    assert models_of(router) == {"laya": LAYA_MODEL, "jev": JEV_MODEL_DEFAULT}


def test_models_of_falls_back_to_the_defaults_for_a_bare_backend() -> None:
    assert models_of(FakeBackend()) == DEFAULT_MODELS
