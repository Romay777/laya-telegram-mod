"""Seam: the Menu navigator's public API (§13).

Show a screen into the Admin's single Menu message: edit the stored message,
or, when it can't be edited any more, send a new one and remove the old where
possible.
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, EditMessageText
from aiogram.types import InlineKeyboardMarkup
from app.clock import FakeClock
from app.db.models import BotUser
from app.db.repositories.users import BotUserRepository
from app.menu.navigator import MenuNavigator
from app.menu.screens.home import ChatSummary
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.support.fake_session import FakeBotSession
from tests.support.i18n import started_core

# The Postgres container is shared across tests, so every test talks to the
# bot as a different Admin.
_admin_ids = count(501, 10)


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def session() -> FakeBotSession:
    return FakeBotSession()


@pytest.fixture
def bot(session: FakeBotSession) -> Bot:
    return Bot("42:test-token", session=session)


@pytest.fixture
async def db_session(postgres_url: str) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(create_async_engine(postgres_url), expire_on_commit=False)
    async with maker() as db:
        yield db


@pytest.fixture
async def navigator() -> AsyncIterator[MenuNavigator]:
    yield MenuNavigator(core=await started_core())


async def new_user(db_session: AsyncSession, user_id: int) -> BotUser:
    return await BotUserRepository(db_session).get_or_create(user_id, started_at=FakeClock().now())


async def test_first_start_sends_language_screen_highlighting_telegram_language(
    admin_id: int,
    session: FakeBotSession,
    bot: Bot,
    db_session: AsyncSession,
    navigator: MenuNavigator,
) -> None:
    user = await new_user(db_session, admin_id)

    await navigator.show_language_screen(
        bot=bot,
        session=db_session,
        user=user,
        telegram_language_code="ru",
        locale="ru",
    )
    await db_session.commit()

    assert session.call_names() == ["SendMessage"]
    sent = session.calls_of("SendMessage")[0].method
    assert sent.chat_id == admin_id
    assert sent.text == "Выберите язык"  # the Telegram client language, before any choice
    markup = sent.reply_markup
    assert markup is not None
    ru, en = markup.inline_keyboard[0]
    assert ru.text == "🇷🇺 Русский"
    assert ru.style == "primary"  # matches the Telegram language_code
    assert ru.callback_data == "menu:set-language:ru"
    assert en.text == "🇬🇧 English"
    assert en.style is None
    assert en.callback_data == "menu:set-language:en"
    assert user.menu_message_id == session.calls_of("SendMessage")[0].result.message_id


async def test_language_screen_without_matching_telegram_language(
    admin_id: int,
    session: FakeBotSession,
    bot: Bot,
    db_session: AsyncSession,
    navigator: MenuNavigator,
) -> None:
    user = await new_user(db_session, admin_id)

    await navigator.show_language_screen(
        bot=bot,
        session=db_session,
        user=user,
        telegram_language_code="de",
        locale="en",
    )

    ru, en = session.calls_of("SendMessage")[0].method.reply_markup.inline_keyboard[0]
    assert ru.style is None
    assert en.style is None


async def test_home_edits_the_stored_menu_message(
    admin_id: int,
    session: FakeBotSession,
    bot: Bot,
    db_session: AsyncSession,
    navigator: MenuNavigator,
) -> None:
    user = await new_user(db_session, admin_id)
    user.menu_message_id = 100  # the language screen was sent earlier

    await navigator.show_home(bot=bot, session=db_session, user=user, locale="en", chats=[])
    await db_session.commit()

    assert "SendMessage" not in session.call_names()
    edit = session.calls_of("EditMessageText")[0].method
    assert isinstance(edit, EditMessageText)
    assert edit.chat_id == admin_id
    assert edit.message_id == 100  # the same message, edited in place
    assert edit.text == "Menu"
    assert user.menu_message_id == 100


async def test_home_buttons_offer_add_to_chat_language_and_how_it_works(
    admin_id: int,
    session: FakeBotSession,
    bot: Bot,
    db_session: AsyncSession,
    navigator: MenuNavigator,
) -> None:
    user = await new_user(db_session, admin_id)
    user.menu_message_id = 100

    await navigator.show_home(bot=bot, session=db_session, user=user, locale="en", chats=[])

    markup: InlineKeyboardMarkup = session.calls_of("EditMessageText")[0].method.reply_markup
    (add_to_chat,), (language,), (how_it_works,) = markup.inline_keyboard
    assert add_to_chat.text == "🔵 Add to chat"
    assert add_to_chat.style == "primary"  # the main action on the screen
    assert add_to_chat.callback_data == "menu:add-to-chat:"
    assert language.text == "Language"
    assert language.callback_data == "menu:language:"
    assert how_it_works.text == "How it works"
    assert how_it_works.callback_data == "menu:how-it-works:"


async def test_home_offers_one_button_per_chat_above_the_fixed_rows(
    admin_id: int,
    session: FakeBotSession,
    bot: Bot,
    db_session: AsyncSession,
    navigator: MenuNavigator,
) -> None:
    user = await new_user(db_session, admin_id)
    user.menu_message_id = 100

    await navigator.show_home(
        bot=bot,
        session=db_session,
        user=user,
        locale="en",
        chats=[
            ChatSummary(chat_id=-100200, title="My Chat"),
            ChatSummary(chat_id=-100300, title=None),
        ],
    )

    markup: InlineKeyboardMarkup = session.calls_of("EditMessageText")[0].method.reply_markup
    (first,), (second,), (add_to_chat,) = markup.inline_keyboard[:3]
    assert first.text == "My Chat"
    assert first.callback_data == "chat:-100200"  # the callback data carries the chat_id
    assert second.text == "—"  # a chat without a title still gets its button
    assert second.callback_data == "chat:-100300"
    assert add_to_chat.text == "🔵 Add to chat"


async def test_uneditable_menu_message_is_replaced_by_a_new_one(
    admin_id: int,
    session: FakeBotSession,
    bot: Bot,
    db_session: AsyncSession,
    navigator: MenuNavigator,
) -> None:
    user = await new_user(db_session, admin_id)
    user.menu_message_id = 100  # deleted or too old: Telegram refuses the edit
    session.script(
        EditMessageText,
        TelegramBadRequest(method=None, message="Bad Request: message to edit not found"),
    )

    await navigator.show_home(bot=bot, session=db_session, user=user, locale="en", chats=[])
    await db_session.commit()

    assert "SendMessage" in session.call_names()
    deleted = session.calls_of("DeleteMessage")[0].method
    assert isinstance(deleted, DeleteMessage)
    assert deleted.message_id == 100  # the stale message is removed where possible
    assert user.menu_message_id == session.calls_of("SendMessage")[0].result.message_id


async def test_how_it_works_screen_edits_in_place_in_the_admin_language(
    admin_id: int,
    session: FakeBotSession,
    bot: Bot,
    db_session: AsyncSession,
    navigator: MenuNavigator,
) -> None:
    user = await new_user(db_session, admin_id)
    user.menu_message_id = 100

    await navigator.show_how_it_works(bot=bot, session=db_session, user=user, locale="ru")

    edit = session.calls_of("EditMessageText")[0].method
    assert edit.message_id == 100
    assert edit.text.startswith("Laya проверяет")  # every Menu string follows the language
    (back,) = edit.reply_markup.inline_keyboard[0]
    assert back.text == "Назад"
    assert back.callback_data == "menu:home:"
