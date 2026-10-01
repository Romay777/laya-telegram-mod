"""The Chat Notice anchor (§7): where a notice for a flagged message lands.

A forum-topic message sends with `message_thread_id`; a comment under a
channel post sends as a reply to its thread root — the auto-forwarded
post, whose id the comment carries as its own `message_thread_id`; a plain
message carries no anchor at all.
"""

from datetime import UTC, datetime

from aiogram.types import Chat, Message, User
from app.notices.anchor import REPLY, TOPIC, notice_anchor


def a_message(message_id: int = 99, **kwargs) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=-100500, type="supergroup", title="My Chat"),
        from_user=User(id=7, is_bot=False, first_name="Member"),
        text="a message",
        **kwargs,
    )


def auto_forward(chat_id: int, message_id: int) -> Message:
    """The discussion group's copy of a channel post (§7)."""
    return a_message(
        message_id=message_id,
        sender_chat=Chat(id=chat_id, type="channel"),
        is_automatic_forward=True,
    )


def test_a_forum_topic_message_anchors_to_its_topic() -> None:
    anchor = notice_anchor(a_message(message_thread_id=42, is_topic_message=True))

    assert anchor is not None
    assert anchor.kind == TOPIC
    assert anchor.message_id == 42


def test_a_comment_anchors_to_its_thread_root() -> None:
    # A discussion group's comment: the thread root's message id arrives as
    # the comment's own `message_thread_id`, with no topic flag.
    anchor = notice_anchor(a_message(message_thread_id=501))

    assert anchor is not None
    assert anchor.kind == REPLY
    assert anchor.message_id == 501


def test_a_reply_to_the_auto_forwarded_post_anchors_without_a_thread_id() -> None:
    # An older client may leave `message_thread_id` empty; the auto-forward
    # the comment replies to is the same thread root (§7).
    anchor = notice_anchor(a_message(reply_to_message=auto_forward(-100900, 501)))

    assert anchor is not None
    assert anchor.kind == REPLY
    assert anchor.message_id == 501


def test_a_reply_to_a_plain_message_carries_no_anchor() -> None:
    anchor = notice_anchor(a_message(reply_to_message=a_message(message_id=501)))

    assert anchor is None


def test_a_plain_message_carries_no_anchor() -> None:
    assert notice_anchor(a_message()) is None


def test_a_forum_general_topic_message_carries_no_anchor() -> None:
    # The General topic's messages come without either field (§7): the
    # plain send is already where they belong.
    assert notice_anchor(a_message(message_thread_id=None, is_topic_message=None)) is None
