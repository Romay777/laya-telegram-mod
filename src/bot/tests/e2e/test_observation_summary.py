"""End-to-end: the Auto-moderation choice and the Observation summary (§13).

Right after Linking the Menu offers the choice: 🟢 Enable auto-moderation
now, or 🔵 Observe for 2 days first — which schedules the one summary the
Linker gets at `observation_summary_at` (§11).
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import Chat, Suspicion
from app.db.repositories.chats import ChatRepository
from app.menu.callbacks import SuspicionDecideCallback
from app.scheduler import Scheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import FIXED_NOW, TestApp, app_fixture, started_admin
from tests.support.telegram import member_administrator, member_member, member_owner
from tests.support.updates import (
    group_message_update,
    my_chat_member_update,
    private_callback_update,
    user,
)

SPAMMY_HIGH = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}
SPAM_TEXT = "Buy cheap crypto now, DM me https://t.me/+abc"

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(1750, 10)
_chat_ids = count(-100700, -10)


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


async def linked_menu(app: TestApp, admin_id: int, chat_id: int) -> int:
    """The Admin started, pressed Add to chat, and the chat was promoted."""
    menu_message_id = await started_admin(app, admin_id)
    await app.feed(
        private_callback_update(admin_id, "menu:add-to-chat:", menu_message_id, language_code="en")
    )
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title="My Chat")
    )
    return menu_message_id


async def stored_chat(session_maker: async_sessionmaker[AsyncSession], chat_id: int) -> Chat | None:
    async with session_maker() as db:
        return await db.get(Chat, chat_id)


async def test_after_linking_the_menu_offers_the_auto_moderation_choice(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    await linked_menu(app, admin_id, chat_id)

    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "✅ My Chat linked" in (edit.text or "")
    (enable,), (observe,), (back,) = edit.reply_markup.inline_keyboard
    assert enable.text == "🟢 Enable auto-moderation now"
    assert enable.callback_data == f"enable-auto:{chat_id}"
    assert enable.style == "success"  # §13: Enable is the confirming action
    assert observe.text == "🔵 Observe for 2 days first"
    assert observe.callback_data == f"observe:{chat_id}"
    assert observe.style == "primary"  # §13: the screen's main action
    assert back.callback_data == "menu:home:"

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.mode == "observation"
    assert chat.observation_summary_at is None  # nothing scheduled before the choice


async def test_observing_for_two_days_schedules_the_summary(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_menu(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))

    await app.feed(
        private_callback_update(admin_id, f"observe:{chat_id}", menu, language_code="en")
    )

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.mode == "observation"
    assert chat.observation_summary_at == FIXED_NOW + timedelta(hours=48)
    # The Menu moves on to the chat's own screen.
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "Mode: Observation Mode" in (edit.text or "")
    assert edit.reply_markup.inline_keyboard != []  # a regular screen again


async def test_enabling_auto_moderation_now_arms_the_chat(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await linked_menu(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))

    await app.feed(
        private_callback_update(admin_id, f"enable-auto:{chat_id}", menu, language_code="en")
    )

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.mode == "auto"
    assert chat.observation_summary_at is None  # no summary was ever scheduled
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "Mode: Auto-moderation" in (edit.text or "")


# --- The one Observation summary at observation_summary_at (§11, §13) -------


async def opt_in_second_admin(app: TestApp, chat_id: int) -> int:
    """A second, real Admin opts into All alerts (§9)."""
    second_id = next(_admin_ids)
    app.session.script(GetChatMember, member_administrator(user(second_id)))
    other_menu = await started_admin(app, second_id)
    for data in (
        f"chat:{chat_id}",
        f"chat-settings:{chat_id}",
        f"chat-alerts:{chat_id}:",
        f"chat-alerts:{chat_id}:all",
    ):
        await app.feed(private_callback_update(second_id, data, other_menu, language_code="en"))
    return second_id


async def scheduler(app: TestApp):
    return Scheduler(
        bot=app.bot, session_maker=app.session_maker, clock=app.clock, core=app.i18n.core
    )


async def test_a_failure_later_in_the_tick_does_not_repeat_the_summary(
    app: TestApp, admin_id: int, chat_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job that raises after the summary must not roll `summary_sent` back (§11).

    The tick's transaction commits the sent summary at once, so even the
    removed-chat purge crashing behind it leaves the offer made — and the
    next tick does not repeat it (§11: sent once, never repeated).
    """
    menu = await linked_menu(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        private_callback_update(admin_id, f"observe:{chat_id}", menu, language_code="en")
    )
    app.session.calls.clear()

    # A chat removed long ago gives the tick's purge job work to do — and the
    # purge raises, the way a real failure would (§10).
    async with app.session_maker() as db:
        db.add(
            Chat(
                chat_id=chat_id - 1,
                title="Old Chat",
                status="removed",
                linker_id=admin_id,
                linked_at=FIXED_NOW - timedelta(days=40),
                removed_at=FIXED_NOW - timedelta(days=31),
            )
        )
        await db.commit()

    async def failing_purge(self: ChatRepository, chat_id: int) -> None:
        raise RuntimeError("the purge failed")

    monkeypatch.setattr(ChatRepository, "purge", failing_purge)

    app.clock.advance(timedelta(hours=48))
    with pytest.raises(RuntimeError):
        await (await scheduler(app)).run_once()

    # The message went out and the mark is durable, crash or no crash (§11).
    (summary,) = app.session.calls_of("SendMessage")
    assert summary.method.chat_id == admin_id
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.summary_sent is True

    app.session.calls.clear()
    with pytest.raises(RuntimeError):
        await (await scheduler(app)).run_once()
    assert app.session.calls == []  # sent once, never repeated (§11)


async def test_the_48_hour_summary_fires_once_and_enabling_arms_the_chat(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    """Link → observe → suspicion → punish → 48h later: one summary, then auto."""
    menu = await linked_menu(app, admin_id, chat_id)
    await opt_in_second_admin(app, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))  # the choice's re-check
    await app.feed(
        private_callback_update(admin_id, f"observe:{chat_id}", menu, language_code="en")
    )

    # During observation a high-confidence hit becomes a Suspicion, and the
    # Linker punishes it (the counts of the summary will show 1 and 1).
    member_id = next(_admin_ids)
    app.backend.script(SPAMMY_HIGH)
    app.session.script(GetChatMember, member_member(user(member_id)))  # the sender
    app.session.calls.clear()
    await app.feed(
        group_message_update(chat_id, member_id, SPAM_TEXT, message_id=91, sender_name="Spammer")
    )
    suspicion_alert = app.session.calls_of("SendMessage")[0]
    async with app.session_maker() as db:
        suspicion = (await db.execute(select(Suspicion))).scalars().one()
    app.session.script(GetChatMember, member_member(user(member_id)))  # the notice's {user}
    await app.feed(
        private_callback_update(
            admin_id,
            SuspicionDecideCallback(chat_id=chat_id, suspicion_id=suspicion.id, punish=True).pack(),
            suspicion_alert.result.message_id,
            language_code="en",
            username="alpha",
        )
    )
    app.session.calls.clear()

    # The summary is not due before 48 hours have passed (§11) — the tick
    # may still do its other jobs (the punished Violation's notice removal).
    app.clock.advance(timedelta(hours=47))
    await (await scheduler(app)).run_once()
    assert app.session.calls_of("SendMessage") == []

    app.clock.advance(timedelta(hours=1))
    await (await scheduler(app)).run_once()

    (summary,) = app.session.calls_of("SendMessage")
    assert summary.method.chat_id == admin_id  # the Linker gets it (§13)
    assert (summary.method.text or "").splitlines() == [
        "📊 My Chat — the last 48 hours",
        "Suspicions: 1",
        "Punished by you: 1",
    ]
    (enable,) = summary.method.reply_markup.inline_keyboard[0]
    assert enable.text == "🟢 Enable auto-moderation"  # §9: the offer repeats once
    assert enable.callback_data == f"enable-auto:{chat_id}"
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.summary_sent is True
    app.session.calls.clear()

    # Sent once, never repeated (§13).
    await (await scheduler(app)).run_once()
    assert app.session.calls == []

    # The Linker takes the offer: the chat arms Auto-moderation (§13).
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        private_callback_update(
            admin_id, f"enable-auto:{chat_id}", menu, language_code="en", username="alpha"
        )
    )
    print(
        "SCRIPTED LEFT:", [type(o).__name__ for o in app.session._scripted.get(GetChatMember, [])]
    )
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.mode == "auto"
