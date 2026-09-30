"""Wiring (§2): a `message_check` row records the classifier's own models.

`run()` wires the dispatcher with the router it built and passes no models
of its own — the models come from the classifier. Both backends are named
even when Jev has no key: a stale chat choice (§5) still records its checks
and skips against Jev, and a missing model must never KeyError the
pipeline (§12).
"""

from app.classifiers.router import BackendRouter
from app.classifiers.spec import JEV_MODEL_DEFAULT, LAYA_MODEL
from app.main import DEFAULT_MODELS, build_models

from tests.support.backend import FakeBackend


def test_build_models_names_both_backends_with_the_configured_model() -> None:
    assert build_models("jev-2.0.0") == {"laya": LAYA_MODEL, "jev": "jev-2.0.0"}


def test_the_default_models_pin_both_defaults() -> None:
    assert DEFAULT_MODELS == {"laya": LAYA_MODEL, "jev": JEV_MODEL_DEFAULT}


def test_the_router_carries_the_models_it_was_built_with() -> None:
    router = BackendRouter(laya=FakeBackend(), jev=FakeBackend(), models={"laya": "laya-9"})
    assert router.models == {"laya": "laya-9"}


def test_a_router_without_models_of_its_own_pins_the_defaults() -> None:
    router = BackendRouter(laya=FakeBackend(), jev=FakeBackend())
    assert router.models == {"laya": LAYA_MODEL, "jev": JEV_MODEL_DEFAULT}


def test_a_stand_in_backend_carries_models_of_its_own() -> None:
    # The ClassifierBackend protocol declares `models` (§12): a dispatcher is
    # never wired without a model per backend, a stand-in included.
    assert FakeBackend().models == DEFAULT_MODELS
