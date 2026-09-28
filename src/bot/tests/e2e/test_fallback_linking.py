"""End-to-end: the fallback Linking path (§10) for a bot that was added by hand.

The Admin taps "I added the bot already" and names the chat with an @username,
a numeric id or a message forwarded from it. The same checks as the deep-link
path run, with the sender treated as the person linking; assertions only look
at the recorded Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from app.db.models import Chat
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import TestApp, app_fixture
from tests.support.updates import private_callback_update, start_update

# The Postgres container is shared, so every test gets its own Admin and chat.
_admin_ids = count(1100, 10)
_chat_ids = count(-100500, -10)


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        yield app


async def started_admin(app: TestApp, admin_id: int) -> int:
    """/start with the language picked; returns the Admin's Menu message id."""
    await app.feed(start_update(admin_id, "en"))
    menu_message_id = app.session.calls_of("SendMessage")[0].result.message_id
    await app.feed(
        private_callback_update(
            admin_id, "menu:set-language:en", menu_message_id, language_code="en"
        )
    )
    return menu_message_id


async def on_add_chat_screen(app: TestApp, admin_id: int) -> int:
    """The Admin opened the Add a chat screen; returns the Menu message id."""
    menu_message_id = await started_admin(app, admin_id)
    await app.feed(
        private_callback_update(admin_id, "menu:add-to-chat:", menu_message_id, language_code="en")
    )
    app.session.calls.clear()
    return menu_message_id


async def stored_chat(session_maker: async_sessionmaker[AsyncSession], chat_id: int) -> Chat | None:
    async with session_maker() as db:
        return await db.get(Chat, chat_id)


async def test_the_added_already_button_asks_for_the_chat_identifier(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu_message_id = await on_add_chat_screen(app, admin_id)

    await app.feed(
        private_callback_update(
            admin_id, "menu:added-already:", menu_message_id, language_code="en"
        )
    )

    edit = app.session.calls_of("EditMessageText")[-1].method
    assert edit.message_id == menu_message_id  # the same self-editing Menu message
    text = edit.text or ""
    assert "@username" in text  # the prompt names all three accepted inputs
    assert "-100" in text
    assert "forward" in text
    (back,) = edit.reply_markup.inline_keyboard
    assert back[0].callback_data == "menu:home:"  # leaving the screen cancels the wait

    assert await stored_chat(app.session_maker, chat_id) is None
