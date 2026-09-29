"""End-to-end: `chat_member` updates invalidate the cached admin list (§4 step 1).

A promotion exempts at once, inside the TTL; a demotion puts the ex-admin
back into the pipeline without waiting for `admin_cache.ttl_s`.
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import MessageCheck
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.backend import CLEAN
from tests.support.harness import TestApp, app_fixture, auto_moderation_chat
from tests.support.telegram import member_administrator, member_member
from tests.support.updates import chat_member_update, group_message_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(2300, 10)
_chat_ids = count(-100870, -10)

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


async def all_checks(
    session_maker: async_sessionmaker[AsyncSession],
) -> list[MessageCheck]:
    async with session_maker() as db:
        return list((await db.execute(select(MessageCheck))).scalars().all())


async def test_a_promotion_exempts_immediately_inside_the_ttl(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    # The Member is checked first: a plain member today.
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(
        group_message_update(
            chat_id, member_id, "Hello there friends", message_id=150, sender_name="Upcoming"
        )
    )
    assert len(await all_checks(app.session_maker)) == 1
    app.session.calls.clear()
    app.backend.script(CLEAN)

    # Telegram promotes them; the update arrives while the cache still holds
    # the old "member" answer (§4 step 1: the cache is invalidated).
    await app.feed(chat_member_update(chat_id, member_id, status="administrator"))
    # The next message asks Telegram afresh — the stale "member" answer is gone.
    app.session.script(GetChatMember, member_administrator(user(member_id)))
    await app.feed(
        group_message_update(
            chat_id, member_id, "Hello there friends again", message_id=151, sender_name="Admin now"
        )
    )

    # One fresh ask, and the promoted admin is exempt (§4).
    assert app.session.call_names() == ["GetChatMember"]
    checks = await all_checks(app.session_maker)
    assert len(checks) == 1  # no check row for the promoted admin's message


async def test_a_demotion_is_checked_again_immediately(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_administrator(user(member_id)))
    await app.feed(
        group_message_update(
            chat_id, member_id, "Hello there friends", message_id=152, sender_name="Admin"
        )
    )
    assert await all_checks(app.session_maker) == []  # exempt as an admin
    app.session.calls.clear()
    app.backend.script(CLEAN)

    await app.feed(chat_member_update(chat_id, member_id, status="member"))
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(
        group_message_update(chat_id, member_id, "Hello there friends again", message_id=153)
    )

    # Telegram was asked again — the cached "yes" did not stand (§4 step 1).
    assert app.session.call_names() == ["GetChatMember"]
    checks = await all_checks(app.session_maker)
    assert len(checks) == 1
    assert checks[0].outcome == "clean"


async def test_an_unrelated_chat_member_update_invalidates_only_its_own_pair(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """A status change of user X does not forget user Y's cached answer (§4)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    other_id = member_id + 1
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(group_message_update(chat_id, member_id, "Hello there friends", message_id=154))
    app.session.calls.clear()

    # Someone else's status changed; this member's cached answer must stand.
    await app.feed(chat_member_update(chat_id, other_id, status="administrator"))
    await app.feed(
        group_message_update(chat_id, member_id, "Hello there friends again", message_id=155)
    )

    assert app.session.calls == []  # still served from the cache
    checks = await all_checks(app.session_maker)
    assert len(checks) == 2  # the second message was checked like the first
