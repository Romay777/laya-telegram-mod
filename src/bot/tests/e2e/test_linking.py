"""End-to-end: the primary Linking path via the startgroup deep link (§10).

The promotion is a fabricated `my_chat_member` Update; assertions only look at
the recorded Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import AdminSubscription, Chat, LinkIntent
from app.domain.linking import DEFAULT_EXPIRY_SECONDS, DEFAULT_LADDER
from app.linking.deep_link import INTENT_TTL
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import FIXED_NOW, TestApp, app_fixture
from tests.support.telegram import member_member, member_owner
from tests.support.updates import (
    my_chat_member_update,
    private_callback_update,
    start_update,
    user,
)

# The Postgres container is shared, so every test gets its own Admin and chat.
_admin_ids = count(900, 10)
_chat_ids = count(-100200, -10)


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


async def linked_menu(app: TestApp, admin_id: int) -> int:
    """The Admin started, picked a language and pressed Add to chat."""
    menu_message_id = await started_admin(app, admin_id)
    await app.feed(
        private_callback_update(admin_id, "menu:add-to-chat:", menu_message_id, language_code="en")
    )
    app.session.calls.clear()  # only the promotion's calls matter from here on
    return menu_message_id


async def the_intent(session_maker: async_sessionmaker[AsyncSession], admin_id: int) -> LinkIntent:
    async with session_maker() as db:
        return (
            await db.execute(select(LinkIntent).where(LinkIntent.user_id == admin_id))
        ).scalar_one()


async def stored_chat(session_maker: async_sessionmaker[AsyncSession], chat_id: int) -> Chat | None:
    async with session_maker() as db:
        return await db.get(Chat, chat_id)


async def stored_subscription(
    session_maker: async_sessionmaker[AsyncSession], chat_id: int, admin_id: int
) -> AdminSubscription | None:
    async with session_maker() as db:
        return await db.get(AdminSubscription, (chat_id, admin_id))


async def test_add_to_chat_shows_the_deep_link_and_stores_a_one_hour_intent(
    app: TestApp, admin_id: int
) -> None:
    menu_message_id = await started_admin(app, admin_id)

    await app.feed(
        private_callback_update(admin_id, "menu:add-to-chat:", menu_message_id, language_code="en")
    )

    names = app.session.call_names()
    # The bot's username for the link, the screen, and the button press answer.
    assert names[3:] == ["GetMe", "EditMessageText", "AnswerCallbackQuery"]
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert edit.message_id == menu_message_id  # the same self-editing Menu message
    (open_picker,), (back,) = edit.reply_markup.inline_keyboard
    assert open_picker.text == "🔵 Open the group picker"
    assert back.callback_data == "menu:home:"

    intent = await the_intent(app.session_maker, admin_id)
    assert open_picker.url == (
        f"https://t.me/laya_moderator_bot?startgroup={intent.token}"
        "&admin=delete_messages+restrict_members"
    )
    assert intent.user_id == admin_id  # bound to the Admin who pressed the button
    assert intent.expires_at == FIXED_NOW + INTENT_TTL  # valid for 1 hour


async def test_promotion_links_the_chat_and_edits_the_linkers_menu(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu_message_id = await linked_menu(app, admin_id)
    # Check 3 runs against getChatMember: the person who added the bot is its creator.
    app.session.script(GetChatMember, member_owner(user(admin_id)))

    await app.feed(
        my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title="My Chat")
    )

    assert app.session.call_names() == ["GetChatMember", "EditMessageText"]
    edit = app.session.calls_of("EditMessageText")[0].method
    assert (edit.chat_id, edit.message_id) == (admin_id, menu_message_id)
    lines = (edit.text or "").splitlines()
    assert lines[0] == "✅ My Chat linked"
    assert "Mode: Observation Mode" in lines  # every chat starts in Observation Mode
    assert "Backend: Laya" in lines
    assert "Sensitivity: Balanced" in lines
    (back,) = edit.reply_markup.inline_keyboard[0]
    assert back.callback_data == "menu:home:"

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.linker_id == admin_id
    assert chat.mode == "observation"
    assert chat.backend == "laya"
    assert chat.sensitivity == "balanced"
    assert chat.chat_language == "en"  # the Linker's interface language
    assert list(chat.ladder) == list(DEFAULT_LADDER)
    assert chat.expiry_seconds == DEFAULT_EXPIRY_SECONDS
    assert chat.linked_at == FIXED_NOW

    subscription = await stored_subscription(app.session_maker, chat_id, admin_id)
    assert subscription is not None
    assert subscription.alert_mode == "all"  # the Linker's alert subscription

    async with app.session_maker() as db:
        left = (
            (await db.execute(select(LinkIntent).where(LinkIntent.user_id == admin_id)))
            .scalars()
            .all()
        )
    assert left == []  # the token was single use


async def test_a_basic_group_gets_its_own_upgrade_explanation(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu_message_id = await linked_menu(app, admin_id)

    await app.feed(my_chat_member_update(chat_id, "group", linker_id=admin_id, title="Old Chat"))

    assert app.session.call_names() == [
        "EditMessageText"
    ]  # nothing live to check: not a supergroup
    edit = app.session.calls_of("EditMessageText")[0].method
    assert (edit.chat_id, edit.message_id) == (admin_id, menu_message_id)
    text = edit.text or ""
    assert "Old Chat" in text
    assert "basic group" in text
    assert "supergroup" in text  # the explanation says how the chat is upgraded
    (check_against,), (back,) = edit.reply_markup.inline_keyboard
    assert check_against.text == "🔵 Check again"
    assert check_against.callback_data == f"link-check:{chat_id}"
    assert back.callback_data == "menu:home:"

    assert await stored_chat(app.session_maker, chat_id) is None
    assert await the_intent(app.session_maker, admin_id) is not None  # not consumed


async def test_missing_rights_are_listed_exactly(app: TestApp, admin_id: int, chat_id: int) -> None:
    menu_message_id = await linked_menu(app, admin_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))

    await app.feed(
        my_chat_member_update(
            chat_id,
            "supergroup",
            linker_id=admin_id,
            title="My Chat",
            can_delete_messages=True,
            can_restrict_members=False,
        )
    )

    edit = app.session.calls_of("EditMessageText")[0].method
    assert (edit.chat_id, edit.message_id) == (admin_id, menu_message_id)
    text = edit.text or ""
    assert "My Chat" in text
    assert "restrict members" in text
    assert "delete messages" not in text  # exactly what is missing
    assert await stored_chat(app.session_maker, chat_id) is None


async def test_a_linker_who_is_not_an_admin_is_told_so(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu_message_id = await linked_menu(app, admin_id)
    # getChatMember says the person who added the bot is a plain member.
    app.session.script(GetChatMember, member_member(user(admin_id)))

    await app.feed(
        my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title="My Chat")
    )

    edit = app.session.calls_of("EditMessageText")[0].method
    assert (edit.chat_id, edit.message_id) == (admin_id, menu_message_id)
    assert "not an admin" in (edit.text or "")
    (check_against,), _ = edit.reply_markup.inline_keyboard
    assert check_against.callback_data == f"link-check:{chat_id}"
    assert await stored_chat(app.session_maker, chat_id) is None
