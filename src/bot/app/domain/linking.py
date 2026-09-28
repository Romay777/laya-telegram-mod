"""Pure Linking logic: the promotion checks of §10 step 3, in order.

The checks run in this order:

1. The chat is a supergroup. A basic group short-circuits: it needs its own
   upgrade explanation, and the other checks say nothing useful about it.
2. The bot holds both required admin rights.
3. The person who added the bot is `creator` or `administrator`.

Whatever fails is reported, so the Admin's Menu can list exactly what is
missing. No Telegram, no DB, no I/O.
"""

import re
from dataclasses import dataclass

#: The rights the bot needs on a Linked Chat (§10). Order matters: it is the
#: order the missing ones are listed in.
REQUIRED_RIGHTS: tuple[str, ...] = ("can_delete_messages", "can_restrict_members")

#: Chat statuses that count as an Admin of the chat.
ADMIN_STATUSES = frozenset({"creator", "administrator"})

#: A negative integer: a chat id, never a user id.
_CHAT_ID = re.compile(r"-\d+")

#: The §12 defaults a Linked Chat is created with on Linking.
DEFAULT_LADDER: tuple[int, ...] = (3600, 86400, 0)  # 1 hour → 24 hours → forever
DEFAULT_EXPIRY_SECONDS = 30 * 24 * 60 * 60  # 30 days
DEFAULT_MODE = "observation"
DEFAULT_SENSITIVITY = "balanced"


@dataclass(frozen=True, slots=True)
class LinkingProblems:
    """Exactly what is missing, in check order."""

    basic_group: bool = False
    missing_rights: tuple[str, ...] = ()
    from_not_admin: bool = False

    def __bool__(self) -> bool:
        return self.basic_group or bool(self.missing_rights) or self.from_not_admin


def linking_problems(
    *,
    chat_type: str,
    can_delete_messages: bool,
    can_restrict_members: bool,
    linker_status: str | None,
) -> LinkingProblems:
    """Check one promotion attempt and report what is missing."""

    if chat_type != "supergroup":
        return LinkingProblems(basic_group=True)

    held = {
        "can_delete_messages": can_delete_messages,
        "can_restrict_members": can_restrict_members,
    }
    return LinkingProblems(
        missing_rights=tuple(right for right in REQUIRED_RIGHTS if not held[right]),
        from_not_admin=linker_status not in ADMIN_STATUSES,
    )


def parse_chat_ref(text: str | None) -> str | None:
    """The chat a fallback Linking input names (§10), as `getChat` accepts it.

    The Admin answers with an @username or a numeric id (`-100…`); anything
    else names no chat and is reported back instead of sent to Telegram.
    """

    if text is None:
        return None
    candidate = text.strip()
    if candidate.startswith("@") and len(candidate) > 1:
        return candidate
    if _CHAT_ID.fullmatch(candidate):
        return candidate
    return None
