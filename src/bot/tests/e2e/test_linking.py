"""End-to-end: the primary Linking path via the startgroup deep link (§10).

The promotion is a fabricated `my_chat_member` Update; assertions only look at
the recorded Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from app.db.models import LinkIntent
from app.linking.deep_link import INTENT_TTL
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import FIXED_NOW, TestApp, app_fixture
from tests.support.updates import private_callback_update, start_update

# The Postgres container is shared, so every test gets its own Admin.
_admin_ids = count(900, 10)


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


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


async def the_intent(session_maker: async_sessionmaker[AsyncSession], admin_id: int) -> LinkIntent:
    async with session_maker() as db:
        return (
            await db.execute(select(LinkIntent).where(LinkIntent.user_id == admin_id))
        ).scalar_one()


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
