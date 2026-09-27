"""End-to-end: /start → language choice → Home, all in one self-editing message.

Fabricated Updates go through the real Dispatcher; assertions only look at
the recorded Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import EditMessageText
from app.db.models import BotUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import FIXED_NOW, TestApp, app_fixture, build_app
from tests.support.updates import private_callback_update, start_update

# The Postgres container is shared, so every test talks to the bot as a
# different Admin.
_admin_ids = count(700, 10)


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        yield app


async def stored_user(
    session_maker: async_sessionmaker[AsyncSession], user_id: int
) -> BotUser | None:
    async with session_maker() as db:
        return await db.get(BotUser, user_id)


async def test_start_of_a_new_user_shows_the_language_screen(app: TestApp, admin_id: int) -> None:
    await app.feed(start_update(admin_id, language_code="ru"))

    assert app.session.call_names() == ["SendMessage"]
    sent = app.session.calls_of("SendMessage")[0].method
    assert sent.text == "Выберите язык"  # the Telegram client language, before any choice
    assert app.session.calls_of("SendMessage")[0].result.message_id > 0

    user = await stored_user(app.session_maker, admin_id)
    assert user is not None
    assert user.started_at == FIXED_NOW  # time comes from the Clock
    assert user.language is None  # not chosen yet


async def test_language_choice_edits_the_same_message_into_home(
    app: TestApp, admin_id: int
) -> None:
    await app.feed(start_update(admin_id, language_code="en"))
    menu_message_id = app.session.calls_of("SendMessage")[0].result.message_id

    await app.feed(
        private_callback_update(
            admin_id, "menu:set-language:en", menu_message_id, language_code="en"
        )
    )

    # Every step after the first is an in-place edit of the same message.
    names_after_start = app.session.call_names()[1:]
    assert "SendMessage" not in names_after_start
    edit_calls = app.session.calls_of("EditMessageText")
    assert [call.method.message_id for call in edit_calls] == [menu_message_id]
    edit: EditMessageText = edit_calls[0].method
    assert edit.chat_id == admin_id
    assert edit.text == "Menu"

    user = await stored_user(app.session_maker, admin_id)
    assert user is not None
    assert user.language == "en"  # the choice is stored


async def test_repeated_start_reedits_the_stored_menu_message(app: TestApp, admin_id: int) -> None:
    await app.feed(start_update(admin_id, language_code="en"))
    menu_message_id = app.session.calls_of("SendMessage")[0].result.message_id
    await app.feed(
        private_callback_update(
            admin_id, "menu:set-language:en", menu_message_id, language_code="en"
        )
    )

    await app.feed(start_update(admin_id, language_code="en"))

    names_after_choice = app.session.call_names()[2:]
    assert "SendMessage" not in names_after_choice
    edits = app.session.calls_of("EditMessageText")
    assert len(edits) == 2  # the language choice, then /start again
    assert all(call.method.message_id == menu_message_id for call in edits)


async def test_language_switch_from_home_renders_every_menu_string_in_ru(
    app: TestApp, admin_id: int
) -> None:
    await app.feed(start_update(admin_id, language_code="en"))
    menu_message_id = app.session.calls_of("SendMessage")[0].result.message_id
    await app.feed(
        private_callback_update(
            admin_id, "menu:set-language:en", menu_message_id, language_code="en"
        )
    )

    # Home → Language screen → Русский
    await app.feed(
        private_callback_update(admin_id, "menu:language:", menu_message_id, language_code="en")
    )
    language_screen = app.session.calls_of("EditMessageText")[-1].method
    assert language_screen.text == "Choose your language"

    await app.feed(
        private_callback_update(
            admin_id, "menu:set-language:ru", menu_message_id, language_code="en"
        )
    )
    home = app.session.calls_of("EditMessageText")[-1].method
    assert home.text == "Меню"
    (add_to_chat,), (language,), (how_it_works,) = home.reply_markup.inline_keyboard
    assert add_to_chat.text == "🔵 Добавить чат"
    assert language.text == "Язык"
    assert how_it_works.text == "Как это работает"

    user = await stored_user(app.session_maker, admin_id)
    assert user is not None
    assert user.language == "ru"

    # The How it works screen now renders in Russian too.
    await app.feed(
        private_callback_update(admin_id, "menu:how-it-works:", menu_message_id, language_code="en")
    )
    how_screen = app.session.calls_of("EditMessageText")[-1].method
    assert how_screen.text.startswith("Laya проверяет")


async def test_start_after_restart_lands_on_home_in_a_new_app_instance(
    app: TestApp, postgres_url: str, admin_id: int
) -> None:
    """A second app instance over the same DB finds the stored Menu message."""
    await app.feed(start_update(admin_id, language_code="en"))
    menu_message_id = app.session.calls_of("SendMessage")[0].result.message_id
    await app.feed(
        private_callback_update(
            admin_id, "menu:set-language:en", menu_message_id, language_code="en"
        )
    )

    second_instance = await build_app(postgres_url)  # new app, same DB
    try:
        await second_instance.feed(start_update(admin_id, language_code="en"))

        assert second_instance.session.call_names() == ["EditMessageText"]
        edit = second_instance.session.calls_of("EditMessageText")[0].method
        assert edit.message_id == menu_message_id  # the stored Menu message, found via the DB
        assert edit.text == "Menu"
    finally:
        await second_instance.aclose()
