"""Pure signal adjustments (§3): the Member row and the text shift thresholds.

Three signals exist in v1 (§3), each one a shift applied to *both*
thresholds, and the result is clamped to [0.05, 0.99]:

- **New Member** (first seen < 24 h ago, or < 3 checked messages) posting
  a link, invite or @mention: -0.10;
- Telegram invite link (`t.me/+…`, `t.me/joinchat/…`): -0.05;
- **Established Member** (≥ 30 days in the chat, ≥ 50 checked messages,
  never flagged): +0.05.

The shift values arrive from config (§3); this module holds only the
rules. No Telegram, no DB, no I/O.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta

#: The §3 bounds every adjusted threshold is clamped to.
MIN_THRESHOLD = 0.05
MAX_THRESHOLD = 0.99

#: The §3 Established Member bounds: days in the chat and checks without a flag.
ESTABLISHED_DAYS = 30
ESTABLISHED_MESSAGES = 50

#: The §3 New Member bounds: either one makes the Member new.
NEW_MEMBER_HOURS = 24
NEW_MEMBER_MESSAGES = 3

#: The `signals` shift names, as config.toml's `[signals]` section spells them.
NEW_MEMBER_LINK = "new_member_link"
INVITE_LINK = "invite_link"
ESTABLISHED_MEMBER = "established_member"


def is_new(*, first_seen_at: datetime, checked_count: int, now: datetime) -> bool:
    """A Member under 24 hours or under 3 checked messages is new (§3)."""
    if now - first_seen_at < timedelta(hours=NEW_MEMBER_HOURS):
        return True
    return checked_count < NEW_MEMBER_MESSAGES


def is_established(
    *, first_seen_at: datetime, checked_count: int, flagged_count: int, now: datetime
) -> bool:
    """Established (§3): 30+ days, 50+ checked messages, never flagged."""
    if now - first_seen_at < timedelta(days=ESTABLISHED_DAYS):
        return False
    if checked_count < ESTABLISHED_MESSAGES:
        return False
    return flagged_count == 0


def threshold_shifts(
    *,
    is_new_member: bool,
    has_link_invite_or_mention: bool,
    has_invite_link: bool,
    is_established_member: bool,
    shifts: Mapping[str, float],
) -> tuple[float, ...]:
    """The shifts one message triggers, in §3 order (each may be absent).

    `is_new_member` already knows the link fact: the New Member shift
    applies only when the message carries a link, invite or @mention. The
    invite-link shift is independent — an invite link in an Established
    Member's message still lowers the threshold (§3). Established and New
    are mutually exclusive by definition, so their shifts never combine.
    """
    applied: list[float] = []
    if is_new_member and has_link_invite_or_mention:
        applied.append(shifts[NEW_MEMBER_LINK])
    if has_invite_link:
        applied.append(shifts[INVITE_LINK])
    if is_established_member:
        applied.append(shifts[ESTABLISHED_MEMBER])
    return tuple(applied)


def adjusted_thresholds(
    *, violation_threshold: float, suspicion_threshold: float, shifts: tuple[float, ...]
) -> tuple[float, float]:
    """Both thresholds moved by every shift, clamped to the §3 band."""
    total = sum(shifts)
    return (
        clamp(violation_threshold + total),
        clamp(suspicion_threshold + total),
    )


def clamp(value: float) -> float:
    """Keep one threshold inside [0.05, 0.99] (§3)."""
    return max(MIN_THRESHOLD, min(MAX_THRESHOLD, value))
