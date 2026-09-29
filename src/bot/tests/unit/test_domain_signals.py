"""Pure signal adjustments (§3): member facts and text → threshold shifts.

The shifts lower or raise *both* thresholds; the result is clamped to
[0.05, 0.99]. No Telegram, no DB, no I/O.
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.domain import signals as domain_signals
from app.domain.signals import (
    ESTABLISHED_DAYS,
    ESTABLISHED_MESSAGES,
    MAX_THRESHOLD,
    MIN_THRESHOLD,
    adjusted_thresholds,
    is_established,
    is_new,
)


def adjusted_threshold_shifts(
    *,
    first_seen_at: datetime,
    checked_count: int,
    flagged_count: int,
    has_link_invite_or_mention: bool,
    has_invite_link: bool,
    now: datetime,
    shifts: dict[str, float],
) -> tuple[float, ...]:
    """The test's facade over the domain rule: member facts → shifts."""
    return domain_signals.threshold_shifts(
        is_new_member=is_new(first_seen_at=first_seen_at, checked_count=checked_count, now=now),
        has_link_invite_or_mention=has_link_invite_or_mention,
        has_invite_link=has_invite_link,
        is_established_member=is_established(
            first_seen_at=first_seen_at,
            checked_count=checked_count,
            flagged_count=flagged_count,
            now=now,
        ),
        shifts=shifts,
    )


SEEN = datetime(2026, 1, 1, tzinfo=UTC)

# The §3 default shifts, as config.toml ships them.
SHIFTS = {"new_member_link": -0.10, "invite_link": -0.05, "established_member": 0.05}


# --- What makes a Member new or established (§3) -----------------------------


def test_a_member_under_24_hours_or_under_3_checked_messages_is_new() -> None:
    assert is_new(first_seen_at=SEEN - timedelta(hours=23), checked_count=50, now=SEEN)
    assert is_new(first_seen_at=SEEN - timedelta(days=9), checked_count=2, now=SEEN)
    # Neither bound holds: not new.
    assert not is_new(first_seen_at=SEEN - timedelta(hours=25), checked_count=3, now=SEEN)


def test_a_member_is_established_only_with_all_three_conditions() -> None:
    # 30 days and 50 checked messages, never flagged (§3).
    assert is_established(
        first_seen_at=SEEN - timedelta(days=ESTABLISHED_DAYS),
        checked_count=ESTABLISHED_MESSAGES,
        flagged_count=0,
        now=SEEN,
    )
    # One flag ever: never "established" again (§3).
    assert not is_established(
        first_seen_at=SEEN - timedelta(days=ESTABLISHED_DAYS),
        checked_count=ESTABLISHED_MESSAGES,
        flagged_count=1,
        now=SEEN,
    )
    # 29 days: not yet.
    assert not is_established(
        first_seen_at=SEEN - timedelta(days=ESTABLISHED_DAYS) + timedelta(hours=1),
        checked_count=ESTABLISHED_MESSAGES,
        flagged_count=0,
        now=SEEN,
    )
    # 49 checked messages: not yet.
    assert not is_established(
        first_seen_at=SEEN - timedelta(days=ESTABLISHED_DAYS),
        checked_count=ESTABLISHED_MESSAGES - 1,
        flagged_count=0,
        now=SEEN,
    )


# --- Which signal fires for one message (§3) ---------------------------------


def test_a_new_member_posting_a_link_takes_the_new_member_shift() -> None:
    shifts = adjusted_threshold_shifts(
        first_seen_at=SEEN - timedelta(hours=1),
        checked_count=0,
        flagged_count=0,
        has_link_invite_or_mention=True,
        has_invite_link=False,
        now=SEEN,
        shifts=SHIFTS,
    )
    assert shifts == (SHIFTS["new_member_link"],)


def test_an_established_member_takes_the_established_shift() -> None:
    shifts = adjusted_threshold_shifts(
        first_seen_at=SEEN - timedelta(days=ESTABLISHED_DAYS),
        checked_count=ESTABLISHED_MESSAGES,
        flagged_count=0,
        has_link_invite_or_mention=False,
        has_invite_link=False,
        now=SEEN,
        shifts=SHIFTS,
    )
    assert shifts == (SHIFTS["established_member"],)


def test_an_invite_link_shifts_even_for_an_established_member() -> None:
    """The invite-link signal is about the text, not the sender (§3)."""
    shifts = adjusted_threshold_shifts(
        first_seen_at=SEEN - timedelta(days=ESTABLISHED_DAYS),
        checked_count=ESTABLISHED_MESSAGES,
        flagged_count=0,
        has_link_invite_or_mention=False,
        has_invite_link=True,
        now=SEEN,
        shifts=SHIFTS,
    )
    assert shifts == (SHIFTS["invite_link"], SHIFTS["established_member"])


def test_a_new_member_with_an_invite_link_gets_both_negative_shifts() -> None:
    shifts = adjusted_threshold_shifts(
        first_seen_at=SEEN - timedelta(hours=1),
        checked_count=0,
        flagged_count=0,
        has_link_invite_or_mention=True,
        has_invite_link=True,
        now=SEEN,
        shifts=SHIFTS,
    )
    assert shifts == (SHIFTS["new_member_link"], SHIFTS["invite_link"])


def test_an_ordinary_message_from_an_ordinary_member_shifts_nothing() -> None:
    shifts = adjusted_threshold_shifts(
        first_seen_at=SEEN - timedelta(days=2),
        checked_count=10,
        flagged_count=0,
        has_link_invite_or_mention=False,
        has_invite_link=False,
        now=SEEN,
        shifts=SHIFTS,
    )
    assert shifts == ()


def test_shifts_come_entirely_from_config() -> None:
    """A different config.toml changes the applied shifts (§3: values from config)."""
    shifts = adjusted_threshold_shifts(
        first_seen_at=SEEN - timedelta(hours=1),
        checked_count=0,
        flagged_count=0,
        has_link_invite_or_mention=True,
        has_invite_link=False,
        now=SEEN,
        shifts={"new_member_link": -0.4, "invite_link": -0.05, "established_member": 0.05},
    )
    assert shifts == (-0.4,)


# --- Clamping (§3) ------------------------------------------------------------


def test_both_thresholds_shift_and_clamp_to_the_band() -> None:
    violation, suspicion = adjusted_thresholds(
        violation_threshold=0.90,
        suspicion_threshold=0.60,
        shifts=(-0.10, -0.05),
    )
    assert (violation, suspicion) == (pytest.approx(0.75), pytest.approx(0.45))


def test_the_result_never_drops_below_the_floor() -> None:
    violation, suspicion = adjusted_thresholds(
        violation_threshold=0.10,
        suspicion_threshold=0.07,
        shifts=(-0.10, -0.05),
    )
    assert violation == MIN_THRESHOLD
    assert suspicion == MIN_THRESHOLD


def test_the_result_never_rises_above_the_ceiling() -> None:
    violation, suspicion = adjusted_thresholds(
        violation_threshold=0.99,
        suspicion_threshold=0.98,
        shifts=(0.05,),
    )
    assert violation == MAX_THRESHOLD
    assert suspicion == MAX_THRESHOLD


def test_the_floor_and_ceiling_are_the_spec_bands() -> None:
    assert MIN_THRESHOLD == 0.05
    assert MAX_THRESHOLD == 0.99
