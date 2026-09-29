"""End-to-end: the §3 signals move the thresholds (issue #15, §4 step 5).

A new Member's message carrying an invite link is judged against a lowered
threshold — a confidence that would only be a Suspicion from an ordinary
Member becomes a full Violation. Assertions only look at recorded calls
and DB state (§17); the pure shift rules have their own unit tests.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import Member, MessageCheck
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import FIXED_NOW, TestApp, app_fixture, auto_moderation_chat
from tests.support.telegram import member_member
from tests.support.updates import group_message_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(2100, 10)
_chat_ids = count(-100850, -10)

# Below the Balanced violation threshold (0.90) but above the shifted one
# (0.90 - 0.10 - 0.05 = 0.75): only the signals make it a Violation.
NEAR_MISS = {"spam": 0.80, "ads": 0.05, "insult": 0.05, "clean": 0.10}
INVITE_TEXT = "Join our channel https://t.me/+abc123 now"


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


async def test_a_new_member_with_an_invite_link_is_caught_at_lower_confidence(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """New Member + invite link: -0.10 - 0.05 turns 0.80 into a Violation (§3)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(NEAR_MISS)
    app.session.script(GetChatMember, member_member(user(member_id)))  # not an Admin
    app.session.calls.clear()

    await app.feed(
        group_message_update(chat_id, member_id, INVITE_TEXT, message_id=130, sender_name="Fresh")
    )

    # A full Violation: delete → restrict → notice → alerts (§6).
    assert app.session.call_names() == [
        "GetChatMember",
        "DeleteMessage",
        "RestrictChatMember",
        "SendMessage",
        "SendMessage",
    ]
    (check,) = await the_checks(app.session_maker)
    assert check.outcome == "violation"  # 0.80 ≥ the shifted 0.75
    assert check.confidence == pytest.approx(0.80)
    # The Member row was created by this first check, flagged (§12).
    async with app.session_maker() as db:
        (member,) = (await db.execute(select(Member))).scalars().all()
    assert (member.checked_count, member.flagged_count) == (1, 1)
    assert member.first_seen_at == FIXED_NOW


async def test_the_same_message_from_an_ordinary_member_is_only_a_suspicion(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """Without the new-member signal, 0.80 stays inside the middle band (§3)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(NEAR_MISS)
    app.session.script(GetChatMember, member_member(user(member_id)))
    # The Member already has history: 5 checked messages over 3 days, no flags.
    async with app.session_maker() as db:
        db.add(
            Member(
                chat_id=chat_id,
                user_id=member_id,
                first_seen_at=FIXED_NOW - timedelta(days=3),
                checked_count=5,
                flagged_count=0,
            )
        )
        await db.commit()
    app.session.calls.clear()

    await app.feed(
        group_message_update(chat_id, member_id, INVITE_TEXT, message_id=131, sender_name="Regular")
    )

    # The invite-link signal (-0.05) still lowers the threshold to 0.85 —
    # but 0.80 is below that, so only the Suspicion zone catches it.
    assert app.session.call_names() == ["GetChatMember", "SendMessage"]
    assert app.session.calls_of("DeleteMessage") == []
    (check,) = await the_checks(app.session_maker)
    assert check.outcome == "suspicion"


async def test_configured_shifts_reach_the_pipeline(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """The shift values come from config (§3): a bigger shift catches even lower."""
    from tests.support.harness import build_app

    # A config shift of -0.30 for the new-member signal: 0.90 - 0.30 - 0.05
    # = 0.55, and 0.60 crosses it where the defaults would not.
    app2 = await build_app(
        app.engine.url.render_as_string(hide_password=False),
        shifts={"new_member_link": -0.30, "invite_link": -0.05, "established_member": 0.05},
    )
    try:
        await auto_moderation_chat(app2, admin_id, chat_id)
        low = {"spam": 0.60, "ads": 0.10, "insult": 0.10, "clean": 0.20}
        app2.backend.script(low)
        app2.session.script(GetChatMember, member_member(user(member_id)))
        app2.session.calls.clear()

        await app2.feed(
            group_message_update(
                chat_id, member_id, INVITE_TEXT, message_id=132, sender_name="Fresh"
            )
        )

        assert app2.session.calls_of("DeleteMessage") != []  # a full Violation
        checks = await the_checks(app2.session_maker)
        assert checks[0].outcome == "violation"
    finally:
        await app2.aclose()


async def test_a_checked_history_ages_a_member_out_of_new(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """The new-member bound is either age or checked messages (§3)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(NEAR_MISS)
    app.session.script(GetChatMember, member_member(user(member_id)))
    # 3 checked messages over 2 days: neither §3 new-member bound holds.
    async with app.session_maker() as db:
        db.add(
            Member(
                chat_id=chat_id,
                user_id=member_id,
                first_seen_at=FIXED_NOW - timedelta(days=2),
                checked_count=3,
                flagged_count=0,
            )
        )
        await db.commit()
    app.session.calls.clear()

    await app.feed(
        group_message_update(chat_id, member_id, INVITE_TEXT, message_id=133, sender_name="Regular")
    )

    (check,) = await the_checks(app.session_maker)
    assert check.outcome == "suspicion"  # only the invite shift (-0.05) applies
