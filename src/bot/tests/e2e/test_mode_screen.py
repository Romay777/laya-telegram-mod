"""End-to-end: the Mode screen (§13) — Settings switches Observation ↔ Auto.

The switch is what arms the moderation pipeline (ticket #6): in Observation
Mode nothing is acted on yet. Access to the switch is re-checked like every
chat-scoped callback (§13).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import Chat
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import TestApp, app_fixture, linked_via_deeplink, started_admin
from tests.support.telegram import member_member, member_owner
from tests.support.updates import private_callback_update, user

# The Postgres container is shared, so every test gets its own Admin and chat.
_admin_ids = count(1500, 10)
_chat_ids = count(-100500, -10)


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


async def stored_chat(session_maker: async_sessionmaker[AsyncSession], chat_id: int) -> Chat | None:
    async with session_maker() as db:
        return await db.get(Chat, chat_id)


async def test_the_chat_screen_leads_to_the_mode_switch(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(private_callback_update(admin_id, f"chat:{chat_id}", menu, language_code="en"))

    edit = app.session.calls_of("EditMessageText")[-1].method
    (back,), (settings,) = edit.reply_markup.inline_keyboard
    assert back.callback_data == "menu:home:"
    assert settings.callback_data == f"chat-settings:{chat_id}"
    app.session.calls.clear()

    # Settings opens the Mode screen, and the chat stays in Observation Mode.
    await app.feed(
        private_callback_update(admin_id, f"chat-settings:{chat_id}", menu, language_code="en")
    )
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "My Chat" in (edit.text or "")
    assert "Mode: Observation Mode" in (edit.text or "")
    (
        (enable_auto,),
        (categories,),
        (_backend,),
        (sensitivity,),
        (ladder,),
        (language,),
        (template,),
        (my_alerts,),
        (back_to_chat,),
    ) = edit.reply_markup.inline_keyboard
    assert categories.callback_data == f"chat-categories:{chat_id}:"  # §13: Categories
    assert sensitivity.callback_data == f"chat-sensitivity:{chat_id}:"  # §13: Sensitivity
    assert ladder.callback_data == f"chat-ladder:{chat_id}"  # §13: Penalty Ladder
    assert language.callback_data == f"chat-language:{chat_id}:"  # §13: Chat Language
    assert template.callback_data == f"chat-template:{chat_id}"  # §13, §14: Notice Template
    assert my_alerts.callback_data == f"chat-alerts:{chat_id}:"  # §13: My alerts
    assert enable_auto.text == "🟢 Enable auto-moderation"
    assert enable_auto.callback_data == f"chat-mode:{chat_id}"
    assert back_to_chat.callback_data == f"chat:{chat_id}"
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.mode == "observation"
    app.session.calls.clear()

    # The switch arms Auto-moderation, and the screen offers the way back.
    await app.feed(
        private_callback_update(admin_id, f"chat-mode:{chat_id}", menu, language_code="en")
    )
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "Mode: Auto-moderation" in (edit.text or "")
    (observe,), *_ = edit.reply_markup.inline_keyboard
    assert observe.text == "Switch to observation"
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.mode == "auto"
    app.session.calls.clear()

    await app.feed(
        private_callback_update(admin_id, f"chat-mode:{chat_id}", menu, language_code="en")
    )
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.mode == "observation"


async def test_a_non_admin_cannot_touch_the_mode_switch(
    app: TestApp, admin_id: int, stranger_id: int, chat_id: int
) -> None:
    await linked_via_deeplink(app, admin_id, chat_id)
    # A stranger starts the bot and taps the Mode switch of a chat they don't run.
    app.session.script(GetChatMember, member_member(user(stranger_id)))  # the Home render
    stranger_menu = await started_admin(app, stranger_id)

    await app.feed(
        private_callback_update(
            stranger_id, f"chat-mode:{chat_id}", stranger_menu, language_code="en"
        )
    )

    # A toast explains the dead end, the bot goes back to Home, nothing changed.
    answer = app.session.calls_of("AnswerCallbackQuery")[-1].method
    assert answer.text == "You are no longer an admin of this chat."
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert edit.text == "Menu"  # the Home screen
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.mode == "observation"
