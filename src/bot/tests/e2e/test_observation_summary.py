"""End-to-end: the Auto-moderation choice and the Observation summary (§13).

Right after Linking the Menu offers the choice: 🟢 Enable auto-moderation
now, or 🔵 Observe for 2 days first — which schedules the one summary the
Linker gets at `observation_summary_at` (§11).
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import Chat
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import FIXED_NOW, TestApp, app_fixture, started_admin
from tests.support.telegram import member_owner
from tests.support.updates import my_chat_member_update, private_callback_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(1750, 10)
_chat_ids = count(-100700, -10)


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


async def linked_menu(app: TestApp, admin_id: int, chat_id: int) -> int:
    """The Admin started, pressed Add to chat, and the chat was promoted."""
    menu_message_id = await started_admin(app, admin_id)
    await app.feed(
        private_callback_update(admin_id, "menu:add-to-chat:", menu_message_id, language_code="en")
    )
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title="My Chat")
    )
    return menu_message_id


async def stored_chat(session_maker: async_sessionmaker[AsyncSession], chat_id: int) -> Chat | None:
    async with session_maker() as db:
        return await db.get(Chat, chat_id)


async def test_after_linking_the_menu_offers_the_auto_moderation_choice(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_menu(app, admin_id, chat_id)

    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "✅ My Chat linked" in (edit.text or "")
    (enable,), (observe,), (back,) = edit.reply_markup.inline_keyboard
    assert enable.text == "🟢 Enable auto-moderation now"
    assert enable.callback_data == f"enable-auto:{chat_id}"
    assert enable.style == "success"  # §13: Enable is the confirming action
    assert observe.text == "🔵 Observe for 2 days first"
    assert observe.callback_data == f"observe:{chat_id}"
    assert observe.style == "primary"  # §13: the screen's main action
    assert back.callback_data == "menu:home:"

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.mode == "observation"
    assert chat.observation_summary_at is None  # nothing scheduled before the choice


async def test_observing_for_two_days_schedules_the_summary(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_menu(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))

    await app.feed(
        private_callback_update(admin_id, f"observe:{chat_id}", menu, language_code="en")
    )

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.mode == "observation"
    assert chat.observation_summary_at == FIXED_NOW + timedelta(hours=48)
    # The Menu moves on to the chat's own screen.
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "Mode: Observation Mode" in (edit.text or "")
    assert edit.reply_markup.inline_keyboard != []  # a regular screen again


async def test_enabling_auto_moderation_now_arms_the_chat(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_menu(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))

    await app.feed(
        private_callback_update(admin_id, f"enable-auto:{chat_id}", menu, language_code="en")
    )

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.mode == "auto"
    assert chat.observation_summary_at is None  # no summary was ever scheduled
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "Mode: Auto-moderation" in (edit.text or "")
