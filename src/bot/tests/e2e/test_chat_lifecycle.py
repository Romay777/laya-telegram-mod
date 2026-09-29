"""End-to-end: the chat lifecycle after Linking — Suspended and Removed (§10, §11).

Every transition is driven by a fabricated `my_chat_member` Update or by
the scheduler; assertions only look at the recorded Bot API calls and the
DB state (§17). "Suspension and re-activation" is a named row of §17.
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import AdminAlert, Chat, MessageCheck
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import (
    TestApp,
    app_fixture,
    linked_via_deeplink,
)
from tests.support.telegram import member_administrator
from tests.support.updates import (
    bot_membership_update,
    group_message_update,
    user,
)

# The Postgres container is shared, so every test gets its own Admin and chat.
_admin_ids = count(3000, 10)
_chat_ids = count(-100300, -10)
_member_ids = count(3500, 5)

SPAMMY = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}
SPAM_TEXT = "Buy cheap crypto now, DM me https://t.me/+abc"


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def member_id() -> Iterator[int]:
    yield next(_member_ids)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        yield app


async def stored_chat(session_maker: async_sessionmaker[AsyncSession], chat_id: int) -> Chat | None:
    async with session_maker() as db:
        return await db.get(Chat, chat_id)


async def lifecycle_alerts(
    session_maker: async_sessionmaker[AsyncSession], chat_id: int
) -> list[AdminAlert]:
    async with session_maker() as db:
        rows = (
            (
                await db.execute(
                    select(AdminAlert).where(
                        AdminAlert.subject_type == "lifecycle",
                        AdminAlert.subject_id == chat_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def suspend(
    app: TestApp,
    admin_id: int,
    chat_id: int,
    *,
    can_delete_messages: bool = False,
    can_restrict_members: bool = False,
) -> None:
    """The bot's rights change to exactly what the caller says (§10)."""
    # The alert fan-out re-checks that the Linker is still an Admin (§10).
    app.session.script(GetChatMember, member_administrator(user(admin_id)))
    await app.feed(
        bot_membership_update(
            chat_id,
            "supergroup",
            linker_id=admin_id,
            new_status="administrator",
            can_delete_messages=can_delete_messages,
            can_restrict_members=can_restrict_members,
        )
    )


# --- Suspension by my_chat_member, recovery by my_chat_member (§10) ---------


async def test_losing_a_required_right_suspends_and_alerts_the_linker(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    await linked_via_deeplink(app, admin_id, chat_id)
    app.session.calls.clear()

    await suspend(app, admin_id, chat_id, can_delete_messages=True)

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.status == "suspended"

    # The Linker gets an Admin Alert naming the missing right, with a
    # 🔵 Check again button (§9, §10).
    (alert_call,) = app.session.calls_of("SendMessage")
    assert "suspended" in alert_call.method.text
    assert "restrict members" in alert_call.method.text
    (button,) = alert_call.method.reply_markup.inline_keyboard[0]
    assert button.callback_data == f"link-check:{chat_id}"

    copies = await lifecycle_alerts(app.session_maker, chat_id)
    assert len(copies) == 1
    assert copies[0].admin_id == admin_id


async def test_both_rights_lost_are_listed_in_order(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    await linked_via_deeplink(app, admin_id, chat_id)
    app.session.calls.clear()

    await suspend(app, admin_id, chat_id)

    (alert_call,) = app.session.calls_of("SendMessage")
    assert "delete messages, restrict members" in alert_call.method.text


async def test_demotion_suspends_too(app: TestApp, admin_id: int, chat_id: int) -> None:
    await linked_via_deeplink(app, admin_id, chat_id)
    # The alert fan-out re-checks that the Linker is still an Admin (§10).
    app.session.script(GetChatMember, member_administrator(user(admin_id)))
    await app.feed(
        bot_membership_update(chat_id, "supergroup", linker_id=admin_id, new_status="member")
    )

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.status == "suspended"


async def test_rights_restored_update_reactivates_and_edits_the_alert(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    await linked_via_deeplink(app, admin_id, chat_id)
    await suspend(app, admin_id, chat_id, can_delete_messages=True)
    app.session.calls.clear()

    # The rights come back: Telegram says the bot is an administrator again.
    await app.feed(
        bot_membership_update(chat_id, "supergroup", linker_id=admin_id, new_status="administrator")
    )

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.status == "active"

    # The Linker is notified: the Suspension copy is edited to the all-clear.
    (edit,) = app.session.calls_of("EditMessageText")
    assert "active again" in edit.method.text


async def test_a_suspended_chat_checks_nothing(
    app: TestApp, admin_id: int, chat_id: int, member_id: int
) -> None:
    """Checks stop while suspended (§10): no Bot API moderation, no check row."""
    await linked_via_deeplink(app, admin_id, chat_id)
    await suspend(app, admin_id, chat_id, can_delete_messages=True)
    app.session.calls.clear()

    app.backend.script(SPAMMY)
    await app.feed(
        group_message_update(chat_id, member_id, SPAM_TEXT, message_id=77, sender_name="Spammer")
    )

    assert app.session.calls_of("DeleteMessage") == []
    assert app.session.calls_of("RestrictChatMember") == []
    async with app.session_maker() as db:
        checks = (await db.execute(select(MessageCheck))).scalars().all()
    assert checks == []
