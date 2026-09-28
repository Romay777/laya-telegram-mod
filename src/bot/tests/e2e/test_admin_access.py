"""End-to-end: Admin access (§10) — who sees which chat on Home, and who may open it.

Any current administrator or creator of a Linked Chat can open and manage it;
status comes from `getChatMember`, cached for `admin_cache.ttl_s`. Every
callback that refers to a chat re-checks access.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import Chat
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import TestApp, app_fixture
from tests.support.telegram import member_administrator, member_member, member_owner
from tests.support.updates import (
    my_chat_member_update,
    private_callback_update,
    start_update,
    user,
)

# The Postgres container is shared, so every test gets its own Admins and chats.
_admin_ids = count(1200, 10)
_chat_ids = count(-100700, -10)


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def second_admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
def second_chat_id() -> Iterator[int]:
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


async def linked_via_deeplink(
    app: TestApp, admin_id: int, chat_id: int, title: str = "My Chat"
) -> int:
    """Add to chat → promotion; returns the Admin's Menu message id."""
    menu_message_id = await started_admin(app, admin_id)
    await app.feed(
        private_callback_update(admin_id, "menu:add-to-chat:", menu_message_id, language_code="en")
    )
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title=title))
    app.session.calls.clear()
    return menu_message_id


async def stored_chat(session_maker: async_sessionmaker[AsyncSession], chat_id: int) -> Chat | None:
    async with session_maker() as db:
        return await db.get(Chat, chat_id)


async def test_home_lists_every_linked_chat_the_user_administers(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu_message_id = await linked_via_deeplink(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))  # still an admin

    await app.feed(
        private_callback_update(admin_id, "menu:home:", menu_message_id, language_code="en")
    )

    # The chat is the first thing on Home, one button per chat (§13).
    edit = app.session.calls_of("EditMessageText")[-1].method
    (chat_row,) = edit.reply_markup.inline_keyboard[0]
    assert chat_row.text == "My Chat"
    assert chat_row.callback_data == f"chat:{chat_id}"  # callback data carries the chat_id
    (add_to_chat,), (language,), (how_it_works,) = edit.reply_markup.inline_keyboard[1:]
    assert add_to_chat.text == "🔵 Add to chat"
    assert language.callback_data == "menu:language:"
    assert how_it_works.callback_data == "menu:how-it-works:"


async def test_opening_the_chat_shows_its_screen_without_asking_again(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    """The Home render just asked Telegram; the cached answer covers the open."""
    menu_message_id = await linked_via_deeplink(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        private_callback_update(admin_id, "menu:home:", menu_message_id, language_code="en")
    )
    app.session.calls.clear()

    await app.feed(
        private_callback_update(admin_id, f"chat:{chat_id}", menu_message_id, language_code="en")
    )

    assert app.session.calls_of("GetChatMember") == []  # the cached answer counts
    edit = app.session.calls_of("EditMessageText")[-1].method
    text = (edit.text or "").splitlines()
    assert text[0] == "My Chat"  # the Chat screen: what was linked and with what
    assert "Mode: Observation Mode" in text
    assert "Backend: Laya" in text
    assert "Sensitivity: Balanced" in text
    (back,) = edit.reply_markup.inline_keyboard[0]
    assert back.callback_data == "menu:home:"


async def test_a_co_admin_who_is_not_the_linker_can_manage_the_chat(
    app: TestApp, admin_id: int, second_admin_id: int, chat_id: int
) -> None:
    """Access is a Telegram fact: another current administrator sees and opens the chat."""
    await linked_via_deeplink(app, admin_id, chat_id)

    # A co-admin starts the bot; nobody subscribed them anywhere (§9).
    # Picking the language renders Home, which is where the chat is first checked.
    app.session.script(GetChatMember, member_administrator(user(second_admin_id)))
    co_menu = await started_admin(app, second_admin_id)

    await app.feed(
        private_callback_update(second_admin_id, "menu:home:", co_menu, language_code="en")
    )

    edit = app.session.calls_of("EditMessageText")[-1].method
    (chat_row,) = edit.reply_markup.inline_keyboard[0]
    assert chat_row.text == "My Chat"  # the co-admin's Home lists it like the Linker's
    assert chat_row.callback_data == f"chat:{chat_id}"
    app.session.calls.clear()

    await app.feed(
        private_callback_update(second_admin_id, f"chat:{chat_id}", co_menu, language_code="en")
    )

    # The Home render just asked Telegram; the cached answer covers the open.
    assert app.session.calls_of("GetChatMember") == []
    edit = app.session.calls_of("EditMessageText")[-1].method
    text = (edit.text or "").splitlines()
    assert text[0] == "My Chat"  # the co-admin manages the same Chat screen
    assert "Mode: Observation Mode" in text


async def test_a_demoted_admin_loses_access_once_the_cache_has_expired(
    app: TestApp, admin_id: int, second_admin_id: int, chat_id: int
) -> None:
    """The cached answer keeps them in for ttl_s; the next check shuts the door (§10)."""
    await linked_via_deeplink(app, admin_id, chat_id)
    # Picking the language renders Home, which is where the chat is first checked.
    app.session.script(GetChatMember, member_administrator(user(second_admin_id)))
    co_menu = await started_admin(app, second_admin_id)
    await app.feed(
        private_callback_update(second_admin_id, "menu:home:", co_menu, language_code="en")
    )

    # The co-admin is demoted while their cached answer is still fresh.
    app.clock.advance(timedelta(seconds=100))
    await app.feed(
        private_callback_update(second_admin_id, f"chat:{chat_id}", co_menu, language_code="en")
    )
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert (edit.text or "").splitlines()[0] == "My Chat"  # still inside the ttl_s window
    app.session.calls.clear()

    # Past admin_cache.ttl_s the answer is stale: the check runs again.
    app.clock.advance(timedelta(seconds=301))
    app.session.script(GetChatMember, member_member(user(second_admin_id)))
    await app.feed(
        private_callback_update(second_admin_id, f"chat:{chat_id}", co_menu, language_code="en")
    )

    # A toast explains the dead end, and the bot is sent back to Home.
    answer = app.session.calls_of("AnswerCallbackQuery")[-1].method
    assert answer.text == "You are no longer an admin of this chat."
    assert answer.show_alert is False  # a toast, not an alert
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert edit.text == "Menu"  # the Home screen
    assert edit.reply_markup.inline_keyboard[0][0].callback_data != f"chat:{chat_id}"


async def test_home_hides_chats_the_user_is_no_longer_an_admin_of(
    app: TestApp, admin_id: int, chat_id: int, second_chat_id: int
) -> None:
    menu_message_id = await linked_via_deeplink(app, admin_id, chat_id, title="First")
    app.clock.advance(timedelta(seconds=1))  # the second chat links a moment later
    await app.feed(
        private_callback_update(admin_id, "menu:add-to-chat:", menu_message_id, language_code="en")
    )
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        my_chat_member_update(second_chat_id, "supergroup", linker_id=admin_id, title="Second")
    )
    app.session.calls.clear()

    # The bot was demoted in the first chat; the second is untouched.
    app.session.script(GetChatMember, member_member(user(admin_id)))  # First
    app.session.script(GetChatMember, member_owner(user(admin_id)))  # Second
    await app.feed(
        private_callback_update(admin_id, "menu:home:", menu_message_id, language_code="en")
    )

    edit = app.session.calls_of("EditMessageText")[-1].method
    rows = edit.reply_markup.inline_keyboard
    (only_chat,) = rows[0]
    assert only_chat.text == "Second"
    assert only_chat.callback_data == f"chat:{second_chat_id}"
    assert all(row[0].callback_data != f"chat:{chat_id}" for row in rows)
