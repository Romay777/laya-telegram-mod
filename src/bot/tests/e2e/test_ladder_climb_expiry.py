"""End-to-end: three Violations climb the ladder, Expiry starts it over (§6).

A Member's Violations in an Auto-moderation chat take Steps 1h, 1d, forever;
after the clock moves past the 30-day Expiry of every recorded Violation,
the next Violation lands on Step 1 again. Assertions only look at the
recorded Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import Violation
from sqlalchemy import select

from tests.support.harness import FIXED_NOW, TestApp, app_fixture, auto_moderation_chat
from tests.support.telegram import member_member
from tests.support.updates import group_message_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(2600, 10)
_member_ids = count(2650, 10)
_chat_ids = count(-1001400, -10)

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


async def violate(app: TestApp, chat_id: int, member_id: int, message_id: int) -> None:
    """One violating message from the Member, seen as a non-Admin."""
    app.backend.script(SPAMMY)
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(group_message_update(chat_id, member_id, SPAM_TEXT, message_id=message_id))


async def violations(app: TestApp) -> list[Violation]:
    async with app.session_maker() as db:
        return list((await db.execute(select(Violation).order_by(Violation.id))).scalars())


async def test_three_violations_climb_one_hour_one_day_forever_then_expiry_restarts(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.calls.clear()

    await violate(app, chat_id, member_id, message_id=101)
    await violate(app, chat_id, member_id, message_id=102)
    await violate(app, chat_id, member_id, message_id=103)

    restricts = app.session.calls_of("RestrictChatMember")
    assert len(restricts) == 3
    untils = [call.method.until_date for call in restricts]
    assert untils[0] == int((FIXED_NOW + timedelta(hours=1)).timestamp())  # Step 1: 1 hour
    assert untils[1] == int((FIXED_NOW + timedelta(days=1)).timestamp())  # Step 2: 1 day
    assert untils[2] == 0  # Step 3: forever

    rows = await violations(app)
    assert [(row.step_index, row.restriction_seconds) for row in rows] == [
        (0, 3600),
        (1, 86400),
        (2, 0),
    ]
    # Each Violation expires 30 days after it was recorded (§6).
    assert rows[0].expires_at - rows[0].created_at == timedelta(days=30)
    assert rows[2].expires_at is not None and rows[2].expires_at > rows[0].expires_at
    app.session.calls.clear()

    # The clock moves past the 30-day Expiry of every recorded Violation.
    app.clock.advance(timedelta(days=31))
    await violate(app, chat_id, member_id, message_id=104)

    (restart,) = app.session.calls_of("RestrictChatMember")
    # Step 1 again: one hour from the Violation the clock now stands at.
    # Exactly one RestrictChatMember — Expiry lifted no earlier Restriction (§6).
    assert restart.method.until_date == int((app.clock.now() + timedelta(hours=1)).timestamp())
    rows = await violations(app)
    assert (rows[-1].step_index, rows[-1].restriction_seconds) == (0, 3600)
