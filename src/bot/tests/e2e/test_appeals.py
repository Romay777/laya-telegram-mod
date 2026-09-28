"""End-to-end: Appeals (§8).

A restricted Member presses "🙋 It's a mistake" under the Chat Notice; the
admin side decides, and the outcome lands on the notice and on every alert
copy. The deleted message stays deleted throughout.
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import Violation
from app.menu.callbacks import AppealCallback
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import TestApp, app_fixture, auto_moderation_chat
from tests.support.telegram import member_member
from tests.support.updates import group_message_update, private_callback_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(2200, 10)
_member_ids = count(2950, 5)
_chat_ids = count(-100900, -10)

SPAMMY = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}
SPAM_TEXT = "Buy cheap crypto now, DM me https://t.me/+abc"


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def member_id() -> Iterator[int]:
    yield next(_member_ids)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        yield app


async def the_violation(session_maker: async_sessionmaker[AsyncSession]) -> Violation:
    async with session_maker() as db:
        (violation,) = (await db.execute(select(Violation))).scalars().all()
        return violation


async def spam_message(app: TestApp, chat_id: int, member_id: int, message_id: int) -> None:
    app.backend.script(SPAMMY)
    app.session.script(GetChatMember, member_member(user(member_id)))  # the sender
    app.session.calls.clear()
    await app.feed(
        group_message_update(
            chat_id, member_id, SPAM_TEXT, message_id=message_id, sender_name="Spammer"
        )
    )


async def test_the_notice_carries_the_appeal_button_with_the_violation_id(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    await spam_message(app, chat_id, member_id, message_id=77)

    notice = app.session.calls_of("SendMessage")[0]  # the Chat Notice, then the alerts
    markup = notice.method.reply_markup
    assert markup is not None
    (row,) = markup.inline_keyboard
    (button,) = row
    assert button.text == "🙋 It's a mistake"
    callback = AppealCallback.unpack(button.callback_data)
    assert callback.chat_id == chat_id
    violation = await the_violation(app.session_maker)
    assert callback.violation_id == violation.id


async def test_the_notice_leaves_the_button_out_while_no_admin_receives_appeals(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    # The Linker switches their alerts off: nobody receives Appeals any more (§9).
    for data in (f"chat-alerts:{chat_id}:", f"chat-alerts:{chat_id}:off"):
        await app.feed(private_callback_update(admin_id, data, menu, language_code="en"))
    await spam_message(app, chat_id, member_id, message_id=77)

    (notice,) = app.session.calls_of("SendMessage")
    assert notice.method.reply_markup is None
