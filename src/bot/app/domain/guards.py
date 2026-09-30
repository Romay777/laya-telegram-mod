"""The cross-chat guard (§13): a loaded row must belong to the callback's chat.

Callback data travels in the button, so it can be forged to name one chat
while naming another chat's row. Every chat-scoped callback therefore
re-checks Admin access (§13) and then keeps only rows of the chat the
callback carries. No Telegram, no DB, no I/O.
"""

from typing import Protocol


class ChatScoped(Protocol):
    """A row that belongs to one chat: a Violation, a Suspicion, a card."""

    @property
    def chat_id(self) -> int: ...


def belongs_to_chat(row: ChatScoped | None, chat_id: int) -> bool:
    """True when `row` exists and belongs to `chat_id` — the §13 guard."""
    return row is not None and row.chat_id == chat_id
