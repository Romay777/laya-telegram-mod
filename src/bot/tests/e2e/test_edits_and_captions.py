"""End-to-end: full moderation coverage (§4, issue #15).

Edits are re-checked as fresh messages; captions are checked; the remaining
Exempt Senders are skipped; the §3 signals move the thresholds through the
Member row; foreign channels are banned. Assertions only look at the
recorded Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import MessageCheck, Suspicion, Violation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.backend import CLEAN, SPAMMY
from tests.support.harness import FIXED_NOW, TestApp, app_fixture, auto_moderation_chat
from tests.support.telegram import member_member
from tests.support.updates import (
    group_edited_message_update,
    group_message_update,
    user,
)

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(1800, 10)
_chat_ids = count(-100600, -10)

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


async def the_checks(
    session_maker: async_sessionmaker[AsyncSession],
) -> list[MessageCheck]:
    async with session_maker() as db:
        return list((await db.execute(select(MessageCheck))).scalars().all())


async def test_hi_edited_into_an_ad_is_caught(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """The edit is checked again from scratch: delete, Violation, Restriction (§4)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_member(user(member_id)))  # not an Admin
    await app.feed(
        group_message_update(chat_id, member_id, "hi", message_id=100, sender_name="Sneaky")
    )
    assert app.backend.calls == []  # "hi" was skipped as short
    app.backend.script(SPAMMY)
    app.session.calls.clear()

    await app.feed(
        group_edited_message_update(
            chat_id,
            member_id,
            SPAM_TEXT,
            message_id=100,
            edit_date=FIXED_NOW,
        )
    )

    # §6 order on the edited message: delete → restrict → notice → alerts.
    assert app.session.call_names() == [
        "DeleteMessage",
        "RestrictChatMember",
        "SendMessage",
        "SendMessage",
    ]
    (delete,) = app.session.calls_of("DeleteMessage")
    assert (delete.method.chat_id, delete.method.message_id) == (chat_id, 100)

    checks = await the_checks(app.session_maker)
    assert [check.outcome for check in checks] == ["skipped_short", "violation"]
    assert checks[1].is_edit is True  # the re-check is marked as an edit (§12)
    async with app.session_maker() as db:
        (violation,) = (await db.execute(select(Violation))).scalars().all()
    assert violation.user_id == member_id


async def test_an_edit_older_than_48_hours_is_skipped(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """Telegram no longer allows deleting old messages, so the edit is skipped (§4 step 2)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(
        group_message_update(chat_id, member_id, "hi", message_id=101, sender_name="Sneaky")
    )
    app.session.calls.clear()
    app.backend.script(SPAMMY)

    await app.feed(
        group_edited_message_update(
            chat_id,
            member_id,
            SPAM_TEXT,
            message_id=101,
            date=FIXED_NOW - timedelta(days=3),
            edit_date=FIXED_NOW,
        )
    )

    assert app.session.call_names() == []  # not even the admin cache asked
    assert app.backend.calls == []  # the classifier never ran
    checks = await the_checks(app.session_maker)
    assert [check.outcome for check in checks] == ["skipped_short"]  # no new row


async def test_an_open_suspicion_is_not_alerted_again_on_edit(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """The message already has an open Suspicion: a new one is not re-alerted (§4)."""
    from tests.support.backend import Probabilities

    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_member(user(member_id)))
    mid = Probabilities({"spam": 0.70, "ads": 0.10, "insult": 0.10, "clean": 0.10})
    app.backend.script(mid)
    await app.feed(
        group_message_update(chat_id, member_id, SPAM_TEXT, message_id=102, sender_name="Sneaky")
    )
    (alert,) = app.session.calls_of("SendMessage")
    app.session.calls.clear()
    async with app.session_maker() as db:
        (suspicion,) = (await db.execute(select(Suspicion))).scalars().all()

    # The same message edited into another mid-zone phrasing.
    await app.feed(
        group_edited_message_update(
            chat_id, member_id, SPAM_TEXT + " edited", message_id=102, edit_date=FIXED_NOW
        )
    )

    assert app.session.calls_of("SendMessage") == []  # no second Suspicion alert
    checks = await the_checks(app.session_maker)
    assert len(checks) == 2
    assert checks[1].outcome == "suspicion"  # the edit was checked...
    async with app.session_maker() as db:
        suspicions = (await db.execute(select(Suspicion))).scalars().all()
    assert len(suspicions) == 1  # ...but no second Suspicion row was opened
    assert suspicions[0].id == suspicion.id


async def test_a_new_violation_closes_the_open_suspicion_as_superseded(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """The edit turned worse: the Violation closes the open Suspicion (§4)."""
    from tests.support.backend import Probabilities

    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_member(user(member_id)))
    mid = Probabilities({"spam": 0.70, "ads": 0.10, "insult": 0.10, "clean": 0.10})
    app.backend.script(mid)
    await app.feed(
        group_message_update(chat_id, member_id, SPAM_TEXT, message_id=103, sender_name="Sneaky")
    )
    app.session.calls.clear()
    async with app.session_maker() as db:
        (suspicion,) = (await db.execute(select(Suspicion))).scalars().all()

    app.backend.script(SPAMMY)
    await app.feed(
        group_edited_message_update(
            chat_id, member_id, SPAM_TEXT + "!!", message_id=103, edit_date=FIXED_NOW
        )
    )

    assert app.session.call_names() == [
        "DeleteMessage",
        "RestrictChatMember",
        "SendMessage",
        "SendMessage",
    ]
    async with app.session_maker() as db:
        decided = await db.get(Suspicion, suspicion.id)
    assert decided is not None
    assert decided.status == "superseded"  # the §12 status finally used (§4)
    assert decided.decided_by is None  # nobody decided; the Violation did


async def test_a_clean_message_is_checked_exactly_once_more_than_its_original(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """Edits of ordinary messages are checked like any other (§4)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(CLEAN)
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(
        group_message_update(
            chat_id, member_id, "Hello there friends", message_id=104, sender_name="Member"
        )
    )
    app.session.calls.clear()

    await app.feed(
        group_edited_message_update(
            chat_id,
            member_id,
            "Hello there friends, edited",
            message_id=104,
            edit_date=FIXED_NOW,
        )
    )

    assert app.session.call_names() == []  # the admin re-check is warm from the original
    checks = await the_checks(app.session_maker)
    assert [check.is_edit for check in checks] == [False, True]
    assert all(check.outcome == "clean" for check in checks)
