"""End-to-end: the Penalty Ladder and Expiry editor (§6, §13).

Admins shape each chat's escalation with buttons only: the ladder screen
lists the Steps, a Step opens the duration presets, Add step and
Remove last keep the ladder within 1-10 Steps, and the Expiry presets
say when a Violation stops counting. Assertions only look at the recorded
Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import Chat
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import TestApp, app_fixture, linked_via_deeplink
from tests.support.telegram import member_owner
from tests.support.updates import private_callback_update, user

#: The Add-step button's text lives in the locales; the emoji confuses ruff's
#: confusables check, so it is spelled out once here.
ADD_STEP = "\u2795 Add step"

# The Postgres container is shared, so every test gets its own Admin and chat.
_admin_ids = count(2500, 10)
_chat_ids = count(-1001300, -10)


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


async def stored_chat(session_maker: async_sessionmaker[AsyncSession], chat_id: int) -> Chat:
    async with session_maker() as db:
        chat = await db.get(Chat, chat_id)
        assert chat is not None
        return chat


def last_edit(app: TestApp):
    return app.session.calls_of("EditMessageText")[-1].method


def buttons_of(app: TestApp) -> dict[str, object]:
    """The buttons of the last Menu edit, keyed by their text."""
    return {
        button.text: button for row in last_edit(app).reply_markup.inline_keyboard for button in row
    }


async def open_ladder(app: TestApp, admin_id: int, chat_id: int, menu: int) -> None:
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        private_callback_update(admin_id, f"chat-ladder:{chat_id}", menu, language_code="en")
    )


async def test_the_ladder_screen_lists_the_steps_add_remove_and_expiry(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        private_callback_update(admin_id, f"chat-settings:{chat_id}", menu, language_code="en")
    )
    callbacks = [
        button.callback_data
        for row in last_edit(app).reply_markup.inline_keyboard
        for button in row
    ]
    assert f"chat-ladder:{chat_id}" in callbacks  # the §13 Settings entry
    app.session.calls.clear()

    await open_ladder(app, admin_id, chat_id, menu)

    buttons = buttons_of(app)
    assert set(buttons) >= {
        "Step 1: 1 hour",
        "Step 2: 1 day",
        "Step 3: forever",  # the §12 default ladder
        ADD_STEP,
        "🔴 Remove last",
        "7 days",
        "14 days",
        "30 days",
        "60 days",
        "90 days",
        "never",  # the §6 Expiry presets
    }
    assert buttons["30 days"].style == "primary"  # the §12 default Expiry
    assert buttons[ADD_STEP].disabled is None
    assert buttons["🔴 Remove last"].disabled is None


async def test_a_step_opens_the_duration_presets_and_a_pick_is_stored(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    await open_ladder(app, admin_id, chat_id, menu)
    app.session.calls.clear()

    await app.feed(
        private_callback_update(
            admin_id, f"chat-ladder-step:{chat_id}:0:", menu, language_code="en"
        )
    )

    buttons = buttons_of(app)
    assert set(buttons) >= {
        "5 minutes",
        "15 minutes",
        "1 hour",
        "3 hours",
        "12 hours",
        "1 day",
        "3 days",
        "7 days",
        "30 days",
        "forever",
    }
    assert buttons["1 hour"].style == "primary"  # Step 1's current duration
    app.session.calls.clear()

    await app.feed(
        private_callback_update(
            admin_id, f"chat-ladder-step:{chat_id}:0:300", menu, language_code="en"
        )
    )

    buttons = buttons_of(app)
    assert "Step 1: 5 minutes" in buttons  # back on the ladder with the new Step
    chat = await stored_chat(app.session_maker, chat_id)
    assert list(chat.ladder) == [300, 86400, 0]


async def test_add_appends_a_step_and_remove_drops_the_last(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    await open_ladder(app, admin_id, chat_id, menu)
    app.session.calls.clear()

    await app.feed(
        private_callback_update(
            admin_id, f"chat-ladder-edit:{chat_id}:add", menu, language_code="en"
        )
    )

    assert "Step 4: 30 days" in buttons_of(app)  # a new Step starts at 30 days
    chat = await stored_chat(app.session_maker, chat_id)
    assert list(chat.ladder) == [3600, 86400, 0, 2592000]
    app.session.calls.clear()

    await app.feed(
        private_callback_update(
            admin_id, f"chat-ladder-edit:{chat_id}:remove", menu, language_code="en"
        )
    )

    buttons = buttons_of(app)
    assert "Step 4: 30 days" not in buttons
    assert "Step 3: forever" in buttons
    chat = await stored_chat(app.session_maker, chat_id)
    assert list(chat.ladder) == [3600, 86400, 0]


async def test_buttons_that_would_break_the_one_to_ten_limit_are_disabled(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    await open_ladder(app, admin_id, chat_id, menu)
    app.session.calls.clear()

    # Seven adds take the default 3-Step ladder to the §6 maximum of 10.
    for _ in range(7):
        await app.feed(
            private_callback_update(
                admin_id, f"chat-ladder-edit:{chat_id}:add", menu, language_code="en"
            )
        )
    buttons = buttons_of(app)
    assert buttons[ADD_STEP].disabled is not None
    assert buttons["🔴 Remove last"].disabled is None
    chat = await stored_chat(app.session_maker, chat_id)
    assert len(chat.ladder) == 10
    app.session.calls.clear()

    # Another add is refused: the ladder stays at 10 Steps.
    await app.feed(
        private_callback_update(
            admin_id, f"chat-ladder-edit:{chat_id}:add", menu, language_code="en"
        )
    )
    chat = await stored_chat(app.session_maker, chat_id)
    assert len(chat.ladder) == 10
    app.session.calls.clear()

    # Nine removes take it back down to the §6 minimum of 1.
    for _ in range(9):
        await app.feed(
            private_callback_update(
                admin_id, f"chat-ladder-edit:{chat_id}:remove", menu, language_code="en"
            )
        )
    buttons = buttons_of(app)
    assert buttons["🔴 Remove last"].disabled is not None
    assert buttons[ADD_STEP].disabled is None
    assert "Step 1: 1 hour" in buttons
    chat = await stored_chat(app.session_maker, chat_id)
    assert list(chat.ladder) == [3600]
    app.session.calls.clear()

    # One more remove is refused: the ladder is never empty.
    await app.feed(
        private_callback_update(
            admin_id, f"chat-ladder-edit:{chat_id}:remove", menu, language_code="en"
        )
    )
    chat = await stored_chat(app.session_maker, chat_id)
    assert list(chat.ladder) == [3600]


async def test_expiry_presets_store_the_period_and_never(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    await open_ladder(app, admin_id, chat_id, menu)
    app.session.calls.clear()

    await app.feed(
        private_callback_update(admin_id, f"chat-expiry:{chat_id}:604800", menu, language_code="en")
    )

    buttons = buttons_of(app)
    assert buttons["7 days"].style == "primary"
    assert buttons["30 days"].style is None
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat.expiry_seconds == 604800
    app.session.calls.clear()

    await app.feed(
        private_callback_update(admin_id, f"chat-expiry:{chat_id}:0", menu, language_code="en")
    )

    buttons = buttons_of(app)
    assert buttons["never"].style == "primary"
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat.expiry_seconds is None  # never (§12)
