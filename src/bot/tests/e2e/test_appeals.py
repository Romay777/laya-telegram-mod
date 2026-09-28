"""End-to-end: Appeals (§8).

A restricted Member presses "🙋 It's a mistake" under the Chat Notice; the
admin side decides, and the outcome lands on the notice and on every alert
copy. The deleted message stays deleted throughout.
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import AdminAlert, Appeal, Violation
from app.menu.callbacks import AppealCallback, AppealDecideCallback
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import TestApp, app_fixture, auto_moderation_chat
from tests.support.telegram import member_member
from tests.support.updates import (
    group_callback_update,
    group_message_update,
    private_callback_update,
    user,
)

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


async def alert_copy(
    session_maker: async_sessionmaker[AsyncSession], appeal_id: int
) -> AdminAlert | None:
    """The one recorded copy of an Appeal alert in these single-admin tests."""
    async with session_maker() as db:
        return await db.scalar(
            select(AdminAlert).where(
                AdminAlert.subject_type == "appeal", AdminAlert.subject_id == appeal_id
            )
        )


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


async def appeal_press(
    app: TestApp,
    chat_id: int,
    member_id: int,
    violation_id: int,
    notice_message_id: int,
) -> None:
    await app.feed(
        group_callback_update(
            member_id,
            AppealCallback(chat_id=chat_id, violation_id=violation_id).pack(),
            chat_id=chat_id,
            message_id=notice_message_id,
            sender_name="Spammer",
        )
    )


async def test_filing_an_appeal_tells_the_admins_and_calms_the_notice(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    await spam_message(app, chat_id, member_id, message_id=77)
    notice = app.session.calls_of("SendMessage")[0]
    violation = await the_violation(app.session_maker)
    app.session.calls.clear()

    await appeal_press(app, chat_id, member_id, violation.id, notice.result.message_id)

    # The notice button is replaced by the pending text (§8): markup first, then text.
    assert app.session.call_names() == [
        "EditMessageReplyMarkup",
        "EditMessageText",
        "SendMessage",  # the Appeal Admin Alert (§8 step 3)
        "AnswerCallbackQuery",
    ]
    (markup_edit,) = app.session.calls_of("EditMessageReplyMarkup")
    assert (markup_edit.method.chat_id, markup_edit.method.message_id) == (
        chat_id,
        notice.result.message_id,
    )
    assert markup_edit.method.reply_markup is None
    (text_edit,) = app.session.calls_of("EditMessageText")
    assert text_edit.method.text == "⏳ Appeal sent to admins"

    (alert,) = app.session.calls_of("SendMessage")
    assert alert.method.chat_id == admin_id
    text = alert.method.text or ""
    assert "⚠️ My Chat" in text
    assert "Member: Spammer" in text
    assert "Spam, 97%" in text
    assert "Restriction: 1 hour" in text
    assert "The Member has appealed" in text
    assert SPAM_TEXT in text  # the deleted message, quoted with its entities (§8)
    entities = alert.method.entities or []
    assert any(entity.type == "blockquote" for entity in entities)
    (row,) = alert.method.reply_markup.inline_keyboard
    assert [button.text for button in row] == ["🟢 Lift restriction", "🔴 Reject"]
    lift, reject = row
    assert AppealDecideCallback.unpack(lift.callback_data).approve is True
    assert AppealDecideCallback.unpack(reject.callback_data).approve is False

    async with app.session_maker() as db:
        (appeal,) = (await db.execute(select(Appeal))).scalars().all()
    assert appeal.violation_id == violation.id
    assert appeal.status == "pending"
    assert appeal.decided_by is None
    copy = await alert_copy(app.session_maker, appeal.id)
    assert copy is not None
    assert (copy.subject_type, copy.admin_id, copy.message_id) == (
        "appeal",
        admin_id,
        alert.result.message_id,
    )


async def test_a_press_by_someone_else_is_refused_with_a_toast(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    await spam_message(app, chat_id, member_id, message_id=77)
    notice = app.session.calls_of("SendMessage")[0]
    violation = await the_violation(app.session_maker)
    bystander = member_id + 1
    app.session.calls.clear()

    await app.feed(
        group_callback_update(
            bystander,
            AppealCallback(chat_id=chat_id, violation_id=violation.id).pack(),
            chat_id=chat_id,
            message_id=notice.result.message_id,
            sender_name="Bystander",
        )
    )

    # Only the toast: the notice keeps its button, no appeal, no alert (§8).
    assert app.session.call_names() == ["AnswerCallbackQuery"]
    (answer,) = app.session.calls_of("AnswerCallbackQuery")
    assert answer.method.text == "This button isn't for you"
    async with app.session_maker() as db:
        assert (await db.execute(select(Appeal))).scalars().all() == []


async def test_a_second_appeal_is_refused_with_a_toast(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    await spam_message(app, chat_id, member_id, message_id=77)
    notice = app.session.calls_of("SendMessage")[0]
    violation = await the_violation(app.session_maker)
    await appeal_press(app, chat_id, member_id, violation.id, notice.result.message_id)
    app.session.calls.clear()

    await appeal_press(app, chat_id, member_id, violation.id, notice.result.message_id)

    assert app.session.call_names() == ["AnswerCallbackQuery"]
    (answer,) = app.session.calls_of("AnswerCallbackQuery")
    assert answer.method.text == "Already sent"
    async with app.session_maker() as db:
        appeals = (await db.execute(select(Appeal))).scalars().all()
    assert len(appeals) == 1
