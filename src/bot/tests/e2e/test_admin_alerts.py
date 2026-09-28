"""End-to-end: Violation Admin Alerts and lifting a Restriction (§6, §9).

A Violation fans out a private Admin Alert to every Admin whose mode is
All — chat, Member, Category, confidence, Step, the quoted message, one
🟢 Lift restriction button. The first click lifts: default permissions
back, the Violation becomes a False Positive, every copy is edited. A
late click only learns who was first. And a False Positive no longer
counts, so the Member's next Violation starts the ladder over.
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChat, GetChatMember
from aiogram.types import ChatPermissions
from app.db.models import AdminAlert, Violation
from app.menu.callbacks import LiftRestrictionCallback
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import TestApp, app_fixture, auto_moderation_chat, started_admin
from tests.support.telegram import chat_facts, member_administrator, member_member, member_owner
from tests.support.updates import group_message_update, private_callback_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(2000, 10)
_member_ids = count(2900, 5)
_chat_ids = count(-100800, -10)

SPAMMY = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}
SPAM_TEXT = "Buy cheap crypto now, DM me https://t.me/+abc"
DEFAULT_PERMISSIONS = ChatPermissions(can_send_messages=True, can_send_polls=True)


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def second_admin_id() -> Iterator[int]:
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


async def opted_in_second_admin(app: TestApp, admin_id: int, chat_id: int) -> int:
    """Link the chat, arm Auto-moderation, and opt a second Admin into All."""
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
        f"chat-alerts:{chat_id}:all",
    ):
        await app.feed(private_callback_update(second_id, data, other_menu, language_code="en"))
    return second_id


async def spam_message(app: TestApp, chat_id: int, member_id: int, message_id: int) -> None:
    app.backend.script(SPAMMY)
    app.session.script(GetChatMember, member_member(user(member_id)))  # the sender
    app.session.calls.clear()
    await app.feed(
        group_message_update(
            chat_id, member_id, SPAM_TEXT, message_id=message_id, sender_name="Spammer"
        )
    )


async def the_violation(session_maker: async_sessionmaker[AsyncSession]) -> Violation:
    async with session_maker() as db:
        (violation,) = (await db.execute(select(Violation))).scalars().all()
        return violation


async def alert_copies(
    session_maker: async_sessionmaker[AsyncSession], violation_id: int
) -> list[AdminAlert]:
    async with session_maker() as db:
        rows = (
            (
                await db.execute(
                    select(AdminAlert)
                    .where(AdminAlert.subject_id == violation_id)
                    .order_by(AdminAlert.id)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def test_a_violation_alerts_both_subscribed_admins(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    second_id = await opted_in_second_admin(app, admin_id, chat_id)
    await spam_message(app, chat_id, member_id, message_id=77)

    sends = app.session.calls_of("SendMessage")
    assert [call.method.chat_id for call in sends] == [chat_id, admin_id, second_id]
    notice, first, second = sends

    for alert in (first, second):
        text = alert.method.text or ""
        assert "⚠️ My Chat" in text  # the chat (§9)
        assert "Member: Spammer" in text  # the Member (§9)
        assert "Spam, 97%" in text  # the Category and the confidence (§9)
        assert "Restriction: 1 hour" in text  # the Step (§9)
        assert SPAM_TEXT in text  # the deleted message, quoted (§9)
        entities = alert.method.entities or []
        assert any(entity.type == "blockquote" for entity in entities)
        (button,) = alert.method.reply_markup.inline_keyboard[0]
        assert button.text == "🟢 Lift restriction"

    violation = await the_violation(app.session_maker)
    copies = await alert_copies(app.session_maker, violation.id)
    assert [(copy.admin_id, copy.message_id) for copy in copies] == [
        (admin_id, first.result.message_id),
        (second_id, second.result.message_id),
    ]
    assert all(copy.subject_type == "violation" for copy in copies)
    assert notice.method.text.startswith("Spammer,")  # the Chat Notice went out too


async def test_the_first_click_lifts_and_the_late_click_learns_nothing_new(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    second_id = await opted_in_second_admin(app, admin_id, chat_id)
    await spam_message(app, chat_id, member_id, message_id=77)
    sends = app.session.calls_of("SendMessage")
    first_copy_id = sends[1].result.message_id
    second_copy_id = sends[2].result.message_id
    violation = await the_violation(app.session_maker)
    lift_data = LiftRestrictionCallback(chat_id=chat_id, violation_id=violation.id).pack()
    app.session.calls.clear()

    # The Linker presses 🟢 Lift restriction on their copy.
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", permissions=DEFAULT_PERMISSIONS))
    await app.feed(
        private_callback_update(
            admin_id, lift_data, first_copy_id, language_code="en", username="alpha"
        )
    )

    # Default permissions back, every copy edited, buttons gone (§6, §9).
    assert app.session.call_names() == [
        "GetChat",
        "RestrictChatMember",
        "EditMessageText",
        "EditMessageText",
        "AnswerCallbackQuery",
    ]
    (restrict,) = app.session.calls_of("RestrictChatMember")
    assert (restrict.method.chat_id, restrict.method.user_id) == (chat_id, member_id)
    assert restrict.method.permissions.can_send_messages is True
    assert restrict.method.permissions.can_send_polls is True
    assert restrict.method.until_date == 0
    edits = app.session.calls_of("EditMessageText")
    assert [(call.method.chat_id, call.method.message_id) for call in edits] == [
        (admin_id, first_copy_id),
        (second_id, second_copy_id),
    ]
    assert all(call.method.text == "✅ Restriction lifted by @alpha" for call in edits)
    assert all(call.method.reply_markup is None for call in edits)
    revoked = await the_violation(app.session_maker)
    assert revoked.revoked_by == admin_id
    assert revoked.revoked_at is not None
    app.session.calls.clear()

    # A concurrent second click decides nothing: first click wins (§9).
    app.session.script(GetChatMember, member_owner(user(admin_id, username="alpha")))
    await app.feed(
        private_callback_update(
            second_id, lift_data, second_copy_id, language_code="en", username="beta"
        )
    )
    assert app.session.call_names() == ["GetChatMember", "AnswerCallbackQuery"]
    (answer,) = app.session.calls_of("AnswerCallbackQuery")
    assert answer.method.text == "Already decided by @alpha"
    still_revoked = await the_violation(app.session_maker)
    assert still_revoked.revoked_by == admin_id  # the first click's record stands


async def test_a_false_positive_no_longer_counts_on_the_next_step(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    second_id = await opted_in_second_admin(app, admin_id, chat_id)
    await spam_message(app, chat_id, member_id, message_id=77)
    first = await the_violation(app.session_maker)
    assert first.step_index == 0  # Step 1 of the default ladder

    first_copy_id = app.session.calls_of("SendMessage")[1].result.message_id
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", permissions=DEFAULT_PERMISSIONS))
    await app.feed(
        private_callback_update(
            admin_id,
            LiftRestrictionCallback(chat_id=chat_id, violation_id=first.id).pack(),
            first_copy_id,
            language_code="en",
            username="alpha",
        )
    )
    app.session.calls.clear()

    # The same Member breaks the rules again while still restricted; the
    # sender's cached not-an-admin answer means no new getChatMember.
    await app.feed(
        group_message_update(chat_id, member_id, SPAM_TEXT, message_id=78, sender_name="Spammer")
    )

    async with app.session_maker() as db:
        violations = (await db.execute(select(Violation).order_by(Violation.id))).scalars().all()
    assert len(violations) == 2
    new = violations[1]
    assert new.step_index == 0  # the False Positive no longer pushes the ladder (§6)
    assert new.restriction_seconds == 3600  # Step 1 again, not Step 2
    assert new.revoked_at is None
    # The new Violation alerted both Admins again; the lifted one stays lifted.
    copies = await alert_copies(app.session_maker, first.id)
    assert {copy.admin_id for copy in copies} == {admin_id, second_id}
    assert {copy.admin_id for copy in await alert_copies(app.session_maker, new.id)} == {
        admin_id,
        second_id,
    }
