"""End-to-end: captions are checked; bare media is skipped (§4 step 3, issue #15).

An ad hidden in a photo caption is caught exactly like text; a photo with
no caption has nothing to check. Caption entities feed `state.urls`.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from aiogram.types import MessageEntity
from app.db.models import MessageCheck, Violation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.backend import CLEAN
from tests.support.harness import FIXED_NOW, TestApp, app_fixture, auto_moderation_chat
from tests.support.telegram import member_member
from tests.support.updates import group_caption_update, group_media_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(1900, 10)
_chat_ids = count(-100700, -10)

SPAM_CAPTION = "Cheapest followers, DM me https://t.me/+abc"


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


async def test_an_ad_in_a_photo_caption_is_caught(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """The caption is the message's text: delete, Violation, Restriction (§4)."""
    from tests.support.backend import SPAMMY

    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(SPAMMY)
    app.session.script(GetChatMember, member_member(user(member_id)))  # not an Admin
    app.session.calls.clear()

    await app.feed(
        group_caption_update(
            chat_id, member_id, SPAM_CAPTION, message_id=110, sender_name="Spammer"
        )
    )

    # §6 order on the captioned message: the admin cache asks, then
    # delete → restrict → notice → alerts (§6).
    assert app.session.call_names() == [
        "GetChatMember",
        "DeleteMessage",
        "RestrictChatMember",
        "SendMessage",
        "SendMessage",
    ]
    (delete,) = app.session.calls_of("DeleteMessage")
    assert (delete.method.chat_id, delete.method.message_id) == (chat_id, 110)

    (check,) = await the_checks(app.session_maker)
    assert check.outcome == "violation"
    assert check.is_edit is False
    async with app.session_maker() as db:
        (violation,) = (await db.execute(select(Violation))).scalars().all()
    assert violation.user_id == member_id


async def test_caption_urls_reach_the_state(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """`caption_entities` feed `state.urls` like `entities` do (§4 step 3, §5)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(CLEAN)
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.calls.clear()

    await app.feed(
        group_caption_update(
            chat_id,
            member_id,
            "look here https://example.com",
            message_id=111,
            caption_entities=[MessageEntity(type="url", offset=10, length=19)],
        )
    )

    assert app.session.call_names() == ["GetChatMember"]  # checked, nothing acted on
    (check,) = await the_checks(app.session_maker)
    assert check.outcome == "clean"
    state = app.backend.calls[0]
    assert state["urls"] == ["https://example.com"]
    assert state["message"] == "look here https://example.com"


async def test_a_bare_photo_with_no_caption_is_skipped(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """No text and no caption: nothing to check, no row at all (§4 step 3).

    The group router only handles messages carrying text or a caption, so
    the bare photo never even reaches the pipeline.
    """
    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_member(user(member_id)))

    await app.feed(group_media_update(chat_id, member_id, message_id=112))

    assert app.session.calls == []
    assert app.backend.calls == []  # the classifier never ran
    assert await the_checks(app.session_maker) == []


async def test_an_edited_caption_is_rechecked_as_an_edit(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """Caption edits go through the same edit path as text edits (§4)."""
    from tests.support.backend import SPAMMY
    from tests.support.updates import group_edited_message_update

    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(group_media_update(chat_id, member_id, message_id=113))
    await app.feed(
        group_caption_update(
            chat_id, member_id, "an innocent caption", message_id=113, sender_name="Sneaky"
        )
    )
    app.backend.script(SPAMMY)
    app.session.calls.clear()

    await app.feed(
        group_edited_message_update(
            chat_id,
            member_id,
            SPAM_CAPTION,
            message_id=113,
            edit_date=FIXED_NOW + timedelta(seconds=1),
        )
    )

    # The admin re-check is warm from the original caption; the edit runs
    # the §6 order — delete → restrict → notice → alerts.
    assert app.session.call_names() == [
        "DeleteMessage",
        "RestrictChatMember",
        "SendMessage",
        "SendMessage",
    ]
    checks = await the_checks(app.session_maker)
    assert [check.outcome for check in checks] == ["clean", "violation"]
    assert checks[1].is_edit is True
