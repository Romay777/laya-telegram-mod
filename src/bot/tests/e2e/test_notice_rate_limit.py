"""End-to-end: Chat Notice rate limiting and violation bursts (§7, §9).

A raid keeps moderation at pace without flooding the chat or the Admins:
deleting and restricting are never delayed, Chat Notices leave through the
per-chat rate-limited queue, stale notices are dropped with no Appeal, and
the Violation alerts burst into one summary per chat per minute.
Assertions only look at the recorded Bot API calls and the DB state (§17);
the FakeClock is moved by the queue's own waiting.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import GetChatMember, SendMessage
from app.classifiers.client import Probabilities
from app.db.models import AdminAlert, Appeal, ChatNotice, Violation
from sqlalchemy import select

from tests.support.harness import FIXED_NOW, TestApp, app_fixture, auto_moderation_chat
from tests.support.telegram import member_member, member_owner
from tests.support.updates import (
    group_callback_update,
    group_message_update,
    private_callback_update,
    user,
)

# The Postgres container is shared, so every test gets its own people and chat.
_admin = count(1700, 100)
_chat_ids = count(-100600, -100)

SPAMMY = Probabilities({"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01})
SPAM_TEXT = "Buy cheap crypto now, DM me https://t.me/+abc"

MEMBERS = 40


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    async with app_fixture(postgres_url) as app:
        yield app


async def raid(
    app: TestApp, admin_id: int, chat_id: int, members: int, *, flush: bool = True
) -> list[int]:
    """Link an Auto-moderation chat and feed `members` spamming Members.

    With `flush` the queue is drained to emptiness before returning; a test
    that gates the queue's waits flushes itself, after releasing the gate.
    """
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(SPAMMY)
    member_ids = [admin_id + offset + 1 for offset in range(members)]
    # The admin cache asks once per Member. The Linker is already cached by
    # the linking presses, so no answer is scripted for them.
    for member_id in member_ids:
        app.session.script(GetChatMember, member_member(user(member_id)))
    for offset, member_id in enumerate(member_ids):
        await app.feed(
            group_message_update(
                chat_id,
                member_id,
                SPAM_TEXT,
                message_id=100 + offset,
                sender_name=f"Spammer {offset}",
            )
        )
    if flush:
        await app.notices.flush()
    return member_ids


async def violations_of(app: TestApp) -> list[Violation]:
    async with app.session_maker() as db:
        return list((await db.execute(select(Violation).order_by(Violation.id))).scalars())


async def test_a_raid_restricts_every_member_at_once_and_groups_the_alerts(
    postgres_url: str, admin_id: int, chat_id: int
) -> None:
    """40 Violations, one per Member: everyone is restricted at once, the
    notices leave at the configured rate, and the alerts burst (§7, §9)."""
    async with app_fixture(postgres_url, notices_per_minute=MEMBERS) as app:
        member_ids = await raid(app, admin_id, chat_id, MEMBERS)

        # Every Member was deleted and restricted, whatever the queue was doing.
        assert len(app.session.calls_of("DeleteMessage")) == MEMBERS
        assert len(app.session.calls_of("RestrictChatMember")) == MEMBERS
        restricted = {call.method.user_id for call in app.session.calls_of("RestrictChatMember")}
        assert restricted == set(member_ids)

        # The notices drained at one per second: 39 one-second waits, and the
        # FakeClock — moved only by the queue's waiting — says so (§7).
        assert app.clock.now() == FIXED_NOW + timedelta(seconds=MEMBERS - 1)
        assert len(app.session.calls_of("SendMessage")) == MEMBERS + 6

        # DB state: 40 recorded Violations, each with its posted notice.
        violations = await violations_of(app)
        assert len(violations) == MEMBERS
        async with app.session_maker() as db:
            notices = list((await db.execute(select(ChatNotice))).scalars())
        assert len(notices) == MEMBERS
        assert all(not violation.notice_dropped for violation in violations)

        # The alerts burst: the first 5 Violations alerted one by one, the rest
        # of the minute is one summary per chat (§9).
        to_admin = [
            call for call in app.session.calls_of("SendMessage") if call.method.chat_id == admin_id
        ]
        assert len(to_admin) == 5 + 1  # five individual alerts, one summary
        (summary,) = [
            call for call in to_admin if "in the last minute in My Chat" in call.method.text
        ]
        assert summary.method.text == "⚡ 1 violation in the last minute in My Chat"
        (button,) = summary.method.reply_markup.inline_keyboard[0]
        assert button.text == "Open journal"

        # The summary stays one message per chat per minute: the later
        # Violations edit it in place, up to the final count (§9).
        edits = [
            call
            for call in app.session.calls_of("EditMessageText")
            if call.method.chat_id == admin_id
        ]
        assert len(edits) == MEMBERS - 6
        assert edits[-1].method.text == "⚡ 35 violations in the last minute in My Chat"

        # The five individual alerts are recorded per Violation; the summary is
        # one recorded alert of its own (§9, §12).
        async with app.session_maker() as db:
            alerts = list((await db.execute(select(AdminAlert))).scalars())
        assert len([a for a in alerts if a.subject_type == "violation"]) == 5
        assert len([a for a in alerts if a.subject_type == "burst"]) == 1

        # The summary's Open journal button opens the chat's Journal (§9, §13).
        # The summary lives in the Admin's private chat, so the press comes
        # from there (§9).
        app.session.calls.clear()
        app.session.script(GetChatMember, member_owner(user(admin_id)))
        await app.feed(
            private_callback_update(
                admin_id, button.callback_data, summary.result.message_id, language_code="en"
            )
        )
        (menu_edit,) = app.session.calls_of("EditMessageText")
        assert menu_edit.method.chat_id == admin_id
        assert "Journal — My Chat" in (menu_edit.method.text or "")


async def test_the_per_minute_limit_holds_sends_back(
    postgres_url: str, admin_id: int, chat_id: int
) -> None:
    """`per_minute` binds in a raid: 40 notices at the §3 default of 18 per
    rolling minute take two window resets, not 39 seconds (§7)."""
    async with app_fixture(postgres_url) as app:  # per_second=1, per_minute=18
        await raid(app, admin_id, chat_id, MEMBERS)

        # 18 notices fill the first minute (0 s..17 s), the queue then waits
        # for the oldest to age out (43 s), and the same happens once more:
        # 17 + 43 + 17 + 43 + 3 seconds in all.
        assert app.clock.now() == FIXED_NOW + timedelta(seconds=123)
        notices_sent = [
            call for call in app.session.calls_of("SendMessage") if call.method.chat_id == chat_id
        ]
        assert len(notices_sent) == MEMBERS
        async with app.session_maker() as db:
            stored = list((await db.execute(select(ChatNotice))).scalars())
        assert len(stored) == MEMBERS


async def test_stale_notices_are_dropped_and_accept_no_appeal(
    postgres_url: str, admin_id: int, chat_id: int
) -> None:
    """A queue that cannot keep up drops what has waited too long: the
    Violation is marked `notice_dropped`, its alert says so, and no Appeal
    can be filed (§7)."""
    # A slow pace — one notice per two seconds — against a one-second queue
    # age: the raid outpaces the queue, and what it cannot post in time is
    # dropped. The gate keeps the queue's paced waits behind the feeding, as
    # real seconds would: the Violation alerts are all out before any wait
    # is slept out (§7).
    gate = asyncio.Event()
    async with app_fixture(
        postgres_url, notices_per_second=0.5, max_queue_age_s=1, notices_gate=gate
    ) as app:
        member_ids = await raid(app, admin_id, chat_id, 2, flush=False)
        violations = await violations_of(app)
        assert len(violations) == 2
        gate.set()
        await app.notices.flush()

        # The first notice left at once (its slot was free). The second's
        # slot came at 2 s, when it had already waited longer than the queue
        # allows — it aged out unposted (§7).
        notices_in_chat = [
            call for call in app.session.calls_of("SendMessage") if call.method.chat_id == chat_id
        ]
        assert len(notices_in_chat) == 1

        first, second = violations
        async with app.session_maker() as db:
            stored = {
                notice.violation_id: notice
                for notice in (await db.execute(select(ChatNotice))).scalars()
            }
            reloaded_second = await db.get(Violation, second.id)
        assert set(stored) == {first.id}  # the other has no notice row
        assert reloaded_second is not None and reloaded_second.notice_dropped is True
        assert not first.notice_dropped

        # The drop appears on that Violation's own Admin Alert.
        note_edits = [
            call
            for call in app.session.calls_of("EditMessageText")
            if call.method.chat_id == admin_id
        ]
        (drop_edit,) = note_edits
        assert drop_edit.method.text.endswith("Notice not sent (rate limit).")
        assert "Spammer 1" in drop_edit.method.text

        # No Appeal can be filed for a dropped Violation (§7): pressing the
        # button is refused with a toast, and no appeal row appears.
        app.session.calls.clear()
        await app.feed(
            group_callback_update(
                member_ids[1],
                f"appeal:{chat_id}:{second.id}",
                message_id=999,  # a notice that does not exist
                chat_id=chat_id,
                sender_name="Spammer 1",
            )
        )
        (answer,) = app.session.calls_of("AnswerCallbackQuery")
        assert answer.method.text == "No appeal is possible: the notice was not sent"
        async with app.session_maker() as db:
            assert list((await db.execute(select(Appeal))).scalars()) == []


async def test_a_429_puts_the_sender_to_sleep_for_retry_after(
    postgres_url: str, admin_id: int, chat_id: int
) -> None:
    """A 429 from Telegram sleeps the sender for `retry_after`, then the
    same notice is retried and posted (§7)."""
    async with app_fixture(postgres_url) as app:
        await auto_moderation_chat(app, admin_id, chat_id)
        app.backend.script(SPAMMY)
        member_id = admin_id + 1
        app.session.script(GetChatMember, member_member(user(member_id)))
        app.session.script(
            SendMessage,
            TelegramRetryAfter(
                method=SendMessage(chat_id=chat_id, text="notice"),
                message="Too Many Requests: retry after 7",
                retry_after=7,
            ),
        )
        await app.feed(group_message_update(chat_id, member_id, SPAM_TEXT, message_id=200))
        await app.notices.flush()

        # The refused attempt, the successful retry, then the Admin Alert.
        sends = app.session.calls_of("SendMessage")
        assert [call.method.chat_id for call in sends] == [chat_id, chat_id, admin_id]
        # The sender slept exactly `retry_after`, and the FakeClock moved with it.
        assert app.clock.now() == FIXED_NOW + timedelta(seconds=7)

        violations = await violations_of(app)
        assert len(violations) == 1
        async with app.session_maker() as db:
            (notice,) = (await db.execute(select(ChatNotice))).scalars()
        assert notice.violation_id == violations[0].id
        assert notice.message_id == sends[1].result.message_id  # the retry posted it
