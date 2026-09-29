"""End-to-end: foreign channels are banned, and unbanned from the alert (§4).

A Violation posted as a foreign channel deletes the message and calls
`banChatSenderChat`. There is no ladder step, no Restriction of a user,
no Chat Notice and no Appeal. Admins get an Admin Alert with a 🟢 Unban
button; pressing it lifts the ban and edits every alert copy.
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChat
from app.db.models import ChatNotice, Violation
from app.menu.callbacks import UnbanChannelCallback
from sqlalchemy import select

from tests.support.backend import SPAMMY
from tests.support.harness import TestApp, app_fixture, auto_moderation_chat
from tests.support.telegram import chat_facts
from tests.support.updates import foreign_channel_message_update, private_callback_update

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(2200, 10)
_chat_ids = count(-100860, -10)
_channel_ids = count(-1009100, -10)

SPAM_TEXT = "Buy cheap crypto now, DM me https://t.me/+abc"


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
def foreign_channel_id() -> Iterator[int]:
    yield next(_channel_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        yield app


async def banned_channel(
    app: TestApp, admin_id: int, chat_id: int, foreign_channel_id: int, message_id: int = 140
) -> Violation:
    """A foreign channel's spam post: the ban already happened."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(
        GetChat, chat_facts(chat_id, "supergroup", linked_chat_id=foreign_channel_id + 1)
    )
    app.backend.script(SPAMMY)
    await app.feed(
        foreign_channel_message_update(
            chat_id, foreign_channel_id, SPAM_TEXT, message_id=message_id
        )
    )
    async with app.session_maker() as db:
        (violation,) = (await db.execute(select(Violation))).scalars().all()
    return violation


async def test_a_foreign_channel_violation_deletes_and_bans_without_notice(
    app: TestApp, admin_id: int, chat_id: int, foreign_channel_id: int
) -> None:
    violation = await banned_channel(app, admin_id, chat_id, foreign_channel_id)

    # §4: delete and ban — no RestrictChatMember of a user, no Chat Notice,
    # and the one private message is the Admin Alert.
    assert app.session.call_names() == [
        "GetChat",
        "DeleteMessage",
        "BanChatSenderChat",
        "SendMessage",
    ]
    (delete,) = app.session.calls_of("DeleteMessage")
    assert delete.method.message_id == 140
    (ban,) = app.session.calls_of("BanChatSenderChat")
    assert (ban.method.chat_id, ban.method.sender_chat_id) == (
        chat_id,
        foreign_channel_id,
    )
    (alert,) = app.session.calls_of("SendMessage")
    assert alert.method.chat_id == admin_id
    assert "Foreign Channel" in (alert.method.text or "")

    # The Violation is recorded with no Restriction — the sender-chat ban (§12).
    assert violation.user_id == foreign_channel_id
    assert violation.restriction_seconds is None
    assert violation.restricted_until is None
    assert violation.step_index == 0
    # No Chat Notice exists: no Appeal is possible (§4).
    async with app.session_maker() as db:
        assert (await db.execute(select(ChatNotice))).scalars().all() == []


async def test_the_unban_button_lifts_the_ban_and_edits_the_alert(
    app: TestApp, admin_id: int, chat_id: int, foreign_channel_id: int
) -> None:
    violation = await banned_channel(app, admin_id, chat_id, foreign_channel_id)
    (alert,) = app.session.calls_of("SendMessage")
    copy_id = alert.result.message_id
    (button,) = alert.method.reply_markup.inline_keyboard[0]
    assert button.text == "🟢 Unban"
    data = UnbanChannelCallback(chat_id=chat_id, violation_id=violation.id).pack()
    assert button.callback_data == data

    app.session.calls.clear()
    await app.feed(
        private_callback_update(admin_id, data, copy_id, language_code="en", username="alpha")
    )

    # The ban lifts and the alert copy shows who unbanned, buttons gone (§9).
    (unban,) = app.session.calls_of("UnbanChatSenderChat")
    assert (unban.method.chat_id, unban.method.sender_chat_id) == (
        chat_id,
        foreign_channel_id,
    )
    (edit,) = app.session.calls_of("EditMessageText")
    assert (edit.method.chat_id, edit.method.message_id) == (admin_id, copy_id)
    assert "alpha" in (edit.method.text or "")
    assert edit.method.reply_markup is None


async def test_a_foreign_channel_violation_never_counts_on_a_ladder(
    app: TestApp, admin_id: int, chat_id: int, foreign_channel_id: int
) -> None:
    """A second banned channel also gets no Step: the ladder is for Members (§4)."""
    await banned_channel(app, admin_id, chat_id, foreign_channel_id)
    second = foreign_channel_id - 1
    app.session.script(
        GetChat, chat_facts(chat_id, "supergroup", linked_chat_id=foreign_channel_id + 1)
    )
    await app.feed(foreign_channel_message_update(chat_id, second, SPAM_TEXT, message_id=141))
    async with app.session_maker() as db:
        violations = (await db.execute(select(Violation))).scalars().all()
    assert len(violations) == 2
    assert all(v.restriction_seconds is None for v in violations)
    assert all(v.restricted_until is None for v in violations)
