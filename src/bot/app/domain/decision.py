"""Pure Decision rule (§4 step 8): the two-zone table (ADR-0003).

    | Mode             | p ≥ violation | suspicion ≤ p < violation | below   |
    |------------------|---------------|---------------------------|---------|
    | Auto-moderation  | Violation     | Suspicion                 | nothing |
    | Observation Mode | Suspicion     | Suspicion                 | nothing |

The Verdict is the highest-probability label of the question spec; the
thresholds only sort it into a zone. No Telegram, no DB, no I/O.
"""

from collections.abc import Mapping
from dataclasses import dataclass

#: The one label of the question spec that is never a Category.
CLEAN = "clean"

#: The `message_check` outcomes of a message that reached the classifier (§12).
OUTCOME_VIOLATION = "violation"
OUTCOME_SUSPICION = "suspicion"
OUTCOME_CLEAN = "clean"

#: The two chat modes (§12): Auto-moderation and Observation Mode.
MODE_AUTO = "auto"
MODE_OBSERVATION = "observation"


@dataclass(frozen=True, slots=True)
class Decision:
    """What one check decided: the Verdict and which zone it fell into."""

    outcome: str  # violation | suspicion | clean
    category: str  # the highest-probability label, `clean` included
    confidence: float


def decide(
    *,
    probabilities: Mapping[str, float],
    violation_threshold: float,
    suspicion_threshold: float,
    mode: str,
    enabled_categories: tuple[str, ...] | None = None,
) -> Decision:
    """The argmax label, sorted into a zone by the mode and the thresholds.

    Disabled Categories are ignored when the Verdict is picked (§4 step 7):
    the highest-probability enabled label counts, and "clean" when nothing
    enabled is left. `None` means every label counts, as before.
    """
    if enabled_categories is None:
        category = max(probabilities, key=lambda label: probabilities[label])
    else:
        enabled = set(enabled_categories) | {CLEAN}
        category = max(
            (label for label in probabilities if label in enabled),
            key=lambda label: probabilities[label],
            default=CLEAN,
        )
    confidence = probabilities[category]
    if category != CLEAN and confidence >= suspicion_threshold:
        # One zone is flagged in both modes; only Auto-moderation splits it
        # further into a full Violation (ADR-0003).
        violation = mode == MODE_AUTO and confidence >= violation_threshold
        outcome = OUTCOME_VIOLATION if violation else OUTCOME_SUSPICION
    else:
        outcome = OUTCOME_CLEAN
    return Decision(outcome=outcome, category=category, confidence=confidence)
