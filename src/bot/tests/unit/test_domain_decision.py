"""Pure Decision rule (§4 step 8): probabilities + the violation threshold."""

import pytest

from app.domain.decision import CLEAN, decide

SPAMMY = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}
BORING = {"spam": 0.01, "ads": 0.02, "insult": 0.01, "clean": 0.96}
HALF_SURE = {"spam": 0.55, "ads": 0.05, "insult": 0.05, "clean": 0.35}


def test_a_non_clean_label_at_or_above_the_threshold_is_a_violation() -> None:
    decision = decide(probabilities=SPAMMY, violation_threshold=0.90)

    assert decision.outcome == "violation"
    assert decision.category == "spam"
    assert decision.confidence == pytest.approx(0.97)


def test_the_verdict_is_the_highest_probability_label() -> None:
    probabilities = {"spam": 0.05, "ads": 0.40, "insult": 0.50, "clean": 0.05}

    decision = decide(probabilities=probabilities, violation_threshold=0.90)

    assert decision.category == "insult"  # argmax, even below the threshold


def test_clean_is_never_a_violation_how_sure_it_is() -> None:
    probabilities = {"spam": 0.01, "ads": 0.01, "insult": 0.01, "clean": 0.97}

    decision = decide(probabilities=probabilities, violation_threshold=0.50)

    assert decision.outcome == "clean"
    assert decision.category == CLEAN


def test_below_the_threshold_the_message_is_left_alone() -> None:
    decision = decide(probabilities=HALF_SURE, violation_threshold=0.90)

    assert decision.outcome == "clean"
    assert decision.category == "spam"


def test_exactly_at_the_threshold_counts_as_the_violation_zone() -> None:
    probabilities = {"spam": 0.90, "ads": 0.03, "insult": 0.03, "clean": 0.04}

    decision = decide(probabilities=probabilities, violation_threshold=0.90)

    assert decision.outcome == "violation"
