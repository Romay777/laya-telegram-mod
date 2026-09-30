"""End-to-end: the Statistics and Journal screens (§13).

The Chat screen leads to both. Statistics shows the counts for the last 7
or 30 days; the Journal lists Violations newest first, 5 per page, with
◀ ▶ `disabled` at either end; a card shows the facts, the state and the
quote, and its 🟢 Lift restriction is the Admin Alert action (§9) — with
the Menu's own card re-rendered to match.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.methods import GetChat, GetChatMember
from aiogram.types import ChatPermissions
from app.db.models import Violation
from app.scheduler import Scheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.backend import SPAMMY
from tests.support.harness import TestApp, app_fixture, auto_moderation_chat, started_admin
from tests.support.telegram import chat_facts, member_member, member_owner
from tests.support.updates import group_message_update, private_callback_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(1900, 10)
_member_ids = count(8000, 1)
_chat_ids = count(-100700, -10)

DEFAULT_PERMISSIONS = ChatPermissions(can_send_messages=True, can_send_polls=True)


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


async def violations_of(
    session_maker: async_sessionmaker[AsyncSession],
) -> list[Violation]:
    async with session_maker() as db:
        rows = (await db.execute(select(Violation).order_by(Violation.id))).scalars().all()
        return list(rows)


async def seed_violation(app: TestApp, chat_id: int, member_id: int, message_id: int) -> None:
    """One Member's spam message in an Auto-moderation chat: one Violation."""
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(
        group_message_update(
            chat_id, member_id, "Buy my product now", message_id=message_id, sender_name="Spammer"
        )
    )


def all_buttons(edit) -> list:
    return [button for row in edit.reply_markup.inline_keyboard for button in row]


def entry_ids(edit) -> list[int]:
    """The Violation ids behind a Journal screen's entry buttons, in order."""
    return [
        int(button.callback_data.split(":")[2])
        for button in all_buttons(edit)
        if (button.callback_data or "").startswith("violation-card:")
    ]


def pager_of(edit) -> dict:
    (row,) = [
        row for row in edit.reply_markup.inline_keyboard if any(b.text in ("◀", "▶") for b in row)
    ]
    return {button.text: button for button in row}


def card_lift(edit):
    (lift,) = [
        button for button in all_buttons(edit) if (button.callback_data or "").startswith("lift:")
    ]
    return lift


async def test_statistics_shows_the_seeded_counts_and_the_toggle_switches_windows(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(SPAMMY)
    await seed_violation(app, chat_id, member_id, message_id=77)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(private_callback_update(admin_id, f"chat:{chat_id}", menu, language_code="en"))
    app.session.calls.clear()

    await app.feed(
        private_callback_update(admin_id, f"chat-statistics:{chat_id}:", menu, language_code="en")
    )
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "the last 7 days" in (edit.text or "")
    assert "Messages checked: 1" in (edit.text or "")
    assert "Violations: 1" in (edit.text or "")
    assert "Spam: 1" in (edit.text or "")
    assert "Suspicions: 0" in (edit.text or "")
    assert "Appeals: 0" in (edit.text or "")
    assert "False Positives: 0" in (edit.text or "")
    buttons = {button.text: button for button in all_buttons(edit)}
    assert buttons["Last 7 days"].style == "primary"  # the window shown
    assert buttons["Last 30 days"].style is None
    app.session.calls.clear()

    # The toggle switches the window in place (§13).
    await app.feed(
        private_callback_update(admin_id, f"chat-statistics:{chat_id}:30", menu, language_code="en")
    )
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "the last 30 days" in (edit.text or "")
    assert "Messages checked: 1" in (edit.text or "")
    buttons = {button.text: button for button in all_buttons(edit)}
    assert buttons["Last 30 days"].style == "primary"


async def test_the_journal_pages_through_violations_with_disabled_ends(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(SPAMMY)
    for offset in range(7):  # seven Members, seven Violations
        await seed_violation(app, chat_id, next(_member_ids), message_id=71 + offset)
    # Ids climb with creation (the sequence is shared with other tests, so
    # the assertion works from what this chat actually got).
    newest_first = sorted(
        (violation.id for violation in await violations_of(app.session_maker)), reverse=True
    )
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    app.session.calls.clear()

    await app.feed(
        private_callback_update(admin_id, f"journal:{chat_id}:0", menu, language_code="en")
    )
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "Page 1 of 2" in (edit.text or "")
    assert entry_ids(edit) == newest_first[:5]  # newest first (§13)
    pager = pager_of(edit)
    assert pager["◀"].disabled is not None  # nothing before the first page
    assert pager["▶"].callback_data == f"journal:{chat_id}:1"
    app.session.calls.clear()

    await app.feed(
        private_callback_update(admin_id, f"journal:{chat_id}:1", menu, language_code="en")
    )
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert "Page 2 of 2" in (edit.text or "")
    assert entry_ids(edit) == newest_first[5:]
    pager = pager_of(edit)
    assert pager["◀"].callback_data == f"journal:{chat_id}:0"
    assert pager["▶"].disabled is not None  # nothing after the last page
    app.session.calls.clear()

    # ◀ walks back to the first page, where it disables again (§13).
    await app.feed(
        private_callback_update(admin_id, f"journal:{chat_id}:0", menu, language_code="en")
    )
    edit = app.session.calls_of("EditMessageText")[-1].method
    assert entry_ids(edit) == newest_first[:5]
    assert pager_of(edit)["◀"].disabled is not None


async def test_a_card_opens_from_the_journal_and_lifting_updates_it(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(SPAMMY)
    await seed_violation(app, chat_id, member_id, message_id=77)
    (violation,) = await violations_of(app.session_maker)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    app.session.calls.clear()

    # The Journal lists the Violation; tapping it opens the card (§13).
    await app.feed(
        private_callback_update(admin_id, f"journal:{chat_id}:0", menu, language_code="en")
    )
    (journal_edit,) = app.session.calls_of("EditMessageText")
    (entry,) = [
        button
        for button in all_buttons(journal_edit.method)
        if (button.callback_data or "").startswith("violation-card:")
    ]
    app.session.calls.clear()

    await app.feed(private_callback_update(admin_id, entry.callback_data, menu, language_code="en"))
    card = app.session.calls_of("EditMessageText")[-1].method
    assert f"Member: {member_id}" in (card.text or "")
    assert "Spam" in (card.text or "")
    assert "97%" in (card.text or "")
    assert "1 hour" in (card.text or "")  # Step 1 of the default ladder
    assert "Active" in (card.text or "")
    assert (card.text or "").endswith("Buy my product now")  # the quoted text
    lift = card_lift(card)
    assert lift.disabled is None
    app.session.calls.clear()

    # 🟢 Lift restriction is the Admin Alert action (§9), pressed on the Menu.
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", permissions=DEFAULT_PERMISSIONS))
    await app.feed(private_callback_update(admin_id, lift.callback_data, menu, language_code="en"))

    (restrict,) = app.session.calls_of("RestrictChatMember")
    assert (restrict.method.chat_id, restrict.method.user_id) == (chat_id, member_id)
    assert restrict.method.permissions.can_send_messages is True  # default permissions back
    assert restrict.method.until_date == 0

    # The violation alert copy shows the outcome; the Menu's own card is
    # re-rendered with it (§9, §13).
    edits = app.session.calls_of("EditMessageText")
    assert "Restriction lifted by" in (edits[0].method.text or "")
    card = edits[-1].method
    assert "False Positive" in (card.text or "")
    assert card_lift(card).disabled is not None  # already lifted: the button does nothing

    async with app.session_maker() as db:
        revoked = (await db.get(Violation, violation.id)).revoked_at
    assert revoked is not None


async def test_a_card_keeps_its_journal_page_through_a_lift(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(SPAMMY)
    for offset in range(6):  # six Members, six Violations: two Journal pages
        await seed_violation(app, chat_id, next(_member_ids), message_id=81 + offset)
    app.session.script(GetChatMember, member_owner(user(admin_id)))

    # Open the Journal's second page and one card from it (§13).
    await app.feed(
        private_callback_update(admin_id, f"journal:{chat_id}:1", menu, language_code="en")
    )
    (journal_edit,) = app.session.calls_of("EditMessageText")
    (entry,) = [
        button
        for button in all_buttons(journal_edit.method)
        if (button.callback_data or "").startswith("violation-card:")
    ]
    assert entry.callback_data.endswith(":1")  # opened from page 1
    app.session.calls.clear()

    await app.feed(private_callback_update(admin_id, entry.callback_data, menu, language_code="en"))
    card = app.session.calls_of("EditMessageText")[-1].method
    lift = card_lift(card)
    assert lift.callback_data == f"lift:{chat_id}:{entry.callback_data.split(':')[2]}:1"
    app.session.script(GetChat, chat_facts(chat_id, "supergroup", permissions=DEFAULT_PERMISSIONS))
    app.session.calls.clear()

    # The lift re-renders the card on the page it came from (§13).
    await app.feed(private_callback_update(admin_id, lift.callback_data, menu, language_code="en"))
    edits = app.session.calls_of("EditMessageText")
    card = edits[-1].method
    assert "False Positive" in (card.text or "")
    assert f"journal:{chat_id}:1" in [
        button.callback_data for button in all_buttons(card)
    ]  # Back returns to page 1, not the first page


async def test_a_card_whose_text_was_purged_says_so(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    menu = await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(SPAMMY)
    await seed_violation(app, chat_id, member_id, message_id=77)
    (violation,) = await violations_of(app.session_maker)
    app.session.script(GetChatMember, member_owner(user(admin_id)))

    # The retention passes; the scheduler purges the stored text (§11).
    app.clock.advance(timedelta(days=30))
    await Scheduler(
        bot=app.bot, session_maker=app.session_maker, clock=app.clock, core=app.i18n.core
    ).run_once()
    app.session.calls.clear()

    await app.feed(
        private_callback_update(
            admin_id,
            f"violation-card:{chat_id}:{violation.id}:0",
            menu,
            language_code="en",
        )
    )
    card = app.session.calls_of("EditMessageText")[-1].method
    assert "no longer stored" in (card.text or "")
    assert "Buy my product" not in (card.text or "")
    assert "Expired" in (card.text or "")  # a month old: past the 30-day Expiry


async def test_a_stranger_cannot_open_anyones_journal(
    app: TestApp, admin_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    stranger_id = next(_admin_ids)
    app.session.script(GetChatMember, member_member(user(stranger_id)))  # the Home render
    stranger_menu = await started_admin(app, stranger_id)

    await app.feed(
        private_callback_update(
            stranger_id, f"journal:{chat_id}:0", stranger_menu, language_code="en"
        )
    )
    answer = app.session.calls_of("AnswerCallbackQuery")[-1].method
    assert answer.text == "You are no longer an admin of this chat."
