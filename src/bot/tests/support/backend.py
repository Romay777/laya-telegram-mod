"""FakeBackend (§17): scripted classifier probabilities for the e2e harness.

The prescribed stand-in for the Classifier Backend: every scripted check
returns exactly the outcome it was given, and records the states it was
asked about, so tests can assert what the bot did with the outside world.
"""

from typing import Any

from app.classifiers.client import Probabilities
from app.classifiers.router import CheckSkip

#: The probabilities of an ordinary, boring message.
CLEAN = Probabilities({"spam": 0.02, "ads": 0.03, "insult": 0.01, "clean": 0.94})


class FakeBackend:
    def __init__(self, probabilities: Probabilities | None = None) -> None:
        self._outcome: Probabilities | CheckSkip = probabilities or CLEAN
        self.calls: list[dict[str, Any]] = []

    def script(self, outcome: Probabilities | CheckSkip) -> None:
        """Script what the next checks return until told otherwise."""
        self._outcome = outcome

    async def check(self, state: dict[str, Any]) -> Probabilities | CheckSkip:
        self.calls.append(state)
        return self._outcome
