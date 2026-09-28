"""End-to-end: Suspicions and Observation Mode (§4, §9, ADR-0003).

A flagged message stays in the chat; Admins decide over 🔴 Punish and
Dismiss on private alert copies. Assertions only look at the recorded Bot
API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import FlaggedMessage, MessageCheck, Suspicion
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import (
    FIXED_NOW,
    TestApp,
    app_fixture,
    auto_moderation_chat,
    linked_via_deeplink,
)
from tests.support.telegram import member_member, member_owner
from tests.support.updates import group_message_update, user

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
