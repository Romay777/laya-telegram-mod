"""End-to-end: the Minimum Length screen (§4 step 4, §13).

The Settings screen leads to the length presets; the current choice wears
the primary style, and picking one stores it for the chat. Assertions only
look at the recorded Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from app.db.models import Chat

from tests.support.harness import TestApp, app_fixture, auto_moderation_chat
from tests.support.updates import private_callback_update

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(4700, 10)
_chat_ids = count(-100950, -10)


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


async def open_min_chars_screen(app: TestApp, admin_id: int, chat_id: int, menu_message_id: int):
    await app.feed(
        private_callback_update(admin_id, f"chat-min-chars:{chat_id}:", menu_message_id, "en")
    )
    return app.session.calls_of("EditMessageText")[-1].method


async def test_the_settings_screen_leads_to_the_minimum_length(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)

    await app.feed(private_callback_update(admin_id, f"chat-settings:{chat_id}", menu, "en"))
    edit = app.session.calls_of("EditMessageText")[-1].method
    (min_chars_button,) = [
        button
        for row in edit.reply_markup.inline_keyboard
        for button in row
        if button.callback_data and button.callback_data.startswith("chat-min-chars:")
    ]
    assert min_chars_button.callback_data == f"chat-min-chars:{chat_id}:"
    assert min_chars_button.text == "Minimum length"

    # The screen offers the presets; the linked default of 10 wears primary.
    edit = await open_min_chars_screen(app, admin_id, chat_id, menu)
    rows = edit.reply_markup.inline_keyboard
    assert [row[0].text for row in rows[:-1]] == ["Off", "5", "10", "20", "40"]
    assert rows[2][0].style is not None
    assert rows[-1][0].text == "Back"


async def test_picking_a_minimum_stores_it(app: TestApp, admin_id: int, chat_id: int) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)

    await app.feed(private_callback_update(admin_id, f"chat-min-chars:{chat_id}:0", menu, "en"))
    edit = app.session.calls_of("EditMessageText")[-1].method
    (off,) = edit.reply_markup.inline_keyboard[0]
    assert off.text == "Off"
    assert off.style is not None  # the chat's current choice
    assert "Off checks every message" in (edit.text or "")

    async with app.session_maker() as db:
        chat = await db.get(Chat, chat_id)
        assert chat is not None
        assert chat.min_chars == 0


async def test_a_value_no_preset_offers_is_refused(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    """A stale or hand-crafted pick stores nothing (§13, like the Backend's)."""
    menu = await auto_moderation_chat(app, admin_id, chat_id)

    await app.feed(private_callback_update(admin_id, f"chat-min-chars:{chat_id}:9999", menu, "en"))

    edit = app.session.calls_of("EditMessageText")[-1].method
    assert edit.reply_markup.inline_keyboard[2][0].style is not None  # the stored 10
    async with app.session_maker() as db:
        chat = await db.get(Chat, chat_id)
        assert chat is not None
        assert chat.min_chars == 10
