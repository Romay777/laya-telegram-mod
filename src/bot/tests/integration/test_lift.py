"""Integration: lifting a Restriction from an Admin Alert (§6, §9).

The first 🟢 Lift restriction click wins through the conditional update,
gives the Member back the chat's default permissions, and edits every
recorded copy of the alert to show who decided — buttons gone. A late
click decides nothing and learns who did.
"""

from itertools import count

import pytest
from aiogram import Bot
from aiogram.methods import GetChat, GetChatMember
from aiogram.types import ChatPermissions
from aiogram_i18n.cores.base import BaseCore
from app.clock import FakeClock
from app.db.models import Violation
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.chats import ChatRepository
from app.db.repositories.moderation import ModerationRepository
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.fake_session import FakeBotSession
from tests.support.i18n import started_core
from tests.support.telegram import chat_facts, member_owner
from tests.support.updates import user

# The Postgres container is shared, so every test gets its own chat and people.
_chat_ids = count(-100780, -1)
_admin = count(50)
LINKER = next(_admin)  # 51
SECOND = next(_admin)  # 52
MEMBER = next(_admin)  # 53

DEFAULT_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)


@pytest.fixture
async def core() -> BaseCore:
    return await started_core()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def a_bot() -> Bot:
    return Bot("42:test-token", session=FakeBotSession())


async def a_violation_with_two_copies(db_session: AsyncSession, clock: FakeClock) -> Violation:
    chat = await ChatRepository(db_session).create_linked(
        chat_id=next(_chat_ids),
        title="My Chat",
        linker_id=LINKER,
        linked_at=clock.now(),
        chat_language="en",
    )
    repo = ModerationRepository(db_session)
    check = await repo.record_check(
        chat_id=chat.chat_id,
        user_id=MEMBER,
        message_id=33,
        backend=chat.backend,
        model="multilingual",
        spec_version=1,
        outcome="violation",
        category="spam",
        created_at=clock.now(),
    )
    violation = await repo.record_violation(
        chat=chat, user_id=MEMBER, check_id=check.id, category="spam", now=clock.now()
    )
    alerts = AlertRepository(db_session)
    await alerts.record_alert(
        chat.chat_id,
        admin_id=LINKER,
        message_id=401,
        subject_type="violation",
        subject_id=violation.id,
    )
    await alerts.record_alert(
        chat.chat_id,
        admin_id=SECOND,
        message_id=402,
        subject_type="violation",
        subject_id=violation.id,
    )
    return violation


async def test_the_first_click_lifts_the_restriction_and_edits_every_copy(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    from app.alerts.lift import lift_violation

    violation = await a_violation_with_two_copies(db_session, clock)
    bot = a_bot()
    bot.session.script(
        GetChat,
        chat_facts(violation.chat_id, "supergroup", permissions=DEFAULT_PERMISSIONS),
    )

    outcome = await lift_violation(
        bot,
        db_session,
        core=core,
        clock=clock,
        chat_id=violation.chat_id,
        violation_id=violation.id,
        admin=user(LINKER, username="alpha"),
        locale="en",
    )

    assert outcome.won is True
    assert outcome.decided_by == "@alpha"

    # The Member is restricted back to the chat's own default permissions (§6).
    (restrict,) = bot.session.calls_of("RestrictChatMember")
    method = restrict.method
    assert (method.chat_id, method.user_id) == (violation.chat_id, MEMBER)
    assert method.permissions.can_send_messages is True
    assert method.until_date == 0  # no timed restriction on the way back

    # Every recorded copy shows the outcome, with its buttons gone (§9).
    edits = bot.session.calls_of("EditMessageText")
    assert [(call.method.chat_id, call.method.message_id) for call in edits] == [
        (LINKER, 401),
        (SECOND, 402),
    ]
    assert all(call.method.text == "✅ Restriction lifted by @alpha" for call in edits)
    assert all(call.method.reply_markup is None for call in edits)

    # The Violation is a False Positive now, recorded with who and when (§6).
    revoked = await db_session.get(Violation, violation.id)
    assert revoked is not None
    assert revoked.revoked_by == LINKER
    assert revoked.revoked_at == clock.now()


async def test_a_late_click_decides_nothing_and_names_the_winner(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    from app.alerts.lift import lift_violation

    violation = await a_violation_with_two_copies(db_session, clock)
    bot = a_bot()
    bot.session.script(
        GetChat,
        chat_facts(violation.chat_id, "supergroup", permissions=DEFAULT_PERMISSIONS),
    )
    await lift_violation(
        bot,
        db_session,
        core=core,
        clock=clock,
        chat_id=violation.chat_id,
        violation_id=violation.id,
        admin=user(LINKER, username="alpha"),
        locale="en",
    )
    bot.session.calls.clear()
    # Naming the winner for the toast reads them from the chat (§9).
    bot.session.script(GetChatMember, member_owner(user(LINKER, username="alpha")))

    outcome = await lift_violation(
        bot,
        db_session,
        core=core,
        clock=clock,
        chat_id=violation.chat_id,
        violation_id=violation.id,
        admin=user(SECOND, username="beta"),
        locale="en",
    )

    assert outcome.won is False
    assert outcome.decided_by == "@alpha"
    assert bot.session.calls_of("RestrictChatMember") == []
    assert bot.session.calls_of("EditMessageText") == []
    assert bot.session.calls_of("GetChat") == []

    revoked = await db_session.get(Violation, violation.id)
    assert revoked is not None
    assert revoked.revoked_by == LINKER  # the first click's record stands


async def test_the_winner_is_named_from_the_bot_api_when_the_row_knows_only_the_id(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    """A revoked_at with no readable member still renders an honest toast."""
    from app.alerts.lift import lift_violation

    violation = await a_violation_with_two_copies(db_session, clock)
    repo = ModerationRepository(db_session)
    await repo.revoke_violation(violation.id, by=999, at=clock.now())  # an unknown Admin

    bot = a_bot()
    bot.session.script(GetChatMember, member_owner(user(999, username="zeta")))

    outcome = await lift_violation(
        bot,
        db_session,
        core=core,
        clock=clock,
        chat_id=violation.chat_id,
        violation_id=violation.id,
        admin=user(SECOND, username="beta"),
        locale="en",
    )

    assert outcome.won is False
    assert outcome.decided_by == "@zeta"
