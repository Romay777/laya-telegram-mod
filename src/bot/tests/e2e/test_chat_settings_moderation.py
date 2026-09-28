"""End-to-end: how Categories, Sensitivity and Chat Language shape moderation.

The acceptance behaviours of the ticket: an ads-only chat that allows ads;
Strict catching a message that Balanced lets through; and a Chat Notice in
Russian while the Admin's interface is in English. Assertions only look at
the recorded Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.classifiers.client import Probabilities
from app.db.models import MessageCheck, Violation
from app.menu.callbacks import AppealCallback
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import TestApp, app_fixture, auto_moderation_chat, linked_via_deeplink
from tests.support.telegram import member_member, member_owner
from tests.support.updates import (
    group_callback_update,
    group_message_update,
    private_callback_update,
    user,
)

# The Postgres container is shared, so every test gets its own people and chat.
_admin = count(2000, 100)
_chat_ids = count(-101000, -100)

ADSY = Probabilities({"spam": 0.10, "ads": 0.95, "insult": 0.02, "clean": 0.03})
# In the Balanced suspicion band (0.60..0.90), a full Violation under Strict (>= 0.80).
MID_ADS = Probabilities({"spam": 0.05, "ads": 0.85, "insult": 0.02, "clean": 0.08})
AD_TEXT = "Check out my shop, great prices, https://example.com"


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin)


@pytest.fixture
def member_id() -> Iterator[int]:
    yield next(_admin)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        yield app


async def send_member_message(
    app: TestApp, member_id: int, chat_id: int, text: str, *, message_id: int
) -> None:
    app.session.script(GetChatMember, member_member(user(member_id)))  # not an Admin
    await app.feed(group_message_update(chat_id, member_id, text, message_id=message_id))


async def checks(session_maker: async_sessionmaker[AsyncSession]) -> list[tuple[str, str | None]]:
    async with session_maker() as db:
        rows = (await db.execute(select(MessageCheck))).scalars().all()
        return [(row.outcome, row.category) for row in rows]


async def violations(session_maker: async_sessionmaker[AsyncSession]) -> list[Violation]:
    async with session_maker() as db:
        return list((await db.execute(select(Violation))).scalars())


async def pick(app: TestApp, admin_id: int, chat_id: int, menu: int, data: str) -> None:
    """One Menu press, with the Admin re-check consuming the scripted answer.

    The admin cache remembers the Admin for 5 minutes, so a press that hits
    the cache consumes no scripted `GetChatMember` — and a leftover scripted
    answer would be eaten by the pipeline's own check for the next Member.
    Popping any unused answer keeps the scripted queue aligned.
    """
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(private_callback_update(admin_id, data, menu, language_code="en"))
    app.session.calls.clear()
    app.session._scripted.pop(GetChatMember, None)


async def test_an_ads_only_chat_allows_ads(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """Ads off, spam and insult on: an obvious ad produces no action at all."""
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    await pick(app, admin_id, chat_id, menu, f"chat-categories:{chat_id}:ads")

    app.backend.script(ADSY)
    await send_member_message(app, member_id, chat_id, AD_TEXT, message_id=61)

    # The ad's Verdict falls through to the next enabled label, far below any
    # threshold: nothing was deleted or restricted (§4).
    assert app.session.call_names() == ["GetChatMember"]
    assert await checks(app.session_maker) == [("clean", "spam")]
    assert await violations(app.session_maker) == []

    # Spam still gets through the same chat's defences: ads off, spam on.
    SPAMMY = Probabilities({"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01})
    app.backend.script(SPAMMY)
    await send_member_message(
        app, member_id + 1, chat_id, "Buy cheap crypto now, DM me", message_id=62
    )
    assert "DeleteMessage" in app.session.call_names()
    outcomes = await checks(app.session_maker)
    assert outcomes[-1] == ("violation", "spam")


async def test_strict_catches_a_message_that_balanced_lets_through(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """The same ad at 0.85: a Suspicion under Balanced, a Violation under Strict.

    "Lets through" is the Suspicion band: the message stays in the chat and
    only the private alert goes out (§9). Under Strict the same Verdict
    crosses the violation threshold (0.80) and the message is deleted (§3).
    """
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(MID_ADS)
    await send_member_message(app, member_id, chat_id, AD_TEXT, message_id=71)
    assert await checks(app.session_maker) == [("suspicion", "ads")]
    # The Linker's owner check is already warm in the admin cache from arming
    # Auto-moderation, so only the Member's check runs before the alert.
    assert app.session.call_names() == [
        "GetChatMember",
        "SendMessage",  # the Suspicion alert; the message itself stays
    ]
    app.session.calls.clear()

    await pick(app, admin_id, chat_id, menu, f"chat-sensitivity:{chat_id}:strict")

    await send_member_message(app, member_id, chat_id, AD_TEXT, message_id=72)
    assert "DeleteMessage" in app.session.call_names()
    assert await checks(app.session_maker) == [("suspicion", "ads"), ("violation", "ads")]
    assert [v.category for v in await violations(app.session_maker)] == ["ads"]


async def test_a_chat_notice_in_russian_while_the_admin_interface_is_english(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    """The Chat Notice and its Appeal button speak Russian; the alerts stay English."""
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    await pick(app, admin_id, chat_id, menu, f"chat-language:{chat_id}:ru")

    SPAMMY = Probabilities({"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01})
    app.backend.script(SPAMMY)
    await send_member_message(app, member_id, chat_id, "Buy cheap crypto now", message_id=81)

    (notice, alert) = app.session.calls_of("SendMessage")
    assert notice.method.chat_id == chat_id
    assert notice.method.text == (
        "Member, кажется, ваше сообщение похоже на спам. Вы не можете писать в чат 1 час."
    )
    (button,) = notice.method.reply_markup.inline_keyboard[0]
    assert button.text == "🙋 Это ошибка"
    assert alert.method.chat_id == admin_id  # the Admin's copy stays in English (§15)
    assert "Spam" in (alert.method.text or "")

    # The Member's toasts speak the Chat Language too (§15).
    violation = (await violations(app.session_maker))[0]
    await app.feed(
        group_callback_update(
            admin_id,  # not the restricted Member: the toast is the point
            AppealCallback(chat_id=chat_id, violation_id=violation.id).pack(),
            chat_id=chat_id,
            message_id=notice.result.message_id,
            sender_name="Someone",
        )
    )
    (toast,) = app.session.calls_of("AnswerCallbackQuery")
    assert toast.method.text == "Эта кнопка не для вас"


async def test_the_linked_chat_screen_status_line_shows_mode_backend_and_sensitivity(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(private_callback_update(admin_id, f"chat:{chat_id}", menu, language_code="en"))

    text = app.session.calls_of("EditMessageText")[-1].method.text
    assert text is not None
    assert "Mode: Observation Mode" in text
    assert "Backend: Laya" in text
    assert "Sensitivity: Balanced" in text
