"""End-to-end: the remaining Exempt Senders (§4 step 1, issue #15).

Anonymous admins (the chat speaking as itself), automatic forwards from
the linked channel, and posts from the chat's linked channel are never
checked. A foreign channel — any other sender_chat — is checked like any
other message (its ban is test_channels.py's).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChat, GetChatMember
from app.db.models import MessageCheck
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.backend import SPAMMY
from tests.support.harness import TestApp, app_fixture, auto_moderation_chat
from tests.support.telegram import chat_facts, member_member
from tests.support.updates import (
    anonymous_admin_message_update,
    linked_channel_forward_update,
    user,
)

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(2000, 10)
_chat_ids = count(-100800, -10)
_channel_ids = count(-1009000, -10)

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
def linked_channel_id() -> Iterator[int]:
    yield next(_channel_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        yield app


async def all_checks(
    session_maker: async_sessionmaker[AsyncSession],
) -> list[MessageCheck]:
    async with session_maker() as db:
        return list((await db.execute(select(MessageCheck))).scalars().all())


async def test_an_anonymous_admin_is_exempt(app: TestApp, admin_id: int, chat_id: int) -> None:
    """`sender_chat` is the chat itself: never checked, no Telegram call (§4)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(SPAMMY)

    await app.feed(anonymous_admin_message_update(chat_id, SPAM_TEXT, message_id=120))

    assert app.session.calls == []  # not even the admin cache asked
    assert app.backend.calls == []
    assert await all_checks(app.session_maker) == []


async def test_an_automatic_forward_from_the_linked_channel_is_exempt(
    app: TestApp, admin_id: int, chat_id: int, linked_channel_id: int
) -> None:
    """`is_automatic_forward` is decided from the update alone (§4 step 1)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(SPAMMY)

    await app.feed(
        linked_channel_forward_update(chat_id, linked_channel_id, SPAM_TEXT, message_id=121)
    )

    assert app.session.calls == []
    assert app.backend.calls == []
    assert await all_checks(app.session_maker) == []


async def test_a_post_from_the_linked_channel_is_exempt(
    app: TestApp, admin_id: int, chat_id: int, linked_channel_id: int
) -> None:
    """A `sender_chat` matching the chat's linked channel is skipped (§4 step 1)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", linked_chat_id=linked_channel_id))
    app.backend.script(SPAMMY)

    from tests.support.updates import foreign_channel_message_update

    await app.feed(
        foreign_channel_message_update(chat_id, linked_channel_id, SPAM_TEXT, message_id=122)
    )

    # One getChat to learn the linked channel; the post itself was skipped.
    assert app.session.call_names() == ["GetChat"]
    assert app.backend.calls == []
    assert await all_checks(app.session_maker) == []


async def test_a_foreign_channel_is_checked_like_any_other_message(
    app: TestApp, admin_id: int, chat_id: int, linked_channel_id: int
) -> None:
    """A `sender_chat` that is neither the chat nor its linked channel is checked (§4)."""
    foreign_id = linked_channel_id - 1  # a channel that is not the chat's own
    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", linked_chat_id=linked_channel_id))
    app.backend.script(SPAMMY)

    from tests.support.updates import foreign_channel_message_update

    await app.feed(foreign_channel_message_update(chat_id, foreign_id, SPAM_TEXT, message_id=123))

    checks = await all_checks(app.session_maker)
    assert len(checks) == 1
    assert checks[0].outcome == "violation"
    assert checks[0].user_id == foreign_id  # the check is attributed to the channel


async def test_the_linked_channel_lookup_is_cached_per_request(
    app: TestApp, admin_id: int, member_id: int, chat_id: int, linked_channel_id: int
) -> None:
    """A plain Member's message costs no getChat at all (the lookup is lazy)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.calls.clear()

    from tests.support.updates import group_message_update

    await app.feed(
        group_message_update(
            chat_id, member_id, "Hello there friends", message_id=124, sender_name="Member"
        )
    )

    assert app.session.call_names() == ["GetChatMember"]
    assert app.session.calls_of("GetChat") == []
