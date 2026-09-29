"""Classifier Backend facts (§5): which backend a chat can check with.

A backend is more than a name: whether it exists in this Instance decides
the Menu's `disabled` buttons, the Linking default and the never-available
status line. Laya is there when it is deployed — its /health answered at
least once since the bot started; Jev is there when the Operator set a key.
Fallback needs more: Laya deployed *and* healthy, the last probe succeeded.
Pure rules, so the router, the Menu and Linking share one vocabulary.
"""

from typing import Final

#: The two Classifier Backends of §12.
BACKENDS: Final = ("laya", "jev")

LAYA: Final = "laya"
JEV: Final = "jev"


def other(backend: str) -> str:
    """The backend a failing check falls back to — Jev and Laya pair up."""
    return JEV if backend == LAYA else LAYA


def jev_available(api_key: str | None) -> bool:
    """Jev counts as available only when a key is set (§5)."""
    return bool(api_key)


def laya_available(*, deployed: bool) -> bool:
    """Laya counts as available once its /health answered at least once (§5)."""
    return deployed


def fallback_eligible(*, deployed: bool, healthy: bool) -> bool:
    """Whether a Jev failure may fall back to Laya (§5): deployed and healthy."""
    return deployed and healthy


def never_available(backend: str, *, laya_deployed: bool, jev_available: bool) -> bool:
    """Whether a chat's chosen backend can never answer in this Instance (§5).

    Jev without a key has nothing to call; a Laya that never answered /health
    is not part of this deployment. Either way the chat's status screen says
    so, and switching to the other backend is the way out.
    """
    if backend == JEV:
        return not jev_available
    return not laya_deployed
