"""End-to-end: Jev → Laya fallback and backend incidents (§5, §9).

A chat checking with Jev keeps moderating while Jev is down: the check
falls back to Laya, the first failure opens one `backend_incident` and
alerts the subscribed Admins once, and the first Jev success closes the
incident with a "✅ Jev is back" follow-up. With neither backend answering,
the check is `skipped_unavailable`. Assertions only look at the recorded
Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.db.models import BackendIncident, Chat, MessageCheck
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.backend import CLEAN, SPAMMY
from tests.support.harness import TestApp, app_fixture, auto_moderation_chat
from tests.support.telegram import member_member
from tests.support.updates import group_message_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(2600, 10)
_chat_ids = count(-100700, -10)

SPAM_TEXT = "Buy cheap crypto now, DM me https://t.me/+abc"


@pytest.fixture
def admin_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def member_id() -> Iterator[int]:
    yield next(_admin_ids)


@pytest.fixture
def chat_id() -> Iterator[int]:
    yield next(_chat_ids)


@pytest.fixture
async def app(postgres_url: str) -> AsyncIterator[TestApp]:
    from tests.support.backend import FakeBackend

    async with app_fixture(
        postgres_url, backends={"laya": FakeBackend(), "jev": FakeBackend()}
    ) as app:
        yield app


async def use_jev(app: TestApp, chat_id: int) -> None:
    """Switch the linked chat to Jev, the way the backend screen will (§13)."""
    async with app.session_maker() as db:
        chat = await db.get(Chat, chat_id)
        assert chat is not None
        chat.backend = "jev"
        await db.commit()


async def checks_of(session_maker: async_sessionmaker[AsyncSession]) -> list[MessageCheck]:
    async with session_maker() as db:
        return list((await db.execute(select(MessageCheck).order_by(MessageCheck.id))).scalars())


async def incidents_of(session_maker: async_sessionmaker[AsyncSession]) -> list[BackendIncident]:
    async with session_maker() as db:
        return list((await db.execute(select(BackendIncident))).scalars())


async def test_a_jev_failure_falls_back_to_laya_and_alerts_once(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    await use_jev(app, chat_id)
    app.backends["jev"].script_error("authentication failed")
    app.backends["laya"].script(SPAMMY)
    app.session.script(GetChatMember, member_member(user(member_id)))  # not an Admin
    app.session.calls.clear()

    await app.feed(group_message_update(chat_id, member_id, SPAM_TEXT, message_id=77))

    # The failure is noted the moment the check fails; the Violation then ran
    # on Laya's answer: incident alert → delete → restrict → notice →
    # Violation alert (§5, §6, §9).
    assert app.session.call_names() == [
        "GetChatMember",
        "SendMessage",  # the incident alert
        "DeleteMessage",
        "RestrictChatMember",
        "SendMessage",  # the Chat Notice, in the chat
        "SendMessage",  # the Linker's Violation Admin Alert
    ]
    (incident, notice, _violation_alert) = app.session.calls_of("SendMessage")
    assert notice.method.chat_id == chat_id
    assert incident.method.chat_id == admin_id  # the Linker subscribed to All (§9)
    assert incident.method.text == "⚠️ Jev is unavailable: authentication failed. Using Laya"
    assert incident.method.reply_markup is None  # no buttons on an incident alert

    # The check was served by Laya: its backend, model and thresholds (§5).
    (check,) = await checks_of(app.session_maker)
    assert check.outcome == "violation"
    assert check.backend == "laya"
    assert check.model == "multilingual"

    # One open incident for Jev, none for Laya (§5, §12).
    (row,) = await incidents_of(app.session_maker)
    assert row.backend == "jev"
    assert row.reason == "authentication failed"
    assert row.closed_at is None


async def test_a_second_failure_while_open_alerts_not_again(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    await use_jev(app, chat_id)
    app.backends["jev"].script_error("rate limited")
    app.backends["laya"].script(SPAMMY)
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(group_message_update(chat_id, member_id, SPAM_TEXT, message_id=77))
    app.session.calls.clear()
    await app.feed(group_message_update(chat_id, member_id, SPAM_TEXT, message_id=78))

    incident_alerts = [
        call
        for call in app.session.calls_of("SendMessage")
        if (call.method.text or "").startswith("⚠️ Jev is unavailable")
    ]
    assert incident_alerts == []  # the incident is still open: no second alert
    # The second check was served by Laya and was a Violation, so its own
    # Admin Alert still went out (§9) — only the incident alert is deduped.
    violation_alerts = [
        call
        for call in app.session.calls_of("SendMessage")
        if (call.method.text or "").startswith("⚠️ My Chat")
    ]
    assert len(violation_alerts) == 1
    assert len(await incidents_of(app.session_maker)) == 1  # still the one row


async def test_the_first_jev_success_closes_the_incident_and_says_jev_is_back(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    await use_jev(app, chat_id)
    app.backends["jev"].script_error("rate limited")
    app.backends["laya"].script(SPAMMY)
    app.session.script(GetChatMember, member_member(user(member_id)))
    await app.feed(group_message_update(chat_id, member_id, SPAM_TEXT, message_id=77))

    # Jev recovers: the next message is checked by Jev itself.
    app.backends["jev"].script(CLEAN)
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.calls.clear()
    await app.feed(
        group_message_update(chat_id, member_id, "hello friends, how are you all", message_id=78)
    )

    (check_laya, check_jev) = await checks_of(app.session_maker)
    assert check_laya.backend == "laya"
    assert check_jev.backend == "jev"  # served by Jev again
    assert check_jev.outcome == "clean"
    assert check_jev.model == "jev-1.13.0"

    (row,) = await incidents_of(app.session_maker)
    assert row.closed_at is not None  # the first success closed it (§5)

    recovery = [
        call
        for call in app.session.calls_of("SendMessage")
        if (call.method.text or "").startswith("✅")
    ]
    assert len(recovery) == 1
    assert recovery[0].method.chat_id == admin_id
    assert recovery[0].method.text == "✅ Jev is back"


async def test_with_neither_backend_answering_the_check_is_skipped_unavailable(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    await use_jev(app, chat_id)
    app.backends["jev"].script_error("timed out")
    app.backends["laya"].script_error("timed out")
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.calls.clear()

    await app.feed(group_message_update(chat_id, member_id, SPAM_TEXT, message_id=77))

    # Nothing served the check, so no moderation action ran; the only other
    # call is the incident alert itself (the Admins hear about the failure
    # once, even with nothing served — §5).
    assert app.session.call_names() == ["GetChatMember", "SendMessage"]
    (check,) = await checks_of(app.session_maker)
    assert check.outcome == "skipped_unavailable"
    assert check.backend == "jev"  # nothing served it
    assert check.category is None

    # The Jev incident still opened and was alerted — the Admins hear about
    # the failure once, even with nothing served (§5).
    (row,) = await incidents_of(app.session_maker)
    assert row.backend == "jev"
    incident_alerts = [
        call
        for call in app.session.calls_of("SendMessage")
        if (call.method.text or "").startswith("⚠️")
    ]
    assert len(incident_alerts) == 1


async def test_with_laya_unhealthy_the_jev_failure_skips_without_trying_laya(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    await use_jev(app, chat_id)
    app.laya_health.set(deployed=True, healthy=False)
    app.backends["jev"].script_error("timed out")
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.calls.clear()

    await app.feed(group_message_update(chat_id, member_id, SPAM_TEXT, message_id=77))

    # Nothing served the check, so nothing acted on it; the Jev failure was
    # still noted before the skip was recorded (§5).
    assert app.session.call_names() == ["GetChatMember", "SendMessage"]
    (check,) = await checks_of(app.session_maker)
    assert check.outcome == "skipped_unavailable"
    assert app.backends["laya"].calls == []  # Laya was never tried (§5)

    # The Jev incident opened and the subscribed Linker was told once (§5).
    (row,) = await incidents_of(app.session_maker)
    assert row.backend == "jev"
    assert row.reason == "timed out"
    (alert,) = app.session.calls_of("SendMessage")
    assert alert.method.chat_id == admin_id
    assert alert.method.text == "⚠️ Jev is unavailable: timed out. Using Laya"
