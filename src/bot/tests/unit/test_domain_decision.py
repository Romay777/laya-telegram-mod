"""Pure Decision rule (§4 step 8): the two-zone table over mode and thresholds.

| Mode              | p ≥ violation | suspicion ≤ p < violation | below   |
|-------------------|---------------|---------------------------|---------|
| Auto-moderation   | Violation     | Suspicion                 | nothing |
| Observation Mode  | Suspicion     | Suspicion                 | nothing |
"""

import pytest
from app.domain.decision import CLEAN, MODE_AUTO, MODE_OBSERVATION, decide

SPAMMY = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}
BORING = {"spam": 0.01, "ads": 0.02, "insult": 0.01, "clean": 0.96}
MID_SURE = {"spam": 0.70, "ads": 0.15, "insult": 0.05, "clean": 0.10}
HALF_SURE = {"spam": 0.55, "ads": 0.05, "insult": 0.05, "clean": 0.35}

VIOLATION, SUSPICION = 0.90, 0.60


def test_a_non_clean_label_at_or_above_the_threshold_is_a_violation() -> None:
    decision = decide(
        probabilities=SPAMMY,
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_AUTO,
    )

    assert decision.outcome == "violation"
    assert decision.category == "spam"
    assert decision.confidence == pytest.approx(0.97)


def test_the_verdict_is_the_highest_probability_label() -> None:
    probabilities = {"spam": 0.05, "ads": 0.40, "insult": 0.50, "clean": 0.05}

    decision = decide(
        probabilities=probabilities,
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_AUTO,
    )

    assert decision.category == "insult"  # argmax, even below the threshold


def test_clean_is_never_a_violation_how_sure_it_is() -> None:
    probabilities = {"spam": 0.01, "ads": 0.01, "insult": 0.01, "clean": 0.97}

    decision = decide(
        probabilities=probabilities,
        violation_threshold=VIOLATION,
        suspicion_threshold=0.50,
        mode=MODE_AUTO,
    )

    assert decision.outcome == "clean"
    assert decision.category == CLEAN


def test_below_the_threshold_the_message_is_left_alone() -> None:
    decision = decide(
        probabilities=HALF_SURE,
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_AUTO,
    )

    assert decision.outcome == "clean"
    assert decision.category == "spam"


def test_exactly_at_the_threshold_counts_as_the_violation_zone() -> None:
    probabilities = {"spam": 0.90, "ads": 0.03, "insult": 0.03, "clean": 0.04}

    decision = decide(
        probabilities=probabilities,
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_AUTO,
    )

    assert decision.outcome == "violation"


def test_in_auto_moderation_the_middle_band_is_a_suspicion() -> None:
    decision = decide(
        probabilities=MID_SURE,
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_AUTO,
    )

    assert decision.outcome == "suspicion"
    assert decision.category == "spam"
    assert decision.confidence == pytest.approx(0.70)


def test_exactly_at_the_suspicion_threshold_counts_as_the_suspicion_zone() -> None:
    probabilities = {"spam": 0.60, "ads": 0.20, "insult": 0.10, "clean": 0.10}

    decision = decide(
        probabilities=probabilities,
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_AUTO,
    )

    assert decision.outcome == "suspicion"


def test_in_observation_mode_a_high_confidence_verdict_is_still_a_suspicion() -> None:
    decision = decide(
        probabilities=SPAMMY,
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_OBSERVATION,
    )

    # Nothing is removed automatically: the Admin decides (ADR-0003).
    assert decision.outcome == "suspicion"


def test_in_observation_mode_the_middle_band_is_a_suspicion_too() -> None:
    decision = decide(
        probabilities=MID_SURE,
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_OBSERVATION,
    )

    assert decision.outcome == "suspicion"


def test_below_the_suspicion_threshold_nothing_happens_in_either_mode() -> None:
    for mode in (MODE_AUTO, MODE_OBSERVATION):
        decision = decide(
            probabilities=HALF_SURE,
            violation_threshold=VIOLATION,
            suspicion_threshold=SUSPICION,
            mode=mode,
        )

        assert decision.outcome == "clean"


def test_a_disabled_category_is_ignored_and_the_next_enabled_label_is_used() -> None:
    # Advertising wins the raw argmax, but the chat has switched it off.
    probabilities = {"spam": 0.10, "ads": 0.80, "insult": 0.70, "clean": 0.05}

    decision = decide(
        probabilities=probabilities,
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_AUTO,
        enabled_categories=("spam", "insult"),
    )

    assert decision.category == "insult"
    assert decision.confidence == pytest.approx(0.70)
    assert decision.outcome == "suspicion"  # 0.70 sits in the middle band


def test_a_disabled_category_above_the_threshold_produces_no_violation() -> None:
    # Advertising at 0.97 would have violated; the next enabled label is far below.
    probabilities = {"spam": 0.02, "ads": 0.97, "insult": 0.005, "clean": 0.005}

    decision = decide(
        probabilities=probabilities,
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_AUTO,
        enabled_categories=("spam", "insult"),
    )

    assert decision.outcome == "clean"
    assert decision.category == "spam"


def test_with_every_category_disabled_the_verdict_is_clean() -> None:
    decision = decide(
        probabilities={"spam": 0.90, "ads": 0.05, "insult": 0.04, "clean": 0.01},
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_AUTO,
        enabled_categories=(),
    )

    assert decision.outcome == "clean"
    assert decision.category == CLEAN


def test_clean_counts_even_when_it_is_not_the_raw_argmax() -> None:
    # Only insults are checked, and clean still beats them.
    probabilities = {"spam": 0.30, "ads": 0.10, "insult": 0.05, "clean": 0.55}

    decision = decide(
        probabilities=probabilities,
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_AUTO,
        enabled_categories=("insult",),
    )

    assert decision.outcome == "clean"
    assert decision.category == CLEAN


def test_without_the_argument_every_label_counts_as_before() -> None:
    decision = decide(
        probabilities={"spam": 0.10, "ads": 0.80, "insult": 0.05, "clean": 0.05},
        violation_threshold=VIOLATION,
        suspicion_threshold=SUSPICION,
        mode=MODE_AUTO,
    )

    assert decision.category == "ads"  # the default stays: the full argmax
