"""FakeBackend (§17): scripted classifier probabilities for the e2e harness.

The prescribed stand-in for the Classifier Backend: every scripted check
returns exactly the outcome it was given, and records the states it was
asked about, so tests can assert what the bot did with the outside world.
One fake stands in for one backend; the ticket's fallback tests script one
fake per backend. A fake also speaks `classify`, so the real BackendRouter
can be wired with fakes as its two clients: `script_error` makes it raise
like a failed backend call.
"""

from typing import Any

from app.classifiers.client import Probabilities, SystemOneError
from app.classifiers.router import BackendCheck, CheckSkip

#: The probabilities of an ordinary, boring message.
CLEAN = Probabilities({"spam": 0.02, "ads": 0.03, "insult": 0.01, "clean": 0.94})

#: The probabilities of an obvious spam message (§3: well above every
#: violation threshold, and category `spam`).
SPAMMY = Probabilities({"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01})


class ControllableHealth:
    """The two §5 flags a test sets directly, standing in for the prober."""

    def __init__(self, *, deployed: bool = True, healthy: bool = True) -> None:
        self._deployed = deployed
        self._healthy = healthy

    @property
    def deployed(self) -> bool:
        return self._deployed

    @property
    def healthy(self) -> bool:
        return self._healthy

    def set(self, *, deployed: bool, healthy: bool) -> None:
        self._deployed = deployed
        self._healthy = healthy


class FakeBackend:
    def __init__(self, probabilities: Probabilities | None = None) -> None:
        self._outcome: Probabilities | CheckSkip = probabilities or CLEAN
        self._error: str | None = None
        self.calls: list[dict[str, Any]] = []
        #: The two §5 availability facts, settable like ControllableHealth:
        #: the Menu's disabled buttons and the status warning read them.
        self.laya_deployed = True
        self.laya_healthy = True
        self.jev_available = True

    def script(self, outcome: Probabilities | CheckSkip) -> None:
        """Script what the next checks return until told otherwise."""
        self._outcome = outcome
        self._error = None

    def script_error(self, reason: str) -> None:
        """Script a backend failure: `classify` raises, like a failed call (§5)."""
        self._error = reason

    async def check(self, backend: str, state: dict[str, Any]) -> BackendCheck:
        self.calls.append(state)
        return BackendCheck(backend, self._outcome)

    async def classify(self, state: dict[str, Any], spec: dict[str, Any]) -> Probabilities:
        """The SystemOneClient face: probabilities, or the scripted failure (§5)."""
        self.calls.append(state)
        if self._error is not None:
            raise SystemOneError(f"scripted failure: {self._error}", reason=self._error)
        if isinstance(self._outcome, CheckSkip):
            raise AssertionError(
                "a CheckSkip cannot go through classify: script probabilities or an error"
            )
        return self._outcome
