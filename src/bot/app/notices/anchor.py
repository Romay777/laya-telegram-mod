"""The Chat Notice anchor (§7): where in the chat the notice lands.

A notice must reach the Member where they can still see it. A message in a
forum topic sends with `message_thread_id`; a comment under a channel post
sends as a reply to its thread's root — the auto-forwarded copy of the
post, whose message id a comment carries as its own `message_thread_id`
(a discussion group's comment section is exactly that message's thread).
Anything else sends as before, into the chat root.
"""

from dataclasses import dataclass
from typing import Final

from aiogram.types import Message

#: `sendMessage(message_thread_id=…)`: the notice goes into a forum topic.
TOPIC: Final = "topic"

#: A reply at the thread root: the notice goes into the comment thread.
REPLY: Final = "reply"


@dataclass(frozen=True, slots=True)
class NoticeAnchor:
    """Where a Chat Notice is sent (§7); `None` means the chat root."""

    kind: str  # TOPIC | REPLY
    message_id: int


def notice_anchor(message: Message) -> NoticeAnchor | None:
    """The anchor one flagged message gives its Chat Notice (§7)."""
    if message.is_topic_message and message.message_thread_id is not None:
        return NoticeAnchor(kind=TOPIC, message_id=message.message_thread_id)
    if message.message_thread_id is not None:
        # A comment under a channel post: its thread root is the
        # auto-forwarded post, and a reply to that root lands in the comment
        # thread — the root survives the comment's own deletion (§7).
        return NoticeAnchor(kind=REPLY, message_id=message.message_thread_id)
    reply = message.reply_to_message
    if reply is not None and reply.is_automatic_forward:
        # A client may leave `message_thread_id` empty; the auto-forwarded
        # post the comment replies to is the same thread root (§7).
        return NoticeAnchor(kind=REPLY, message_id=reply.message_id)
    return None
