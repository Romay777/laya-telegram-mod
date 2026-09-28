"""End-to-end: the fallback Linking path (§10) for a bot that was added by hand.

The Admin taps "I added the bot already" and names the chat with an @username,
a numeric id or a message forwarded from it. The same checks as the deep-link
path run, with the sender treated as the person linking; assertions only look
at the recorded Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.exceptions import TelegramNotFound
from aiogram.methods import GetChat, GetChatMember
from app.db.models import AdminSubscription, Chat, LinkIntent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import TestApp, app_fixture
from tests.support.telegram import (
    BOT_USER,
    chat_facts,
    member_administrator,
    member_member,
    member_owner,
)
from tests.support.updates import (
    forwarded_message_update,
    private_callback_update,
    private_text_update,
    start_update,
    user,
)

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


async def waiting_for_the_chat_name(app: TestApp, admin_id: int, menu_message_id: int) -> None:
    """The Admin pressed "I added the bot already" and the bot waits for input."""
    await app.feed(
        private_callback_update(
            admin_id, "menu:added-already:", menu_message_id, language_code="en"
        )
    )
    app.session.calls.clear()


async def stored_subscription(
    session_maker: async_sessionmaker[AsyncSession], chat_id: int, admin_id: int
) -> AdminSubscription | None:
    async with session_maker() as db:
        return await db.get(AdminSubscription, (chat_id, admin_id))


async def test_fallback_linking_via_an_at_username(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu_message_id = await on_add_chat_screen(app, admin_id)
    await waiting_for_the_chat_name(app, admin_id, menu_message_id)

    # The bot is already an administrator of the named chat; the sender is its creator.
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", "My Chat"))
    app.session.script(GetChatMember, member_administrator(BOT_USER))  # the bot, both rights
    app.session.script(GetChatMember, member_owner(user(admin_id)))  # the sender
    await app.feed(private_text_update(admin_id, "@mychat", message_id=77, language_code="en"))

    # The checks ran against the chat the @username named.
    get_chat = app.session.calls_of("GetChat")[0].method
    assert get_chat.chat_id == "@mychat"  # sent to Telegram as the Admin wrote it
    assert app.session.calls_of("GetChatMember")[0].method.chat_id == chat_id  # the bot's rights
    assert app.session.calls_of("GetChatMember")[1].method.user_id == admin_id  # the sender

    # The read input is deleted (§13); the Menu reports the link.
    (deleted,) = app.session.calls_of("DeleteMessage")
    assert (deleted.method.chat_id, deleted.method.message_id) == (admin_id, 77)
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert (edit.text or "").startswith("✅ My Chat linked")

    # The sender is the person linking: the defaults and their subscription (§10, §12).
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.linker_id == admin_id
    assert chat.mode == "observation"
    subscription = await stored_subscription(app.session_maker, chat_id, admin_id)
    assert subscription is not None and subscription.alert_mode == "all"

    # The wait is over: a later free-text message is nobody's input.
    await app.feed(private_text_update(admin_id, "hello", message_id=78, language_code="en"))
    assert app.session.calls_of("DeleteMessage") == [deleted]  # the stray text was ignored
    assert len(app.session.calls_of("EditMessageText")) == 1


async def test_fallback_linking_via_a_numeric_id(app: TestApp, admin_id: int, chat_id: int) -> None:
    menu_message_id = await on_add_chat_screen(app, admin_id)
    await waiting_for_the_chat_name(app, admin_id, menu_message_id)

    app.session.script(GetChat, chat_facts(chat_id, "supergroup", "My Chat"))
    app.session.script(GetChatMember, member_administrator(BOT_USER))
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(private_text_update(admin_id, str(chat_id), message_id=77, language_code="en"))

    get_chat = app.session.calls_of("GetChat")[0].method
    assert get_chat.chat_id == str(chat_id)  # the -100… id, sent as the Admin wrote it
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert (edit.text or "").startswith("✅ My Chat linked")
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.linker_id == admin_id


async def test_fallback_linking_via_a_forwarded_message(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    """The forward names the chat it came from; the forwarded message stays (§13)."""
    menu_message_id = await on_add_chat_screen(app, admin_id)
    await waiting_for_the_chat_name(app, admin_id, menu_message_id)

    app.session.script(GetChat, chat_facts(chat_id, "supergroup", "My Chat"))
    app.session.script(GetChatMember, member_administrator(BOT_USER))
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        forwarded_message_update(
            admin_id, origin_chat_id=chat_id, message_id=77, language_code="en"
        )
    )

    get_chat = app.session.calls_of("GetChat")[0].method
    assert get_chat.chat_id == str(chat_id)  # the origin chat of the forward
    assert app.session.calls_of("DeleteMessage") == []  # the forwarded message is not deleted
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert (edit.text or "").startswith("✅ My Chat linked")
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.linker_id == admin_id


async def test_an_input_that_names_no_chat_reprompts_and_keeps_waiting(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu_message_id = await on_add_chat_screen(app, admin_id)
    await waiting_for_the_chat_name(app, admin_id, menu_message_id)

    await app.feed(private_text_update(admin_id, "hello", message_id=77, language_code="en"))

    # The read input is deleted, nothing is linked, and the prompt returns with why.
    (deleted,) = app.session.calls_of("DeleteMessage")
    assert (deleted.method.chat_id, deleted.method.message_id) == (admin_id, 77)
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "couldn't see that chat" in (edit.text or "")
    assert await stored_chat(app.session_maker, chat_id) is None

    # The bot still waits: the next message that names a chat links it.
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", "My Chat"))
    app.session.script(GetChatMember, member_administrator(BOT_USER))
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(private_text_update(admin_id, str(chat_id), message_id=78, language_code="en"))

    assert (app.session.calls_of("EditMessageText")[-1].method.text or "").startswith(
        "✅ My Chat linked"
    )
    assert await stored_chat(app.session_maker, chat_id) is not None


async def test_a_chat_the_bot_cannot_see_reprompts(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    """A @username the bot can't resolve is not a Linking attempt (§10)."""
    menu_message_id = await on_add_chat_screen(app, admin_id)
    await waiting_for_the_chat_name(app, admin_id, menu_message_id)

    app.session.script(GetChat, TelegramNotFound(method=None, message="Chat not found"))
    await app.feed(private_text_update(admin_id, "@nowhere", message_id=77, language_code="en"))

    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "couldn't see that chat" in (edit.text or "")
    assert app.session.calls_of("GetChatMember") == []  # the checks never ran
    assert await stored_chat(app.session_maker, chat_id) is None

    # The bot still waits: the same Admin can name a chat that exists.
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", "My Chat"))
    app.session.script(GetChatMember, member_administrator(BOT_USER))
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(private_text_update(admin_id, str(chat_id), message_id=78, language_code="en"))

    assert (app.session.calls_of("EditMessageText")[-1].method.text or "").startswith(
        "✅ My Chat linked"
    )


async def test_fallback_failure_lists_whats_missing_and_check_again_links(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    """The sender is not an admin of the named chat: the failure screen, then success."""
    menu_message_id = await on_add_chat_screen(app, admin_id)
    await waiting_for_the_chat_name(app, admin_id, menu_message_id)

    app.session.script(GetChat, chat_facts(chat_id, "supergroup", "My Chat"))
    app.session.script(GetChatMember, member_administrator(BOT_USER))
    app.session.script(GetChatMember, member_member(user(admin_id)))  # the sender
    await app.feed(private_text_update(admin_id, str(chat_id), message_id=77, language_code="en"))

    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "not an admin" in (edit.text or "")
    (check_against,), (_back,) = edit.reply_markup.inline_keyboard
    # The fallback path has no one-hour token: its Check again asks for no intent.
    assert check_against.callback_data == f"link-check-fb:{chat_id}"
    assert await stored_chat(app.session_maker, chat_id) is None
    app.session.calls.clear()

    # The sender was promoted in the meantime; Check again re-runs the checks live.
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", "My Chat"))
    app.session.script(GetChatMember, member_administrator(BOT_USER))
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        private_callback_update(
            admin_id, f"link-check-fb:{chat_id}", menu_message_id, language_code="en"
        )
    )

    assert (app.session.calls_of("EditMessageText")[-1].method.text or "").startswith(
        "✅ My Chat linked"
    )
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.linker_id == admin_id
    async with app.session_maker() as db:
        intents = (
            (await db.execute(select(LinkIntent).where(LinkIntent.user_id == admin_id)))
            .scalars()
            .all()
        )
    # The one token minted at Add to chat is untouched: the fallback path has no
    # token and its Check again consumes nothing (§10).
    assert len(intents) == 1
