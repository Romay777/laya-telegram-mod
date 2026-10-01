"""End-to-end: where the Chat Notice lands (§7).

The notice goes where the Member can still see it: into a forum topic via
`message_thread_id`, into the comment thread under a channel post as a
reply at the auto-forwarded post, and — as before — into the chat root
when the message was a plain one. A punished Suspicion posts from the
anchor frozen on its row. Assertions only look at the recorded Bot API
calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import Suspicion
from app.menu.callbacks import SuspicionDecideCallback
from sqlalchemy import select

from tests.support.backend import SPAMMY
from tests.support.harness import TestApp, app_fixture, auto_moderation_chat
from tests.support.telegram import member_member
from tests.support.updates import group_message_update, private_callback_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(4800, 10)
_chat_ids = count(-101000, -10)

# Right in the Balanced suspicion zone (0.60 ≤ p < 0.90).
SPAMMY_MID = {"spam": 0.70, "ads": 0.10, "insult": 0.10, "clean": 0.10}


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def member_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        yield app


async def feed_a_violation(
    app: TestApp, member_id: int, chat_id: int, message_id: int, **message_kwargs
) -> None:
    app.backend.script(SPAMMY)
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(
        group_message_update(
            chat_id, member_id, "Buy cheap crypto now", message_id=message_id, **message_kwargs
        )
    )


async def test_a_comment_s_notice_replies_at_the_thread_root(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)

    # The flagged message is a comment under a channel post: its thread id
    # is the auto-forwarded post's message id (§7).
    await feed_a_violation(app, member_id, chat_id, 91, message_thread_id=501)

    (notice, _alert) = app.session.calls_of("SendMessage")
    assert notice.method.chat_id == chat_id
    assert notice.method.message_thread_id is None  # not a forum: the field would be ignored
    assert notice.method.reply_parameters is not None
    assert notice.method.reply_parameters.message_id == 501
    assert notice.method.reply_parameters.allow_sending_without_reply is True


async def test_a_forum_topic_s_notice_sends_to_the_topic(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)

    await feed_a_violation(app, member_id, chat_id, 92, message_thread_id=42, is_topic_message=True)

    (notice, _alert) = app.session.calls_of("SendMessage")
    assert notice.method.message_thread_id == 42
    assert notice.method.reply_parameters is None


async def test_a_plain_message_s_notice_stays_in_the_chat_root(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)

    await feed_a_violation(app, member_id, chat_id, 93)

    (notice, _alert) = app.session.calls_of("SendMessage")
    assert notice.method.message_thread_id is None
    assert notice.method.reply_parameters is None


async def test_a_punished_suspicion_s_notice_uses_the_frozen_anchor(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)

    # A mid-confidence comment raises a Suspicion; the anchor is frozen now.
    app.backend.script(SPAMMY_MID)
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(
        group_message_update(
            chat_id, member_id, "buy crypto cheap", message_id=94, message_thread_id=501
        )
    )

    async with app.session_maker() as db:
        suspicion = (await db.execute(select(Suspicion))).scalars().one()
    assert suspicion.anchor_kind == "reply"
    assert suspicion.anchor_message_id == 501

    # An Admin punishes long after: the message is deleted first, but the
    # frozen anchor still sends the notice into the comment thread.
    (alert,) = app.session.calls_of("SendMessage")
    first_copy = alert.result.message_id
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(
        private_callback_update(
            admin_id,
            SuspicionDecideCallback(chat_id=chat_id, suspicion_id=suspicion.id, punish=True).pack(),
            first_copy,
            language_code="en",
            username="alpha",
        )
    )

    (notice,) = [
        call for call in app.session.calls_of("SendMessage") if call.method.chat_id == chat_id
    ]
    assert notice.method.reply_parameters is not None
    assert notice.method.reply_parameters.message_id == 501
    assert notice.method.reply_parameters.allow_sending_without_reply is True
