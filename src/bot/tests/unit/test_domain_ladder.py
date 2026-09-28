"""Penalty Ladder maths (§6): Step selection and when a Restriction ends."""

from datetime import UTC, datetime, timedelta

from app.domain.ladder import restricted_until, select_step

DEFAULT_LADDER = (3600, 86400, 0)  # 1 hour → 24 hours → forever
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_the_first_active_violation_takes_the_first_step() -> None:
    assert select_step(DEFAULT_LADDER, active_count=1) == (0, 3600)


def test_the_second_and_third_take_the_next_steps() -> None:
    assert select_step(DEFAULT_LADDER, active_count=2) == (1, 86400)
    assert select_step(DEFAULT_LADDER, active_count=3) == (2, 0)


def test_past_the_last_step_the_last_step_repeats() -> None:
    assert select_step(DEFAULT_LADDER, active_count=4) == (2, 0)
    assert select_step(DEFAULT_LADDER, active_count=10) == (2, 0)


def test_a_timed_step_ends_at_now_plus_the_duration() -> None:
    assert restricted_until(NOW, 3600) == NOW + timedelta(hours=1)


def test_forever_is_step_zero_and_never_ends() -> None:
    assert restricted_until(NOW, 0) is None


def test_durations_telegram_treats_as_forever_never_end() -> None:
    assert restricted_until(NOW, 29) is None  # shorter than 30 seconds
    assert restricted_until(NOW, 366 * 24 * 60 * 60 + 1) is None  # longer than 366 days


def test_the_boundary_durations_still_end() -> None:
    assert restricted_until(NOW, 30) == NOW + timedelta(seconds=30)
    assert restricted_until(NOW, 366 * 24 * 60 * 60) == NOW + timedelta(days=366)
