"""Penalty Ladder maths (§6): Step selection and when a Restriction ends.

The Step applied is determined by how many Active Violations the Member
already has; past the last Step, the last Step repeats. A Step of 0 seconds
is forever. No Telegram, no DB, no I/O.
"""

from datetime import datetime, timedelta

#: Telegram treats Restriction durations below 30 s or above 366 days as
#: forever (§6), so the bot sends those as `until_date = 0` too.
MIN_TIMED_SECONDS = 30
MAX_TIMED_SECONDS = 366 * 24 * 60 * 60

#: The stored encoding of a forever Step: 0 seconds (§12).
FOREVER_SECONDS = 0


def select_step(ladder: tuple[int, ...] | list[int], *, active_count: int) -> tuple[int, int]:
    """The Step of the `active_count`-th Active Violation: its index and seconds.

    `active_count` starts at 1 (the count including the Violation being
    recorded). When the Member has more Active Violations than there are
    Steps, the last Step repeats (§6).
    """
    index = min(active_count, len(ladder)) - 1
    return index, int(ladder[index])


def restricted_until(now: datetime, step_seconds: int) -> datetime | None:
    """When a Restriction of `step_seconds` ends, or None for forever (§6)."""
    if step_seconds <= 0 or not MIN_TIMED_SECONDS <= step_seconds <= MAX_TIMED_SECONDS:
        return None
    return now + timedelta(seconds=step_seconds)


def violation_state(
    *, revoked_at: datetime | None, expires_at: datetime | None, now: datetime
) -> str:
    """The state of a Violation at `now` (§6): `active`, `expired` or `false_positive`.

    A revoked Violation is a False Positive whatever its Expiry; an unrevoked
    one stops being Active at its Expiry moment, the same boundary
    `count_active` draws.
    """
    if revoked_at is not None:
        return "false_positive"
    if expires_at is not None and expires_at <= now:
        return "expired"
    return "active"
