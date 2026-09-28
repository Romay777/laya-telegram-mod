"""End-to-end: the three chat-settings screens (§13, §15).

Categories, Sensitivity and Chat Language are toggled from the Settings
screen of the Chat screen, and each pick changes how moderation behaves:
which Categories produce Verdicts, which thresholds sort them, and which
language the chat's own texts speak. The Admin's own interface language is
independent. Access to every screen is re-checked like every chat-scoped
callback (§13).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import Chat, ChatCategory
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import TestApp, app_fixture, linked_via_deeplink, started_admin
from tests.support.telegram import member_member, member_owner
from tests.support.updates import private_callback_update, user

# The Postgres container is shared, so every test gets its own Admin and chat.
_admin_ids = count(1900, 10)
_chat_ids = count(-100900, -10)


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def stranger_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        yield app


async def stored_chat(session_maker: async_sessionmaker[AsyncSession], chat_id: int) -> Chat:
    async with session_maker() as db:
        chat = await db.get(Chat, chat_id)
        assert chat is not None
        return chat


async def open_settings(app: TestApp, admin_id: int, chat_id: int, menu: int) -> None:
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        private_callback_update(admin_id, f"chat-settings:{chat_id}", menu, language_code="en")
    )


def last_edit(app: TestApp):
    return app.session.calls_of("EditMessageText")[-1].method


async def test_settings_carries_the_three_new_entries_and_they_open(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)

    keyboard = last_edit(app).reply_markup.inline_keyboard
    callbacks = [button.callback_data for row in keyboard for button in row]
    assert f"chat-categories:{chat_id}:" in callbacks
    assert f"chat-sensitivity:{chat_id}:" in callbacks
    assert f"chat-language:{chat_id}:" in callbacks
    app.session.calls.clear()

    # Categories lists the three toggleable ones; enabled wears primary (§13).
    await app.feed(
        private_callback_update(admin_id, f"chat-categories:{chat_id}:", menu, language_code="en")
    )
    buttons = {
        button.text: button for row in last_edit(app).reply_markup.inline_keyboard for button in row
    }
    assert set(buttons) >= {"Spam", "Advertising", "Insults"}
    assert all(buttons[name].style == "primary" for name in ("Spam", "Advertising", "Insults"))
    app.session.calls.clear()

    # Sensitivity marks Balanced, the default (§12), with the primary style.
    await app.feed(
        private_callback_update(admin_id, f"chat-sensitivity:{chat_id}:", menu, language_code="en")
    )
    buttons = {
        button.text: button for row in last_edit(app).reply_markup.inline_keyboard for button in row
    }
    assert {"Lenient", "Balanced", "Strict"} <= set(buttons)
    assert buttons["Balanced"].style == "primary"
    app.session.calls.clear()

    # Chat Language marks the Linker's language, the default (§12).
    await app.feed(
        private_callback_update(admin_id, f"chat-language:{chat_id}:", menu, language_code="en")
    )
    buttons = {
        button.text: button for row in last_edit(app).reply_markup.inline_keyboard for button in row
    }
    assert buttons["🇬🇧 English"].style == "primary"
    assert buttons["🇷🇺 Русский"].style is None


async def test_category_toggles_persist_and_flip(app: TestApp, admin_id: int, chat_id: int) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    app.session.calls.clear()

    await app.feed(
        private_callback_update(
            admin_id, f"chat-categories:{chat_id}:ads", menu, language_code="en"
        )
    )

    # Advertising went off: its button loses the primary mark, the rest keep it.
    buttons = {
        button.text: button for row in last_edit(app).reply_markup.inline_keyboard for button in row
    }
    assert buttons["Advertising"].style is None
    assert buttons["Spam"].style == "primary"
    assert buttons["Insults"].style == "primary"
    async with app.session_maker() as db:
        row = await db.get(ChatCategory, (chat_id, "ads"))
    assert row is not None and row.enabled is False
    app.session.calls.clear()

    # Tapping it again puts it back.
    await app.feed(
        private_callback_update(
            admin_id, f"chat-categories:{chat_id}:ads", menu, language_code="en"
        )
    )
    buttons = {
        button.text: button for row in last_edit(app).reply_markup.inline_keyboard for button in row
    }
    assert buttons["Advertising"].style == "primary"


async def test_sensitivity_picks_persist(app: TestApp, admin_id: int, chat_id: int) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    app.session.calls.clear()

    await app.feed(
        private_callback_update(
            admin_id, f"chat-sensitivity:{chat_id}:strict", menu, language_code="en"
        )
    )

    buttons = {
        button.text: button for row in last_edit(app).reply_markup.inline_keyboard for button in row
    }
    assert buttons["Strict"].style == "primary"
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat.sensitivity == "strict"
    app.session.calls.clear()

    # The Chat screen status line shows the new Sensitivity (§13).
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(private_callback_update(admin_id, f"chat:{chat_id}", menu, language_code="en"))
    assert "Sensitivity: Strict" in (last_edit(app).text or "")


async def test_chat_language_picks_persist(app: TestApp, admin_id: int, chat_id: int) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    await open_settings(app, admin_id, chat_id, menu)
    app.session.calls.clear()

    await app.feed(
        private_callback_update(admin_id, f"chat-language:{chat_id}:ru", menu, language_code="en")
    )

    buttons = {
        button.text: button for row in last_edit(app).reply_markup.inline_keyboard for button in row
    }
    assert buttons["🇷🇺 Русский"].style == "primary"
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat.chat_language == "ru"
    app.session.calls.clear()

    # The Admin's own interface stays English: the Menu still reads English (§15).
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        private_callback_update(admin_id, f"chat-language:{chat_id}:", menu, language_code="en")
    )
    assert "The language of this chat's notices" in (last_edit(app).text or "")


async def test_a_non_admin_cannot_change_the_settings(
    app: TestApp, admin_id: int, stranger_id: int, chat_id: int
) -> None:
    await linked_via_deeplink(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_member(user(stranger_id)))  # the Home render
    stranger_menu = await started_admin(app, stranger_id)

    for data in (
        f"chat-categories:{chat_id}:ads",
        f"chat-sensitivity:{chat_id}:strict",
        f"chat-language:{chat_id}:ru",
    ):
        await app.feed(
            private_callback_update(stranger_id, data, stranger_menu, language_code="en")
        )
        answer = app.session.calls_of("AnswerCallbackQuery")[-1].method
        assert answer.text == "You are no longer an admin of this chat."

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat.sensitivity == "balanced"
    assert chat.chat_language == "en"
