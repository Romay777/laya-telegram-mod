"""End-to-end: the Exempt Sender and short-message filters (§4 steps 1 and 4).

Admins (via the cached admin list) and bots are never checked. Short
messages with no link, invite or @mention are skipped as `skipped_short`.
In Observation Mode messages are still checked and recorded, but nothing
is ever acted on (ADR-0003).
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from aiogram.types import MessageEntity
from app.db.models import MessageCheck
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.backend import CLEAN
from tests.support.harness import TestApp, app_fixture, auto_moderation_chat, linked_via_deeplink
from tests.support.telegram import member_member, member_owner
from tests.support.updates import group_message_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(1700, 10)
_chat_ids = count(-100400, -10)

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


async def all_checks(session_maker: async_sessionmaker[AsyncSession]) -> list[MessageCheck]:
    async with session_maker() as db:
        return list((await db.execute(select(MessageCheck))).scalars().all())


async def test_an_admins_message_is_exempt_and_acts_on_nothing(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    app.clock.advance(timedelta(seconds=301))  # past admin_cache.ttl_s: the cache forgets
    # The Admin of the chat sends what would clearly be spam.
    app.session.script(GetChatMember, member_owner(user(admin_id)))

    await app.feed(
        group_message_update(chat_id, admin_id, SPAM_TEXT, message_id=90, sender_name="Admin")
    )

    assert app.session.call_names() == ["GetChatMember"]  # only the cache's check
    assert app.backend.calls == []  # the classifier never ran
    assert await all_checks(app.session_maker) == []


async def test_a_bots_message_is_exempt_without_even_asking_telegram(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)

    await app.feed(
        group_message_update(
            chat_id, 555, SPAM_TEXT, message_id=91, sender_name="Some Bot", from_bot=True
        )
    )

    assert app.session.calls == []  # no Telegram call at all
    assert app.backend.calls == []
    assert await all_checks(app.session_maker) == []


async def test_a_short_link_free_message_is_skipped_as_skipped_short(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_member(user(member_id)))

    await app.feed(group_message_update(chat_id, member_id, "hi there", message_id=92))

    assert app.session.call_names() == ["GetChatMember"]
    assert app.backend.calls == []  # too short to bother the model (§4 step 4)
    (check,) = await all_checks(app.session_maker)
    assert check.outcome == "skipped_short"
    assert check.category is None


async def test_a_link_keeps_a_short_message_in_the_pipeline(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """Two words, but the invite link means the check runs (§4 step 4)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(CLEAN)
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.calls.clear()

    await app.feed(
        group_message_update(
            chat_id,
            member_id,
            "join us https://t.me/+abc",
            message_id=93,
            # Telegram marks the bare URL with a `url` entity.
            entities=[MessageEntity(type="url", offset=8, length=17)],
        )
    )

    # nothing acted on a clean verdict, but the admin check ran
    assert app.session.call_names() == ["GetChatMember"]
    (check,) = await all_checks(app.session_maker)
    assert check.outcome == "clean"  # it was checked, not skipped
    state = app.backend.calls[0]
    assert state["urls"] == ["https://t.me/+abc"]  # the spec's `state.urls` (§5)
    assert state["message"] == "join us https://t.me/+abc"


async def test_in_observation_mode_a_clean_message_is_recorded_and_left_alone(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await linked_via_deeplink(app, admin_id, chat_id)  # every chat starts observing (§12)
    app.backend.script(CLEAN)
    app.session.script(GetChatMember, member_member(user(member_id)))

    await app.feed(group_message_update(chat_id, member_id, SPAM_TEXT, message_id=94))

    # Checked and recorded, but never acted on: no deletion, no Restriction,
    # no Chat Notice, no alert (ADR-0003; a flagged Verdict would become a
    # Suspicion — that flow is test_suspicions.py's).
    assert app.session.call_names() == ["GetChatMember"]
    (check,) = await all_checks(app.session_maker)
    assert check.outcome == "clean"


async def test_a_message_in_an_unlinked_chat_is_ignored(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    """The same update for a chat the bot never linked never reaches the pipeline."""
    await app.feed(group_message_update(chat_id, admin_id, "hello?", message_id=95))
    assert app.session.calls == []
