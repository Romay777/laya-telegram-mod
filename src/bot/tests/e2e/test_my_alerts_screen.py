"""End-to-end: the My alerts screen (§13, §9).

Each Admin picks their own alert mode per chat: All, Appeals only, or Off.
The Linker defaults to All; other Admins default to Off. Access to the
screen is re-checked like every chat-scoped callback (§13).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import AdminSubscription
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import (
    TestApp,
    app_fixture,
    auto_moderation_chat,
    linked_via_deeplink,
    started_admin,
)
from tests.support.telegram import member_administrator, member_member, member_owner
from tests.support.updates import private_callback_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(1700, 10)
_chat_ids = count(-100600, -10)


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def other_admin_id() -> Iterator[int]:
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


async def subscriptions(
    session_maker: async_sessionmaker[AsyncSession],
) -> list[AdminSubscription]:
    async with session_maker() as db:
        rows = (await db.execute(select(AdminSubscription))).scalars().all()
        return sorted(rows, key=lambda row: row.user_id)


async def test_settings_leads_to_my_alerts_and_the_linker_defaults_to_all(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    app.session.calls.clear()

    # Settings carries the My alerts button.
    await app.feed(
        private_callback_update(admin_id, f"chat-settings:{chat_id}", menu, language_code="en")
    )
    edit = app.session.calls_of("EditMessageText")[-1].method
    keyboard = edit.reply_markup.inline_keyboard
    assert any(
        button.callback_data == f"chat-alerts:{chat_id}:" for row in keyboard for button in row
    )
    app.session.calls.clear()

    # The Linker's mode defaults to All, set when they linked the chat (§9).
    await app.feed(
        private_callback_update(admin_id, f"chat-alerts:{chat_id}:", menu, language_code="en")
    )
    edit = app.session.calls_of("EditMessageText")[-1].method
    modes = {button.text: button for row in edit.reply_markup.inline_keyboard for button in row}
    assert {"All", "Appeals only", "Off"} <= set(modes)  # plus Back
    assert modes["All"].style == "primary"  # the current choice is marked
    assert modes["All"].callback_data == f"chat-alerts:{chat_id}:all"
    assert modes["Appeals only"].callback_data == f"chat-alerts:{chat_id}:appeals"
    assert modes["Off"].callback_data == f"chat-alerts:{chat_id}:off"

    rows = await subscriptions(app.session_maker)
    assert [(row.user_id, row.alert_mode) for row in rows] == [(admin_id, "all")]


async def test_an_admin_opts_in_from_off_and_the_choice_persists(
    app: TestApp, admin_id: int, other_admin_id: int, chat_id: int
) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    # A second Admin of the same chat starts the bot and opens the chat.
    app.session.script(GetChatMember, member_administrator(user(other_admin_id)))
    other_menu = await started_admin(app, other_admin_id)
    await app.feed(
        private_callback_update(other_admin_id, f"chat:{chat_id}", other_menu, language_code="en")
    )
    await app.feed(
        private_callback_update(
            other_admin_id, f"chat-settings:{chat_id}", other_menu, language_code="en"
        )
    )
    await app.feed(
        private_callback_update(
            other_admin_id, f"chat-alerts:{chat_id}:", other_menu, language_code="en"
        )
    )

    # Off is the default for an Admin who is not the Linker (§9).
    edit = app.session.calls_of("EditMessageText")[-1].method
    modes = {button.text: button for row in edit.reply_markup.inline_keyboard for button in row}
    assert modes["Off"].style == "primary"
    rows = await subscriptions(app.session_maker)
    assert [(row.user_id, row.alert_mode) for row in rows] == [(admin_id, "all")]

    # They opt in to All, and the choice is stored (§9).
    await app.feed(
        private_callback_update(
            other_admin_id, f"chat-alerts:{chat_id}:all", other_menu, language_code="en"
        )
    )
    edit = app.session.calls_of("EditMessageText")[-1].method
    modes = {button.text: button for row in edit.reply_markup.inline_keyboard for button in row}
    assert modes["All"].style == "primary"
    rows = await subscriptions(app.session_maker)
    assert [(row.user_id, row.alert_mode) for row in rows] == [
        (admin_id, "all"),
        (other_admin_id, "all"),
    ]

    # The Linker's own screen still shows All; a switch back to Off works.
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        private_callback_update(admin_id, f"chat-alerts:{chat_id}:off", menu, language_code="en")
    )
    rows = await subscriptions(app.session_maker)
    assert [(row.user_id, row.alert_mode) for row in rows] == [
        (admin_id, "off"),
        (other_admin_id, "all"),
    ]


async def test_a_non_admin_cannot_change_anyone_alerts(
    app: TestApp, admin_id: int, stranger_id: int, chat_id: int
) -> None:
    await linked_via_deeplink(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_member(user(stranger_id)))  # the Home render
    stranger_menu = await started_admin(app, stranger_id)

    await app.feed(
        private_callback_update(
            stranger_id, f"chat-alerts:{chat_id}:all", stranger_menu, language_code="en"
        )
    )

    answer = app.session.calls_of("AnswerCallbackQuery")[-1].method
    assert answer.text == "You are no longer an admin of this chat."
    rows = await subscriptions(app.session_maker)
    assert [(row.user_id, row.alert_mode) for row in rows] == [(admin_id, "all")]


async def test_switching_off_the_last_appeal_recipient_warns_first(
    app: TestApp, admin_id: int, other_admin_id: int, chat_id: int
) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_administrator(user(other_admin_id)))
    other_menu = await started_admin(app, other_admin_id)
    await app.feed(
        private_callback_update(
            other_admin_id, f"chat-alerts:{chat_id}:all", other_menu, language_code="en"
        )
    )
    app.session.calls.clear()

    # The Linker switches off while the second Admin still receives Appeals:
    # no warning — Appeals live on (§9).
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        private_callback_update(admin_id, f"chat-alerts:{chat_id}:off", menu, language_code="en")
    )
    (answer,) = app.session.calls_of("AnswerCallbackQuery")
    assert answer.method.text is None  # no warning while Appeals live on
    app.session.calls.clear()

    # The last Appeal recipient switches off: the warning comes first (§9).
    await app.feed(
        private_callback_update(
            other_admin_id, f"chat-alerts:{chat_id}:off", other_menu, language_code="en"
        )
    )
    (answer,) = app.session.calls_of("AnswerCallbackQuery")
    assert answer.method.text == "Members won't be able to appeal"
    assert answer.method.show_alert is True
    rows = await subscriptions(app.session_maker)
    assert all(row.alert_mode == "off" for row in rows)
