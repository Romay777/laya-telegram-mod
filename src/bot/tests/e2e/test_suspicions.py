"""End-to-end: Suspicions and Observation Mode (§4, §9, ADR-0003).

A flagged message stays in the chat; Admins decide over 🔴 Punish and
Dismiss on private alert copies. Assertions only look at the recorded Bot
API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import ChatNotice, FlaggedMessage, MessageCheck, Suspicion, Violation
from app.menu.callbacks import SuspicionDecideCallback
from app.scheduler import Scheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import (
    FIXED_NOW,
    TestApp,
    app_fixture,
    auto_moderation_chat,
    linked_via_deeplink,
    started_admin,
)
from tests.support.telegram import member_administrator, member_member, member_owner
from tests.support.updates import group_message_update, private_callback_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(1700, 10)
_chat_ids = count(-100550, -10)

# Above the Balanced violation threshold (0.90), right in the suspicion zone.
SPAMMY_MID = {"spam": 0.70, "ads": 0.10, "insult": 0.10, "clean": 0.10}
# Far above it: a Violation in Auto-moderation, but still a Suspicion in
# Observation Mode (ADR-0003).
SPAMMY_HIGH = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}
SPAM_TEXT = "Buy cheap crypto now, DM me https://t.me/+abc"


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


async def test_in_observation_mode_a_high_confidence_hit_is_a_suspicion(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """Nothing is removed automatically: the Linker decides (ADR-0003)."""
    await linked_via_deeplink(app, admin_id, chat_id)  # every chat starts in Observation Mode
    app.backend.script(SPAMMY_HIGH)
    # The Member is not an Admin; the Linker (the alert recipient) is one.
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    app.session.calls.clear()

    await app.feed(
        group_message_update(chat_id, member_id, SPAM_TEXT, message_id=81, sender_name="Spammer")
    )

    # No deletion, no Restriction, no Chat Notice: one private alert only.
    # GetChatMember ran for the Member (not an Admin) and for the Linker,
    # the alert recipient.
    assert app.session.call_names() == ["GetChatMember", "GetChatMember", "SendMessage"]
    (alert,) = app.session.calls_of("SendMessage")
    assert alert.method.chat_id == admin_id
    text = alert.method.text or ""
    assert "My Chat" in text
    assert "Spammer" in text
    assert SPAM_TEXT in text  # the quoted message (§9)
    assert f"https://t.me/c/{str(chat_id).removeprefix('-100')}/81" in text  # a link to it
    # The link is clickable: a text_link entity over the URL line.
    link = next(e for e in (alert.method.entities or []) if getattr(e, "type", None) == "text_link")
    assert link.url == f"https://t.me/c/{str(chat_id).removeprefix('-100')}/81"
    (punish, dismiss) = alert.method.reply_markup.inline_keyboard[0]
    assert punish.text == "🔴 Punish"
    assert punish.style == "danger"
    assert dismiss.text == "Dismiss"
    for button in (punish, dismiss):
        assert button.callback_data.startswith(f"suspicion-decide:{chat_id}:")

    # DB state: the check row in the suspicion zone, the kept text, the row.
    async with app.session_maker() as db:
        checks = (await db.execute(select(MessageCheck))).scalars().all()
        assert len(checks) == 1
        check = checks[0]
        suspicions = (await db.execute(select(Suspicion))).scalars().all()
        assert len(suspicions) == 1
        suspicion = suspicions[0]
    assert check.outcome == "suspicion"  # even at 0.97: Observation Mode (§4)
    assert (check.category, check.confidence) == ("spam", pytest.approx(0.97))
    flagged = await db_get(app.session_maker, FlaggedMessage, check.id)
    assert isinstance(flagged, FlaggedMessage)
    assert flagged.text == SPAM_TEXT  # Suspicions store their text too (§4 step 9)
    assert suspicion.status == "pending"
    assert (suspicion.check_id, suspicion.chat_id) == (check.id, chat_id)
    assert (suspicion.user_id, suspicion.message_id) == (member_id, 81)
    assert suspicion.decided_by is None and suspicion.decided_at is None
    assert suspicion.created_at == FIXED_NOW


async def db_get(
    session_maker: async_sessionmaker[AsyncSession], model: type, key: object
) -> object:
    """Re-read one row in a fresh session (the pipeline wrote and committed)."""
    async with session_maker() as db:
        return await db.get(model, key)


async def test_in_auto_moderation_the_middle_band_is_a_suspicion(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """A mid-confidence Verdict becomes a Suspicion: the message stays (§4)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(SPAMMY_MID)
    app.session.script(GetChatMember, member_member(user(member_id)))  # not an Admin
    app.session.calls.clear()

    await app.feed(
        group_message_update(chat_id, member_id, SPAM_TEXT, message_id=82, sender_name="Spammer")
    )

    # The Member keeps writing: no delete, no Restriction, no Chat Notice.
    assert app.session.call_names() == ["GetChatMember", "SendMessage"]
    (alert,) = app.session.calls_of("SendMessage")
    assert alert.method.chat_id == admin_id
    assert SPAM_TEXT in (alert.method.text or "")

    async with app.session_maker() as db:
        checks = (await db.execute(select(MessageCheck))).scalars().all()
        assert len(checks) == 1
        (suspicion,) = (await db.execute(select(Suspicion))).scalars().all()
    assert checks[0].outcome == "suspicion"
    assert checks[0].confidence == pytest.approx(0.70)
    assert suspicion.status == "pending"


# --- Deciding a Suspicion: 🔴 Punish and Dismiss (§9) -----------------------


async def opted_in_second_admin(app: TestApp, chat_id: int) -> int:
    """Opt a second, real Admin of the chat into All alerts (§9)."""
    second_id = next(_admin_ids)
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


async def pending_suspicion_alerts(
    app: TestApp, admin_id: int, member_id: int, chat_id: int, message_id: int
) -> tuple[int, int, int]:
    """A flagged message in Observation Mode; returns the alert copy ids."""
    await linked_via_deeplink(app, admin_id, chat_id)
    second_id = await opted_in_second_admin(app, chat_id)
    app.backend.script(SPAMMY_HIGH)
    app.session.script(GetChatMember, member_member(user(member_id)))  # the sender
    app.session.script(GetChatMember, member_owner(user(admin_id)))  # the Linker
    # The second Admin's alert-recipient check is warm from their opt-in.
    app.session.calls.clear()
    await app.feed(
        group_message_update(
            chat_id, member_id, SPAM_TEXT, message_id=message_id, sender_name="Spammer"
        )
    )
    sends = app.session.calls_of("SendMessage")
    assert [call.method.chat_id for call in sends] == [admin_id, second_id]
    app.session.calls.clear()
    copies = [call.result.message_id for call in sends]
    return copies[0], copies[1], second_id


async def the_suspicion(session_maker: async_sessionmaker[AsyncSession]) -> Suspicion:
    async with session_maker() as db:
        return (await db.execute(select(Suspicion))).scalars().one()


async def the_violations(session_maker: async_sessionmaker[AsyncSession]) -> list[Violation]:
    async with session_maker() as db:
        return list((await db.execute(select(Violation).order_by(Violation.id))).scalars().all())


async def test_punish_applies_the_full_violation_and_every_copy_learns(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    first_copy, second_copy, second_id = await pending_suspicion_alerts(
        app, admin_id, member_id, chat_id, message_id=83
    )
    suspicion = await the_suspicion(app.session_maker)
    punish_data = SuspicionDecideCallback(
        chat_id=chat_id, suspicion_id=suspicion.id, punish=True
    ).pack()

    # The Linker presses 🔴 Punish on their copy.
    app.session.script(GetChatMember, member_member(user(member_id)))  # the notice's {user}
    await app.feed(
        private_callback_update(
            admin_id, punish_data, first_copy, language_code="en", username="alpha"
        )
    )

    # §6 order on the Violation: delete → record → restrict → notice —
    # then every alert copy shows who decided, buttons gone (§9). The §13
    # admin re-check is warm from the alert's fan-out; the one GetChatMember
    # fetches the Member's display name for the Chat Notice.
    assert app.session.call_names() == [
        "DeleteMessage",
        "RestrictChatMember",
        "GetChatMember",
        "SendMessage",  # the Chat Notice
        "EditMessageText",
        "EditMessageText",
        "AnswerCallbackQuery",
    ]
    (delete,) = app.session.calls_of("DeleteMessage")
    assert (delete.method.chat_id, delete.method.message_id) == (chat_id, 83)
    (restrict,) = app.session.calls_of("RestrictChatMember")
    assert (restrict.method.chat_id, restrict.method.user_id) == (chat_id, member_id)
    assert restrict.method.until_date == int((FIXED_NOW + timedelta(hours=1)).timestamp())
    (notice,) = app.session.calls_of("SendMessage")
    assert notice.method.chat_id == chat_id
    # The notice's {user} is the Member's name as Telegram reports it now
    # (the message itself is gone by the time of the Punish).
    assert (notice.method.text or "").startswith("Admin,")
    edits = app.session.calls_of("EditMessageText")
    assert [(call.method.chat_id, call.method.message_id) for call in edits] == [
        (admin_id, first_copy),
        (second_id, second_copy),
    ]
    assert all(call.method.text == "🔴 Punished by @alpha" for call in edits)
    assert all(call.method.reply_markup is None for call in edits)

    decided = await the_suspicion(app.session_maker)
    assert decided.status == "punished"
    assert decided.decided_by == admin_id
    assert decided.decided_at == FIXED_NOW
    # The Suspicion became a full Violation — recorded by an Admin (§9).
    violations = await the_violations(app.session_maker)
    assert len(violations) == 1
    violation = violations[0]
    assert (violation.source, violation.category) == ("admin", "spam")
    assert violation.check_id == decided.check_id
    assert violation.user_id == member_id
    assert violation.restriction_seconds == 3600  # the first Step of the ladder
    notice_row = await db_get(app.session_maker, ChatNotice, violation.id)
    assert isinstance(notice_row, ChatNotice)
    assert notice_row.message_id == notice.result.message_id


async def test_dismiss_closes_the_suspicion_and_edits_the_copies(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    first_copy, second_copy, second_id = await pending_suspicion_alerts(
        app, admin_id, member_id, chat_id, message_id=84
    )
    suspicion = await the_suspicion(app.session_maker)
    dismiss_data = SuspicionDecideCallback(
        chat_id=chat_id, suspicion_id=suspicion.id, punish=False
    ).pack()

    await app.feed(
        private_callback_update(
            admin_id, dismiss_data, first_copy, language_code="en", username="alpha"
        )
    )

    # Nothing happens to the chat: the copies learn, and that is all (§9).
    # The §13 admin re-check is warm from the alert's fan-out.
    assert app.session.call_names() == [
        "EditMessageText",
        "EditMessageText",
        "AnswerCallbackQuery",
    ]
    edits = app.session.calls_of("EditMessageText")
    assert [(call.method.chat_id, call.method.message_id) for call in edits] == [
        (admin_id, first_copy),
        (second_id, second_copy),
    ]
    assert all(call.method.text == "Dismissed by @alpha" for call in edits)
    assert all(call.method.reply_markup is None for call in edits)
    decided = await the_suspicion(app.session_maker)
    assert decided.status == "dismissed"
    assert decided.decided_by == admin_id
    assert await the_violations(app.session_maker) == []  # no Violation, no Restriction


async def test_a_late_click_on_a_decided_suspicion_learns_who_was_first(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    first_copy, second_copy, second_id = await pending_suspicion_alerts(
        app, admin_id, member_id, chat_id, message_id=85
    )
    suspicion = await the_suspicion(app.session_maker)
    punish_data = SuspicionDecideCallback(
        chat_id=chat_id, suspicion_id=suspicion.id, punish=True
    ).pack()

    # The first press only needs the notice's {user}; the second script
    # serves the late click's who-decided lookup.
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.script(GetChatMember, member_owner(user(admin_id, username="alpha")))
    await app.feed(
        private_callback_update(
            admin_id, punish_data, first_copy, language_code="en", username="alpha"
        )
    )
    app.session.calls.clear()

    # The second Admin presses a second later: first click wins (§9).
    await app.feed(
        private_callback_update(
            second_id, punish_data, second_copy, language_code="en", username="beta"
        )
    )
    assert app.session.call_names() == ["GetChatMember", "AnswerCallbackQuery"]
    (answer,) = app.session.calls_of("AnswerCallbackQuery")
    assert answer.method.text == "Already decided by @alpha"
    decided = await the_suspicion(app.session_maker)
    assert decided.decided_by == admin_id  # the first click's record stands
    assert len(await the_violations(app.session_maker)) == 1  # no second Violation


async def test_punishing_a_48_hour_old_message_skips_the_deletion(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    first_copy, _second_copy, _second_id = await pending_suspicion_alerts(
        app, admin_id, member_id, chat_id, message_id=86
    )
    suspicion = await the_suspicion(app.session_maker)
    punish_data = SuspicionDecideCallback(
        chat_id=chat_id, suspicion_id=suspicion.id, punish=True
    ).pack()

    # A day and an hour pass before anybody looks at the alert (§9).
    app.clock.advance(timedelta(hours=49))
    # The admin re-check's cache has expired; the second GetChatMember is
    # the notice's {user} lookup.
    app.session.script(GetChatMember, member_owner(user(admin_id, username="alpha")))
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(
        private_callback_update(
            admin_id, punish_data, first_copy, language_code="en", username="alpha"
        )
    )

    # The Restriction and the Chat Notice still happen; the deletion is off.
    assert app.session.call_names() == [
        "GetChatMember",  # the §13 re-check: the cache forgot over the 49 hours
        "RestrictChatMember",
        "GetChatMember",  # the Member's display name for the Chat Notice
        "SendMessage",
        "EditMessageText",
        "EditMessageText",
        "AnswerCallbackQuery",
    ]
    assert app.session.calls_of("DeleteMessage") == []
    edits = app.session.calls_of("EditMessageText")
    assert edits[0].method.text == (
        "🔴 Punished by @alpha\nThe message is older than 48 hours, so it stays in the chat."
    )
    (violation,) = await the_violations(app.session_maker)
    assert violation.source == "admin"  # the Violation stands either way


# --- The scheduler auto-closes what nobody decided (§11) ---------------------


async def test_pending_suspicions_auto_close_as_expired(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await pending_suspicion_alerts(app, admin_id, member_id, chat_id, message_id=87)
    app.clock.advance(timedelta(hours=24))  # suspicions.auto_close_h

    await Scheduler(
        bot=app.bot, session_maker=app.session_maker, clock=app.clock, core=app.i18n.core
    ).run_once()

    # Nothing is sent to anybody: the Suspicion just closes (§11).
    assert app.session.calls == []
    decided = await the_suspicion(app.session_maker)
    assert decided.status == "expired"
    assert decided.decided_at == FIXED_NOW + timedelta(hours=24)
    assert decided.decided_by is None  # nobody decided


async def test_a_decided_suspicion_is_never_auto_closed(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    first_copy, _second_copy, _second_id = await pending_suspicion_alerts(
        app, admin_id, member_id, chat_id, message_id=88
    )
    suspicion = await the_suspicion(app.session_maker)
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(
        private_callback_update(
            admin_id,
            SuspicionDecideCallback(
                chat_id=chat_id, suspicion_id=suspicion.id, punish=False
            ).pack(),
            first_copy,
            language_code="en",
            username="alpha",
        )
    )
    app.clock.advance(timedelta(days=3))

    await Scheduler(
        bot=app.bot, session_maker=app.session_maker, clock=app.clock, core=app.i18n.core
    ).run_once()

    decided = await the_suspicion(app.session_maker)
    assert decided.status == "dismissed"  # the Admin's decision stands
    assert decided.decided_at == FIXED_NOW  # untouched by the scheduler


async def test_punishing_an_expired_suspicion_only_gets_a_toast(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    first_copy, _second_copy, _second_id = await pending_suspicion_alerts(
        app, admin_id, member_id, chat_id, message_id=89
    )
    suspicion = await the_suspicion(app.session_maker)
    app.clock.advance(timedelta(hours=24))
    await Scheduler(
        bot=app.bot, session_maker=app.session_maker, clock=app.clock, core=app.i18n.core
    ).run_once()
    app.session.calls.clear()
    app.session.script(GetChatMember, member_owner(user(admin_id, username="alpha")))

    await app.feed(
        private_callback_update(
            admin_id,
            SuspicionDecideCallback(chat_id=chat_id, suspicion_id=suspicion.id, punish=True).pack(),
            first_copy,
            language_code="en",
            username="alpha",
        )
    )

    # The alert's buttons are dead: a toast explains, nothing happens (§9).
    assert app.session.call_names() == ["GetChatMember", "AnswerCallbackQuery"]
    (answer,) = app.session.calls_of("AnswerCallbackQuery")
    assert answer.method.text == "This suspicion has already expired."
    decided = await the_suspicion(app.session_maker)
    assert decided.status == "expired"
    assert await the_violations(app.session_maker) == []
