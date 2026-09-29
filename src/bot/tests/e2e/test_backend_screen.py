"""End-to-end: the Classifier Backend screen (§5, §13).

The Settings screen leads to the backend choice; the current choice wears
the primary style, a backend that can never answer here is `disabled`, and
picking an available backend stores it for the chat. Assertions only look
at the recorded Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.types import DisabledButton
from app.db.models import Chat

from tests.support.harness import TestApp, app_fixture, auto_moderation_chat
from tests.support.updates import private_callback_update

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(4600, 10)
_chat_ids = count(-100900, -10)


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        # No JEV_API_KEY: Jev has no client in this Instance (§5).
        app.backend.jev_available = False
        yield app


async def open_backend_screen(app: TestApp, admin_id: int, chat_id: int, menu_message_id: int):
    await app.feed(
        private_callback_update(admin_id, f"chat-backend:{chat_id}:", menu_message_id, "en")
    )
    return app.session.calls_of("EditMessageText")[-1].method


async def test_the_settings_screen_leads_to_the_backend_choice(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)

    # Settings lists the backend entry between Categories and Sensitivity.
    await app.feed(private_callback_update(admin_id, f"chat-settings:{chat_id}", menu, "en"))
    edit = app.session.calls_of("EditMessageText")[-1].method
    (backend_button,) = [
        b
        for b in edit.reply_markup.inline_keyboard[2]
        if b.callback_data and b.callback_data.startswith("chat-backend:")
    ]
    assert backend_button.callback_data == f"chat-backend:{chat_id}:"

    # The screen offers Laya and Jev; Laya is deployed and healthy here, and
    # the chat came linked with it, so Laya wears the primary style.
    edit = await open_backend_screen(app, admin_id, chat_id, menu)
    assert "classifier" in (edit.text or "")
    (laya, jev) = (edit.reply_markup.inline_keyboard[0][0], edit.reply_markup.inline_keyboard[1][0])
    assert laya.text == "Laya"
    assert laya.style is not None  # the chat's current choice
    assert laya.disabled is None  # deployed: it can answer
    assert jev.disabled is not None  # no key: it can never answer (§5)


async def test_a_backend_that_can_never_answer_is_disabled(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)

    # Jev without a key can never answer in this Instance (§5).
    edit = await open_backend_screen(app, admin_id, chat_id, menu)
    (laya, jev) = (edit.reply_markup.inline_keyboard[0][0], edit.reply_markup.inline_keyboard[1][0])
    assert jev.disabled is not None
    assert isinstance(jev.disabled, DisabledButton)
    assert "(unavailable)" in jev.text

    # A Laya that never answered /health can never answer either.
    app.backend.laya_deployed = False
    edit = await open_backend_screen(app, admin_id, chat_id, menu)
    (laya, jev) = (edit.reply_markup.inline_keyboard[0][0], edit.reply_markup.inline_keyboard[1][0])
    assert laya.disabled is not None


async def test_picking_a_backend_stores_it(app: TestApp, admin_id: int, chat_id: int) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)

    # Jev is unavailable but not never-available (no key means no client, so
    # the pick is refused server-side), so the Admin picks Laya — already
    # current, but the pick flow itself is what is under test.
    await app.feed(private_callback_update(admin_id, f"chat-backend:{chat_id}:laya", menu, "en"))
    edit = app.session.calls_of("EditMessageText")[-1].method
    (laya, _jev) = (
        edit.reply_markup.inline_keyboard[0][0],
        edit.reply_markup.inline_keyboard[1][0],
    )
    assert laya.text == "Laya"
    assert laya.style is not None

    async with app.session_maker() as db:
        chat = await db.get(Chat, chat_id)
        assert chat is not None
        assert chat.backend == "laya"

    # The Chat screen says so, without the never-available warning.
    await app.feed(private_callback_update(admin_id, f"chat:{chat_id}", menu, "en"))
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "Backend: Laya" in (edit.text or "")
    assert "can never answer" not in (edit.text or "")


async def test_a_dead_chosen_backend_warns_on_the_status_screen(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)

    # The Instance has no Jev key and no deployed Laya: whichever the chat
    # chose can never answer, and the status screen says so (§5).
    app.backend.laya_deployed = False
    await app.feed(private_callback_update(admin_id, f"chat:{chat_id}", menu, "en"))
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "Backend: Laya" in (edit.text or "")
    assert "can never answer" in (edit.text or "")
