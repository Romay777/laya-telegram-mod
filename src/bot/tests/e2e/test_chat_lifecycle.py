"""End-to-end: the chat lifecycle after Linking — Suspended and Removed (§10, §11).

Every transition is driven by a fabricated `my_chat_member` Update or by
the scheduler; assertions only look at the recorded Bot API calls and the
DB state (§17). "Suspension and re-activation" is a named row of §17.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.methods import GetChat, GetChatMember, RestrictChatMember
from aiogram.types import ChatPermissions
from app.db.models import AdminAlert, Chat, Member, MessageCheck, Violation
from app.scheduler import Scheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.harness import (
    FIXED_NOW,
    TestApp,
    app_fixture,
    auto_moderation_chat,
    linked_via_deeplink,
)
from tests.support.telegram import (
    BOT_USER,
    chat_facts,
    member_administrator,
    member_member,
    member_owner,
)
from tests.support.updates import (
    bot_membership_update,
    forwarded_message_update,
    group_message_update,
    private_callback_update,
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


# --- A failed restrictChatMember suspends the chat (§6, §10) -----------------


async def test_a_failed_restriction_suspends_and_stops_the_notice(
    app: TestApp, admin_id: int, chat_id: int, member_id: int
) -> None:
    """restrictChatMember fails for lack of rights: the chat suspends (§10)."""
    from aiogram.exceptions import TelegramBadRequest

    from tests.support.harness import auto_moderation_chat

    await auto_moderation_chat(app, admin_id, chat_id)
    # The sender is a plain Member; the Suspension alert's fan-out later
    # re-checks the Linker's Admin status — in that order.
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.script(GetChatMember, member_administrator(user(admin_id)))
    # The Restriction the pipeline is about to make is refused: the bot
    # does not hold `can_restrict_members` any more.
    app.session.script(
        RestrictChatMember,
        TelegramBadRequest(
            method=RestrictChatMember(
                chat_id=chat_id, user_id=member_id, permissions=ChatPermissions()
            ),
            message="Bad Request: not enough rights to restrict/unrestrict chat member",
        ),
    )

    app.backend.script(SPAMMY)
    await app.feed(
        group_message_update(chat_id, member_id, SPAM_TEXT, message_id=88, sender_name="Spammer")
    )

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.status == "suspended"

    # The message was deleted and the Violation recorded, but no Chat
    # Notice follows — the Suspension alert takes over (§6, §10).
    names = app.session.call_names()
    assert "DeleteMessage" in names
    assert "RestrictChatMember" in names
    assert "SendMessage" not in names or all(
        call.method.chat_id == admin_id for call in app.session.calls_of("SendMessage")
    )
    (alert_call,) = [
        call for call in app.session.calls_of("SendMessage") if call.method.chat_id == admin_id
    ]
    assert "suspended" in alert_call.method.text

    async with app.session_maker() as db:
        (violation,) = (await db.execute(select(Violation))).scalars().all()
    assert violation.chat_id == chat_id

    # The Member signal row got the flagged check before the suspension.
    async with app.session_maker() as db:
        (check,) = (await db.execute(select(MessageCheck))).scalars().all()
    assert check.outcome == "violation"


async def test_an_unrelated_bad_request_does_not_suspend(
    app: TestApp, admin_id: int, chat_id: int, member_id: int
) -> None:
    """A Restriction refused for another reason leaves the chat active (§10)."""
    from aiogram.exceptions import TelegramBadRequest

    from tests.support.harness import auto_moderation_chat

    await auto_moderation_chat(app, admin_id, chat_id)
    # The sender is a plain Member; the pipeline asks before it checks.
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.script(
        RestrictChatMember,
        TelegramBadRequest(
            method=RestrictChatMember(
                chat_id=chat_id, user_id=member_id, permissions=ChatPermissions()
            ),
            message="Bad Request: PARTICIPANT_ID_INVALID",
        ),
    )

    app.backend.script(SPAMMY)
    with pytest.raises(TelegramBadRequest):
        await app.feed(
            group_message_update(
                chat_id, member_id, SPAM_TEXT, message_id=89, sender_name="Spammer"
            )
        )

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.status == "active"


# --- Removal, re-add within the window, and the purge (§10, §11) --------------


async def remove_bot(app: TestApp, admin_id: int, chat_id: int) -> None:
    """The bot is removed from an already-linked chat (§10)."""
    # The removal alert's fan-out re-checks the Linker's Admin status.
    app.session.script(GetChatMember, member_administrator(user(admin_id)))
    await app.feed(
        bot_membership_update(chat_id, "supergroup", linker_id=admin_id, new_status="left")
    )


async def test_removal_records_removed_at_and_alerts_the_linker(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    await linked_via_deeplink(app, admin_id, chat_id)
    app.session.calls.clear()

    await remove_bot(app, admin_id, chat_id)

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.status == "removed"
    assert chat.removed_at == FIXED_NOW

    (alert_call,) = app.session.calls_of("SendMessage")
    assert "was removed" in alert_call.method.text
    assert "30 days" in alert_call.method.text


async def test_re_adding_within_the_window_restores_the_settings(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    """The 30-day retention keeps the row; a re-add brings the settings back (§10)."""
    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.calls.clear()
    await remove_bot(app, admin_id, chat_id)

    # Five days later the bot is re-added by deep link: the intent is minted
    # after the removal, then the promotion update completes the re-link.
    app.clock.advance(timedelta(days=5))
    await app.feed(private_callback_update(admin_id, "menu:add-to-chat:", 5, language_code="en"))
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        bot_membership_update(chat_id, "supergroup", linker_id=admin_id, new_status="administrator")
    )

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.status == "active"
    assert chat.removed_at is None
    assert chat.mode == "auto"  # the settings survived the removal
    assert chat.linker_id == admin_id

    # A home press lists the chat again, and no second "linked" row exists.
    async with app.session_maker() as db:
        chats = (await db.execute(select(Chat))).scalars().all()
    assert [row.chat_id for row in chats] == [chat_id]


async def test_re_adding_by_hand_within_the_window_restores_too(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    """The fallback path re-links a Removed Chat the same way (§10)."""
    from tests.support.harness import started_admin

    await auto_moderation_chat(app, admin_id, chat_id)
    app.session.calls.clear()
    await remove_bot(app, admin_id, chat_id)

    app.clock.advance(timedelta(days=3))
    menu = await started_admin(app, admin_id)
    await app.feed(private_callback_update(admin_id, "menu:add-to-chat:", menu, language_code="en"))
    await app.feed(
        private_callback_update(admin_id, "menu:added-already:", menu, language_code="en")
    )
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", "My Chat"))
    app.session.script(GetChatMember, member_administrator(BOT_USER))
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(
        forwarded_message_update(admin_id, origin_chat_id=chat_id, message_id=9, language_code="en")
    )

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None
    assert chat.status == "active"
    assert chat.mode == "auto"


async def test_the_scheduler_purges_the_chat_after_the_window(
    app: TestApp, admin_id: int, chat_id: int, member_id: int
) -> None:
    """31 days after removal the scheduler deletes the row; cascade takes the rest."""
    await auto_moderation_chat(app, admin_id, chat_id)
    # One Member row exists, so the cascade has something to take.
    async with app.session_maker() as db:
        db.add(Member(chat_id=chat_id, user_id=member_id, first_seen_at=FIXED_NOW))
        await db.commit()

    await remove_bot(app, admin_id, chat_id)

    # Day 29: still kept.
    app.clock.advance(timedelta(days=29))
    await Scheduler(
        bot=app.bot,
        session_maker=app.session_maker,
        clock=app.clock,
        core=app.i18n.core,
    ).run_once()
    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.status == "removed"

    # Day 30+: the purge.
    app.clock.advance(timedelta(days=2))
    await Scheduler(
        bot=app.bot,
        session_maker=app.session_maker,
        clock=app.clock,
        core=app.i18n.core,
    ).run_once()
    assert await stored_chat(app.session_maker, chat_id) is None

    async with app.session_maker() as db:
        members = (await db.execute(select(Member))).scalars().all()
    assert members == []


# --- The Chat screen and 🔵 Check again (§10, §13) ----------------------------


async def suspended_chat(app: TestApp, admin_id: int, chat_id: int) -> int:
    """Link, suspend, and return the suspension alert's message id."""
    await linked_via_deeplink(app, admin_id, chat_id)
    app.session.calls.clear()
    await suspend(app, admin_id, chat_id, can_delete_messages=True)
    # The suspension fan-out answered from the warm cache, so its scripted
    # admin answer is still queued; it must not leak into the next call.
    app.session.drop_scripted(GetChatMember)
    (alert_call,) = [
        call for call in app.session.calls_of("SendMessage") if call.method.chat_id == admin_id
    ]
    return alert_call.result.message_id


async def test_the_chat_screen_shows_the_suspended_state_and_missing_right(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await suspended_chat(app, admin_id, chat_id)
    app.session.calls.clear()

    # The Linker opens the chat from Home; the Linker's own cached admin
    # answer covers the access check, and the bot's membership is read live
    # to name what is missing (§10, §13).
    app.session.script(GetChatMember, member_administrator(BOT_USER, can_restrict_members=False))
    await app.feed(private_callback_update(admin_id, f"chat:{chat_id}", menu, language_code="en"))

    (edit,) = app.session.calls_of("EditMessageText")
    assert "suspended" in edit.method.text
    assert "restrict members" in edit.method.text
    buttons = [button for row in edit.method.reply_markup.inline_keyboard for button in row]
    assert any(
        button.callback_data == f"link-check:{chat_id}" and button.text == "🔵 Check again"
        for button in buttons
    )


async def test_check_again_with_rights_back_reactivates_and_tells_the_linker(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    alert_message_id = await suspended_chat(app, admin_id, chat_id)
    app.session.calls.clear()

    # The rights came back; the press re-checks the bot's membership live.
    app.session.script(GetChatMember, member_administrator(BOT_USER))
    await app.feed(
        private_callback_update(
            admin_id, f"link-check:{chat_id}", alert_message_id, language_code="en"
        )
    )

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.status == "active"

    # The Linker is notified: the suspension copy now shows the all-clear.
    # The Menu message is edited to the Chat screen besides the alert copy.
    edits = app.session.calls_of("EditMessageText")
    assert any("active again" in edit.method.text for edit in edits)


async def test_check_again_while_still_suspended_shows_the_screen(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    alert_message_id = await suspended_chat(app, admin_id, chat_id)
    app.session.calls.clear()

    # The right is still gone: the Chat screen says so.
    app.session.script(GetChatMember, member_administrator(BOT_USER, can_restrict_members=False))
    await app.feed(
        private_callback_update(
            admin_id, f"link-check:{chat_id}", alert_message_id, language_code="en"
        )
    )

    chat = await stored_chat(app.session_maker, chat_id)
    assert chat is not None and chat.status == "suspended"

    (edit,) = app.session.calls_of("EditMessageText")
    assert "suspended" in edit.method.text
    assert "restrict members" in edit.method.text
