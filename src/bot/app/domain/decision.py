"""Pure Decision rule (§4 step 8): probabilities plus a threshold → Decision.

The Verdict is the highest-probability label of the question spec. This
ticket acts on the Violation zone only: a non-clean label at or above the
chat's violation threshold is a Violation, everything else is left alone
(§4: Suspicions in the middle band arrive with ticket #9).

No Telegram, no DB, no I/O.
"""

from collections.abc import Mapping
from dataclasses import dataclass

#: The one label of the question spec that is never a Category.
CLEAN = "clean"

#: The `message_check` outcomes of a message that reached the classifier (§12).
OUTCOME_VIOLATION = "violation"
OUTCOME_CLEAN = "clean"


@dataclass(frozen=True, slots=True)
class Decision:
    """What one check decided: the Verdict and which zone it fell into."""

    outcome: str  # violation | clean
    category: str  # the highest-probability label, `clean` included
    confidence: float


def decide(*, probabilities: Mapping[str, float], violation_threshold: float) -> Decision:
    """The Verdict is the argmax label; the threshold only gates the Violation."""
    category = max(probabilities, key=lambda label: probabilities[label])
    confidence = probabilities[category]
    violation = category != CLEAN and confidence >= violation_threshold
    return Decision(
        outcome=OUTCOME_VIOLATION if violation else OUTCOME_CLEAN,
        category=category,
        confidence=confidence,
    )
