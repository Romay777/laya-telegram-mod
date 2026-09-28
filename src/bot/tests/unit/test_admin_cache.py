"""Unit: the Admin-status cache over its public interface (§10).

Admin status comes from `getChatMember` and is remembered for
`admin_cache.ttl_s`; a chat whose answer expired is asked again.
"""

from collections.abc import Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import GetChatMember
from app.clock import FakeClock
from app.linking.admin_cache import AdminCache

from tests.support.fake_session import FakeBotSession
from tests.support.telegram import member_administrator, member_member
from tests.support.updates import user

_user_ids = count(400, 10)


@pytest.fixture
def session() -> FakeBotSession:
    return FakeBotSession()


@pytest.fixture
def bot(session: FakeBotSession) -> Bot:
    return Bot("42:test-token", session=session)


@pytest.fixture
def user_id() -> Iterator[int]:
    yield next(_user_ids)


async def test_a_yes_is_asked_once_and_remembered(
    bot: Bot, session: FakeBotSession, user_id: int
) -> None:
    cache = AdminCache(clock=FakeClock(), ttl_s=300)
    session.script(GetChatMember, member_administrator(user(user_id)))

    assert await cache.is_admin(bot, chat_id=-1001, user_id=user_id)
    assert await cache.is_admin(bot, chat_id=-1001, user_id=user_id)  # from the cache

    (asked,) = session.calls_of("GetChatMember")  # Telegram was asked exactly once
    assert asked.method.chat_id == -1001
    assert asked.method.user_id == user_id


async def test_the_answer_expires_after_the_ttl(
    bot: Bot, session: FakeBotSession, user_id: int
) -> None:
    clock = FakeClock()
    cache = AdminCache(clock=clock, ttl_s=300)
    session.script(GetChatMember, member_administrator(user(user_id)))
    assert await cache.is_admin(bot, -1001, user_id)

    clock.advance(timedelta(seconds=301))  # the cached answer is stale now
    session.script(GetChatMember, member_member(user(user_id)))
    assert not await cache.is_admin(bot, -1001, user_id)

    assert len(session.calls_of("GetChatMember")) == 2  # asked again inside the TTL


async def test_a_no_is_remembered_too(bot: Bot, session: FakeBotSession, user_id: int) -> None:
    """Home filters every Linked Chat through the cache; a stranger's no counts too."""
    cache = AdminCache(clock=FakeClock(), ttl_s=300)
    session.script(GetChatMember, member_member(user(user_id)))

    assert not await cache.is_admin(bot, -1001, user_id)
    assert not await cache.is_admin(bot, -1001, user_id)

    (_asked,) = session.calls_of("GetChatMember")


async def test_a_telegram_error_is_not_cached(
    bot: Bot, session: FakeBotSession, user_id: int
) -> None:
    """A failed check is no answer: the next interaction asks again."""
    cache = AdminCache(clock=FakeClock(), ttl_s=300)
    session.script(GetChatMember, TelegramAPIError(method=None, message="chat not found"))
    assert not await cache.is_admin(bot, -1001, user_id)

    session.script(GetChatMember, member_administrator(user(user_id)))
    assert await cache.is_admin(bot, -1001, user_id)

    assert len(session.calls_of("GetChatMember")) == 2


async def test_answers_are_per_chat_and_per_user(
    bot: Bot, session: FakeBotSession, user_id: int
) -> None:
    cache = AdminCache(clock=FakeClock(), ttl_s=300)
    session.script(GetChatMember, member_administrator(user(user_id)))
    assert await cache.is_admin(bot, -1001, user_id)

    session.script(GetChatMember, member_member(user(user_id)))
    assert not await cache.is_admin(bot, -1002, user_id)  # another chat asks for itself
    session.script(GetChatMember, member_member(user(user_id + 1)))
    assert not await cache.is_admin(bot, -1001, user_id + 1)  # so does another user

    # The first answer is still the cached one.
    assert await cache.is_admin(bot, -1001, user_id)
    assert len(session.calls_of("GetChatMember")) == 3
