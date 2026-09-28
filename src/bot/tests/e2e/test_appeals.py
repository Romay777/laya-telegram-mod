"""End-to-end: Appeals (§8).

A restricted Member presses "🙋 It's a mistake" under the Chat Notice; the
admin side decides, and the outcome lands on the notice and on every alert
copy. The deleted message stays deleted throughout.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.methods import GetChat, GetChatMember
from aiogram.types import ChatPermissions, MessageEntity
from app.db.models import AdminAlert, Appeal, ChatNotice, FlaggedMessage, Violation
from app.menu.callbacks import AppealCallback, AppealDecideCallback, LiftRestrictionCallback
from app.scheduler import Scheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import (
    FIXED_NOW,
    TestApp,
    app_fixture,
    auto_moderation_chat,
    started_admin,
)
from tests.support.telegram import chat_facts, member_administrator, member_member, member_owner
from tests.support.updates import (
    group_callback_update,
    group_message_update,
    my_chat_member_update,
    private_callback_update,
    start_update,
    user,
)

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(2200, 10)
_member_ids = count(2950, 5)
_chat_ids = count(-100900, -10)

SPAMMY = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}
SPAM_TEXT = "Buy cheap crypto now, DM me https://t.me/+abc"
DEFAULT_PERMISSIONS = ChatPermissions(can_send_messages=True, can_send_polls=True)


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


async def fresh_notice(
    session_maker: async_sessionmaker[AsyncSession], violation_id: int
) -> ChatNotice | None:
    async with session_maker() as db:
        return await db.get(ChatNotice, violation_id)


async def spam_message(
    app: TestApp, chat_id: int, member_id: int, message_id: int, *, entities: bool = False
) -> None:
    app.backend.script(SPAMMY)
    app.session.script(GetChatMember, member_member(user(member_id)))  # the sender
    app.session.calls.clear()
    await app.feed(
        group_message_update(
            chat_id,
            member_id,
            SPAM_TEXT,
            message_id=message_id,
            sender_name="Spammer",
            entities=[MessageEntity(type="bold", offset=4, length=5)] if entities else None,
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
    # The flagged message carries formatting of its own (§8: entities kept).
    await spam_message(app, chat_id, member_id, message_id=77, entities=True)
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
    (bold,) = [entity for entity in entities if entity.type == "bold"]
    # Entity offsets are UTF-16 code units, not Python indices (§14).
    units = (alert.method.text or "").encode("utf-16-le")
    assert units[bold.offset * 2 : (bold.offset + bold.length) * 2].decode("utf-16-le") == "cheap"
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


async def opted_in_second_admin(app: TestApp, admin_id: int, chat_id: int, *, mode: str) -> int:
    """Link the chat, arm Auto-moderation, and opt a second Admin in at `mode`."""
    await auto_moderation_chat(app, admin_id, chat_id)
    second_id = next(_admin_ids)
    # The second Admin is a real Telegram admin of the chat and opts in
    # through the My alerts screen (§9).
    app.session.script(GetChatMember, member_administrator(user(second_id)))
    other_menu = await started_admin(app, second_id)
    for data in (
        f"chat:{chat_id}",
        f"chat-settings:{chat_id}",
        f"chat-alerts:{chat_id}:",
        f"chat-alerts:{chat_id}:{mode}",
    ):
        await app.feed(private_callback_update(second_id, data, other_menu, language_code="en"))
    return second_id


async def the_appeal(session_maker: async_sessionmaker[AsyncSession]) -> Appeal:
    async with session_maker() as db:
        (appeal,) = (await db.execute(select(Appeal))).scalars().all()
        return appeal


async def appeal_alert_copies(
    session_maker: async_sessionmaker[AsyncSession], appeal_id: int
) -> list[AdminAlert]:
    async with session_maker() as db:
        return list(
            (
                await db.execute(
                    select(AdminAlert)
                    .where(
                        AdminAlert.subject_type == "appeal",
                        AdminAlert.subject_id == appeal_id,
                    )
                    .order_by(AdminAlert.id)
                )
            )
            .scalars()
            .all()
        )


async def filed_appeal(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> tuple[Appeal, list[int], int]:
    """A violation, an appeal on it, the alert copies' ids, the notice's id."""
    await spam_message(app, chat_id, member_id, message_id=77)
    notice = app.session.calls_of("SendMessage")[0]
    violation = await the_violation(app.session_maker)
    await appeal_press(app, chat_id, member_id, violation.id, notice.result.message_id)
    appeal = await the_appeal(app.session_maker)
    copies = await appeal_alert_copies(app.session_maker, appeal.id)
    return appeal, [copy.message_id for copy in copies], notice.result.message_id


async def test_approving_lifts_the_restriction_and_shows_the_outcome_everywhere(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    _second_id = await opted_in_second_admin(app, admin_id, chat_id, mode="appeals")
    appeal, copy_ids, notice_message_id = await filed_appeal(app, admin_id, member_id, chat_id)
    assert len(copy_ids) == 2  # the Linker (All) and the second Admin (Appeals only)
    app.session.calls.clear()

    # The Linker presses 🟢 Lift restriction on their copy.
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", permissions=DEFAULT_PERMISSIONS))
    await app.feed(
        private_callback_update(
            admin_id,
            AppealDecideCallback(chat_id=chat_id, appeal_id=appeal.id, approve=True).pack(),
            copy_ids[0],
            language_code="en",
            username="alpha",
        )
    )

    # Default permissions back, the False Positive recorded, the notice and
    # every copy show the outcome, buttons gone (§6, §8).
    assert app.session.call_names() == [
        "GetChat",
        "RestrictChatMember",
        "EditMessageText",  # the notice
        "EditMessageText",  # the Linker's copy
        "EditMessageText",  # the second Admin's copy
        "AnswerCallbackQuery",
    ]
    (restrict,) = app.session.calls_of("RestrictChatMember")
    assert (restrict.method.chat_id, restrict.method.user_id) == (chat_id, member_id)
    assert restrict.method.permissions.can_send_messages is True
    assert restrict.method.until_date == 0

    notice_edit, first_edit, second_edit = app.session.calls_of("EditMessageText")
    assert (notice_edit.method.chat_id, notice_edit.method.message_id) == (
        chat_id,
        notice_message_id,
    )
    assert notice_edit.method.text == "✅ Restriction lifted by an admin"
    for edit in (first_edit, second_edit):
        assert edit.method.reply_markup is None
    assert first_edit.method.text == "✅ Restriction lifted by @alpha"
    assert second_edit.method.text == "✅ Restriction lifted by @alpha"

    decided = await the_appeal(app.session_maker)
    assert decided.status == "approved"
    assert decided.decided_by == admin_id
    assert decided.decided_at is not None
    violation = await the_violation(app.session_maker)
    assert violation.revoked_by == admin_id  # a False Positive now (§6)
    # The notice is scheduled for removal after outcome_visible_s (§7, §8).
    stored_notice = await fresh_notice(app.session_maker, violation.id)
    assert stored_notice is not None
    assert stored_notice.delete_at == FIXED_NOW + timedelta(seconds=600)


async def test_rejecting_keeps_the_restriction_and_shows_the_outcome_everywhere(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    appeal, copy_ids, _notice_message_id = await filed_appeal(app, admin_id, member_id, chat_id)
    app.session.calls.clear()

    await app.feed(
        private_callback_update(
            admin_id,
            AppealDecideCallback(chat_id=chat_id, appeal_id=appeal.id, approve=False).pack(),
            copy_ids[0],
            language_code="en",
            username="alpha",
        )
    )

    # No lifting: no getChat, no restrict. The notice and the copy show the
    # rejection (§8).
    assert app.session.call_names() == [
        "EditMessageText",  # the notice
        "EditMessageText",  # the Linker's copy
        "AnswerCallbackQuery",
    ]
    notice_edit, copy_edit = app.session.calls_of("EditMessageText")
    assert notice_edit.method.chat_id == chat_id
    assert notice_edit.method.text == "❌ Appeal rejected"
    assert copy_edit.method.text == "❌ Appeal rejected by @alpha"
    assert copy_edit.method.reply_markup is None

    decided = await the_appeal(app.session_maker)
    assert decided.status == "rejected"
    assert decided.decided_by == admin_id
    violation = await the_violation(app.session_maker)
    assert violation.revoked_at is None  # the Restriction stands (§8)
    stored_notice = await fresh_notice(app.session_maker, violation.id)
    assert stored_notice is not None
    assert stored_notice.delete_at == FIXED_NOW + timedelta(seconds=600)


async def test_a_late_decision_click_learns_who_was_first(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    second_id = await opted_in_second_admin(app, admin_id, chat_id, mode="appeals")
    appeal, copy_ids, _notice_message_id = await filed_appeal(app, admin_id, member_id, chat_id)
    # The first click approves and lifts, so the chat's default permissions are read.
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", permissions=DEFAULT_PERMISSIONS))
    await app.feed(
        private_callback_update(
            admin_id,
            AppealDecideCallback(chat_id=chat_id, appeal_id=appeal.id, approve=True).pack(),
            copy_ids[0],
            language_code="en",
            username="alpha",
        )
    )
    app.session.calls.clear()

    # The second Admin presses 🔴 Reject on the already-approved appeal.
    app.session.script(GetChatMember, member_owner(user(admin_id, username="alpha")))
    await app.feed(
        private_callback_update(
            second_id,
            AppealDecideCallback(chat_id=chat_id, appeal_id=appeal.id, approve=False).pack(),
            copy_ids[1],
            language_code="en",
            username="beta",
        )
    )

    assert app.session.call_names() == ["GetChatMember", "AnswerCallbackQuery"]
    (answer,) = app.session.calls_of("AnswerCallbackQuery")
    assert answer.method.text == "Already decided by @alpha"
    decided = await the_appeal(app.session_maker)
    assert decided.status == "approved"  # the first click's record stands
    assert decided.decided_by == admin_id


async def test_an_appeal_after_the_purge_says_the_text_is_no_longer_stored(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    await spam_message(app, chat_id, member_id, message_id=77)
    notice = app.session.calls_of("SendMessage")[0]
    violation = await the_violation(app.session_maker)

    # The retention passes while the notice is still up: the stored text goes.
    async with app.session_maker() as db:
        flagged = await db.get(FlaggedMessage, violation.check_id)
        assert flagged is not None
        flagged.purge_at = FIXED_NOW + timedelta(seconds=1)
        await db.commit()
    app.clock.advance(timedelta(minutes=1))
    await Scheduler(
        bot=app.bot, session_maker=app.session_maker, clock=app.clock, core=app.i18n.core
    ).run_once()
    app.session.calls.clear()

    await appeal_press(app, chat_id, member_id, violation.id, notice.result.message_id)

    (alert,) = app.session.calls_of("SendMessage")
    text = alert.method.text or ""
    assert "The message text is no longer stored." in text  # the purged case (§8)
    assert SPAM_TEXT not in text
    assert not [entity for entity in alert.method.entities or [] if entity.type == "blockquote"]


async def russian_chat(app: TestApp, admin_id: int, chat_id: int) -> int:
    """A chat linked by a Russian-speaking Linker: its Chat Language is ru (§12)."""
    await app.feed(start_update(admin_id, "ru"))
    menu = app.session.calls_of("SendMessage")[0].result.message_id
    await app.feed(
        private_callback_update(admin_id, "menu:set-language:ru", menu, language_code="ru")
    )
    await app.feed(private_callback_update(admin_id, "menu:add-to-chat:", menu, language_code="ru"))
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title="Мой чат")
    )
    app.session.script(GetChatMember, member_owner(user(admin_id)))  # the chat screen's re-check
    for data in (f"chat:{chat_id}", f"chat-settings:{chat_id}", f"chat-mode:{chat_id}"):
        await app.feed(private_callback_update(admin_id, data, menu, language_code="ru"))
    app.session.calls.clear()
    return menu


async def test_the_appeal_flow_speaks_the_chat_language(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await russian_chat(app, admin_id, chat_id)
    await spam_message(app, chat_id, member_id, message_id=77)
    notice = app.session.calls_of("SendMessage")[0]
    violation = await the_violation(app.session_maker)

    # The button follows the Chat Language (§7, §15).
    (row,) = notice.method.reply_markup.inline_keyboard
    assert row[0].text == "🙋 Это ошибка"

    # A bystander's toast speaks the Chat Language too (§15).
    await app.feed(
        group_callback_update(
            member_id + 1,
            AppealCallback(chat_id=chat_id, violation_id=violation.id).pack(),
            chat_id=chat_id,
            message_id=notice.result.message_id,
            sender_name="Посторонний",
        )
    )
    (answer,) = app.session.calls_of("AnswerCallbackQuery")
    assert answer.method.text == "Эта кнопка не для вас"

    # So does the pending text on the notice (§8).
    app.session.calls.clear()
    await appeal_press(app, chat_id, member_id, violation.id, notice.result.message_id)
    (text_edit,) = app.session.calls_of("EditMessageText")
    assert text_edit.method.text == "⏳ Апелляция отправлена администраторам"


async def test_an_appeal_on_a_lifted_violation_is_refused_with_a_toast(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    await spam_message(app, chat_id, member_id, message_id=77)
    notice = app.session.calls_of("SendMessage")[0]
    violation = await the_violation(app.session_maker)

    # An Admin lifts via the Violation alert: no appeal was filed (§6, §9).
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", permissions=DEFAULT_PERMISSIONS))
    await app.feed(
        private_callback_update(
            admin_id,
            LiftRestrictionCallback(chat_id=chat_id, violation_id=violation.id).pack(),
            app.session.calls_of("SendMessage")[1].result.message_id,
            language_code="en",
            username="alpha",
        )
    )
    app.session.calls.clear()

    # The member presses the still-standing notice's button: check 3 of §8.
    await appeal_press(app, chat_id, member_id, violation.id, notice.result.message_id)

    assert app.session.call_names() == ["AnswerCallbackQuery"]
    (answer,) = app.session.calls_of("AnswerCallbackQuery")
    assert answer.method.text == "The restriction has already been lifted"
    async with app.session_maker() as db:
        assert (await db.execute(select(Appeal))).scalars().all() == []
