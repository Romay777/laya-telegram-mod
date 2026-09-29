"""Pure chat-lifecycle logic (§10): what a `my_chat_member` event means.

The update alone decides the transition: no Telegram, no DB, no I/O. The
required rights come from the Linking module's one list, so Linking and
Suspension can never disagree about what "a required right" is.
"""

from dataclasses import dataclass
from enum import Enum, auto

from app.domain.linking import REQUIRED_RIGHTS


class LifecycleAction(Enum):
    """What the bot does with a Linked Chat after one `my_chat_member` event (§10)."""

    NONE = auto()  # nothing owed: the bot's standing did not change in a way §10 acts on
    SUSPEND = auto()  # rights were lost or the bot was demoted: checks stop
    REACTIVATE = auto()  # the rights came back: checks resume
    REMOVE = auto()  # the bot left the chat: removed_at starts the retention clock


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    """The transition one event drives, plus what is missing when suspended."""

    action: LifecycleAction = LifecycleAction.NONE
    missing_rights: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.action is not LifecycleAction.NONE


def _rights_of(member_status: str, can_delete: bool, can_restrict: bool) -> tuple[str, ...]:
    """The required rights the membership lacks, in listing order."""
    if member_status != "administrator":
        # Demoted to member, kicked, or left: every required right is gone.
        return REQUIRED_RIGHTS
    held = {"can_delete_messages": can_delete, "can_restrict_members": can_restrict}
    return tuple(right for right in REQUIRED_RIGHTS if not held[right])


def rights_missing(
    *, status: str, can_delete_messages: bool, can_restrict_members: bool
) -> tuple[str, ...]:
    """The required rights one bot membership lacks, in listing order (§10).

    A bot that is no longer an administrator lacks both, whatever the
    flags on the membership object say.
    """
    return _rights_of(status, can_delete_messages, can_restrict_members)


def lifecycle_outcome(
    *,
    was_active: bool,
    new_status: str,
    can_delete_messages: bool,
    can_restrict_members: bool,
) -> LifecycleOutcome:
    """Classify one `my_chat_member` event for a Linked Chat (§10).

    - An `active` chat whose rights disappeared, or whose bot was demoted,
      becomes `suspended` and reports exactly which rights are missing.
    - A `suspended` chat whose rights came back becomes `active` again.
    - A bot removed (`left`) or banned (`kicked`) makes the chat `removed`,
      whatever stood before.
    """
    if new_status in ("left", "kicked"):
        return LifecycleOutcome(action=LifecycleAction.REMOVE)

    missing = _rights_of(new_status, can_delete_messages, can_restrict_members)
    if missing:
        if was_active:
            return LifecycleOutcome(action=LifecycleAction.SUSPEND, missing_rights=missing)
        # Already suspended, still missing: nothing new to tell.
        return LifecycleOutcome()

    if was_active:
        return LifecycleOutcome()
    return LifecycleOutcome(action=LifecycleAction.REACTIVATE)


def removed_after(*, removed_at, now, retention_days: int) -> bool:
    """Whether a Removed Chat's 30-day retention has run out (§10, §11)."""
    return (now - removed_at).total_seconds() >= retention_days * 86400
