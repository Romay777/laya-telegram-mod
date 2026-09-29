"""Pure Classifier Backend availability rules (§5).

Which backend a chat can check with is a fact about the Instance: Laya is
there when it is deployed (its /health answered at least once), Jev is there
when the Operator set a key. Fallback needs more than the Menu does: Laya
must be deployed *and* healthy — the last probe succeeded. No Telegram, no
DB, no I/O.
"""

import pytest
from app.domain.backends import (
    BACKENDS,
    JEV,
    LAYA,
    fallback_eligible,
    jev_available,
    laya_available,
    never_available,
    other,
)


def test_the_two_backends_are_laya_and_jev() -> None:
    assert BACKENDS == ("laya", "jev")
    assert LAYA == "laya"
    assert JEV == "jev"


def test_other_pairs_each_backend_with_its_fallback() -> None:
    assert other("jev") == "laya"
    assert other("laya") == "jev"


def test_jev_is_available_exactly_when_a_key_is_set() -> None:
    assert jev_available("secret") is True
    assert jev_available(None) is False
    assert jev_available("") is False  # an empty .env line is no key


def test_laya_is_available_once_deployed() -> None:
    assert laya_available(deployed=True) is True
    assert laya_available(deployed=False) is False


def test_fallback_needs_laya_deployed_and_healthy() -> None:
    assert fallback_eligible(deployed=True, healthy=True) is True
    assert fallback_eligible(deployed=True, healthy=False) is False
    assert fallback_eligible(deployed=False, healthy=True) is False


@pytest.mark.parametrize(
    ("backend", "laya_deployed", "jev_with_key", "expected"),
    [
        # Jev chosen with no key can never check — whatever Laya's fate.
        ("jev", True, False, True),
        ("jev", False, False, True),
        # Laya chosen before it ever answered /health can never check here.
        ("laya", False, True, True),
        ("laya", False, False, True),
        # Both fine: the chat checks with what it chose.
        ("jev", True, True, False),
        ("laya", True, False, False),
    ],
)
def test_a_chat_whose_backend_cannot_answer_here_is_never_available(
    backend: str, laya_deployed: bool, jev_with_key: bool, expected: bool
) -> None:
    assert (
        never_available(backend, laya_deployed=laya_deployed, jev_available=jev_with_key)
        is expected
    )
