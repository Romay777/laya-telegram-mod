"""End-to-end: the auto-moderation tracer (§4-§7, §11).

A Member's spam message in an Auto-moderation chat goes: classifier check →
delete → Violation on Step 1 → Restriction for 1 hour → Chat Notice → the
notice removes itself when the Restriction ends. Assertions only look at
the recorded Bot API calls and the DB state (§17).
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from itertools import count

import pytest
from aiogram.methods import GetChatMember
from app.classifiers.router import CheckSkip
from app.db.models import ChatNotice, FlaggedMessage, MessageCheck, Violation
from app.scheduler import Scheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.backend import CLEAN
from tests.support.harness import FIXED_NOW, TestApp, app_fixture, linked_via_deeplink
from tests.support.telegram import member_member, member_owner
from tests.support.updates import group_message_update, private_callback_update, user

# The Postgres container is shared, so every test gets its own people and chat.
_admin_ids = count(1600, 10)
_chat_ids = count(-100450, -10)

SPAMMY = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}
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
    async with app_fixture(postgres_url) as app:
        yield app


async def auto_moderation_chat(app: TestApp, admin_id: int, chat_id: int) -> int:
    """Link the chat, open it, and arm Auto-moderation on the Mode screen."""
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(private_callback_update(admin_id, f"chat:{chat_id}", menu, language_code="en"))
    await app.feed(
        private_callback_update(admin_id, f"chat-settings:{chat_id}", menu, language_code="en")
    )
    await app.feed(
        private_callback_update(admin_id, f"chat-mode:{chat_id}", menu, language_code="en")
    )
    app.session.calls.clear()
    return menu


async def the_only_check(
    session_maker: async_sessionmaker[AsyncSession],
) -> MessageCheck:
    async with session_maker() as db:
        checks = (await db.execute(select(MessageCheck))).scalars().all()
        assert len(checks) == 1
        return checks[0]


async def fresh(
    session_maker: async_sessionmaker[AsyncSession], model: type, key: object
) -> object:
    """Re-read one row in a fresh session (the pipeline wrote and committed)."""
    async with session_maker() as db:
        return await db.get(model, key)


async def test_a_spam_message_runs_the_whole_violation_sequence(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(SPAMMY)
    app.session.script(GetChatMember, member_member(user(member_id)))  # not an Admin
    app.session.calls.clear()

    await app.feed(
        group_message_update(chat_id, member_id, SPAM_TEXT, message_id=77, sender_name="Spammer")
    )

    # §6 order of actions: delete → restrict → notice; the admin cache asked once.
    assert app.session.call_names() == [
        "GetChatMember",
        "DeleteMessage",
        "RestrictChatMember",
        "SendMessage",
    ]
    (delete,) = app.session.calls_of("DeleteMessage")
    assert (delete.method.chat_id, delete.method.message_id) == (chat_id, 77)

    (restrict,) = app.session.calls_of("RestrictChatMember")
    method = restrict.method
    assert (method.chat_id, method.user_id) == (chat_id, member_id)
    assert all(
        value is False
        for name, value in method.permissions.model_dump().items()
        if name.startswith("can_")
    )  # every can_* permission off (§6)
    assert method.use_independent_chat_permissions is True
    assert method.until_date == int((FIXED_NOW + timedelta(hours=1)).timestamp())  # Step 1: 1 hour

    (sent,) = app.session.calls_of("SendMessage")
    assert sent.method.chat_id == chat_id  # the Chat Notice, in the chat
    assert sent.method.text == (
        "Spammer, it looks like your message looks like spam. You can't write here for 1 hour."
    )

    # DB state: the check row, the flagged text, the Violation, the notice.
    check = await the_only_check(app.session_maker)
    assert check.outcome == "violation"
    assert check.category == "spam"
    assert check.confidence == pytest.approx(0.97)
    assert check.probabilities == SPAMMY
    assert (check.backend, check.model, check.spec_version) == ("laya", "multilingual", 1)
    assert check.latency_ms is not None
    assert check.is_edit is False

    flagged = await fresh(app.session_maker, FlaggedMessage, check.id)
    assert isinstance(flagged, FlaggedMessage)
    assert flagged.text == SPAM_TEXT  # only flagged messages keep their text (§4)
    assert flagged.purge_at == FIXED_NOW + timedelta(days=30)  # flagged_text_days

    async with app.session_maker() as db:
        (violation,) = (await db.execute(select(Violation))).scalars().all()
    assert (violation.chat_id, violation.user_id) == (chat_id, member_id)
    assert violation.step_index == 0  # the first Step of the default ladder
    assert violation.restriction_seconds == 3600
    assert violation.restricted_until == FIXED_NOW + timedelta(hours=1)
    assert violation.expires_at == FIXED_NOW + timedelta(days=30)  # the 30-day Expiry
    assert violation.source == "auto"
    assert violation.revoked_at is None

    notice = await fresh(app.session_maker, ChatNotice, violation.id)
    assert isinstance(notice, ChatNotice)
    assert notice.message_id == sent.result.message_id
    assert notice.delete_at == FIXED_NOW + timedelta(hours=1)  # when the Restriction ends
    assert notice.deleted_at is None

    # The notice removes itself at delete_at, through the scheduler loop (§7, §11).
    app.clock.advance(timedelta(hours=1))
    app.session.calls.clear()
    await Scheduler(bot=app.bot, session_maker=app.session_maker, clock=app.clock).run_once()

    (notice_delete,) = app.session.calls_of("DeleteMessage")
    assert (notice_delete.method.chat_id, notice_delete.method.message_id) == (
        chat_id,
        sent.result.message_id,
    )
    stored_notice = await fresh(app.session_maker, ChatNotice, violation.id)
    assert isinstance(stored_notice, ChatNotice)
    assert stored_notice.deleted_at == FIXED_NOW + timedelta(hours=1)

    # The flagged text stays until its own retention passes (§11).
    assert isinstance(flagged.text, str)
    app.clock.advance(timedelta(days=30))
    await Scheduler(bot=app.bot, session_maker=app.session_maker, clock=app.clock).run_once()
    purged = await fresh(app.session_maker, FlaggedMessage, check.id)
    assert isinstance(purged, FlaggedMessage)
    assert purged.text is None


async def test_a_clean_message_is_recorded_and_left_alone(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(CLEAN)
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.calls.clear()

    await app.feed(
        group_message_update(chat_id, member_id, "Hey, how was your weekend?", message_id=78)
    )

    assert app.session.call_names() == ["GetChatMember"]  # nothing acted on
    check = await the_only_check(app.session_maker)
    assert check.outcome == "clean"
    assert check.category == "clean"
    async with app.session_maker() as db:
        assert (await db.execute(select(FlaggedMessage))).scalars().all() == []
        assert (await db.execute(select(Violation))).scalars().all() == []


async def test_a_backend_overload_is_skipped_not_queued(
    app: TestApp, admin_id: int, member_id: int, chat_id: int
) -> None:
    await auto_moderation_chat(app, admin_id, chat_id)
    app.backend.script(CheckSkip.OVERLOAD)
    app.session.script(GetChatMember, member_member(user(member_id)))
    app.session.calls.clear()

    await app.feed(group_message_update(chat_id, member_id, SPAM_TEXT, message_id=79))

    assert app.session.call_names() == ["GetChatMember"]  # nothing acted on
    check = await the_only_check(app.session_maker)
    assert check.outcome == "skipped_overload"
    assert check.category is None
    async with app.session_maker() as db:
        assert (await db.execute(select(Violation))).scalars().all() == []
