"""Integration: forged callback data never reaches another chat (§13).

Every chat-scoped callback re-checks Admin access on the `chat_id` its
callback data carries — but the data is forged text, so the row the button
names must belong to that very chat. An Admin of chat A pressing a button
about chat B's Violation decides nothing, restricts nobody, and edits no
alert copy.
"""

from itertools import count

import pytest
from aiogram import Bot
from aiogram_i18n.cores.base import BaseCore
from app.alerts.appeal import decide_appeal, file_appeal
from app.alerts.fanout import AlertFanout
from app.alerts.lift import lift_violation
from app.alerts.suspicion import decide_suspicion
from app.alerts.unban import unban_channel
from app.clock import FakeClock
from app.db.models import Appeal, Chat, Violation
from app.db.repositories.appeals import AppealRepository
from app.db.repositories.chats import ChatRepository
from app.db.repositories.moderation import ModerationRepository
from app.db.repositories.suspicions import SuspicionRepository
from app.linking.admin_cache import AdminCache
from app.notices.queue import NoticeQueue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.support.fake_session import FakeBotSession
from tests.support.i18n import started_core
from tests.support.updates import user

# The Postgres container is shared, so every test gets its own people and chats.
_admin = count(80)
LINKER = next(_admin)
MEMBER = next(_admin)
_chat_ids = count(-100840, -2)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def core() -> BaseCore:
    return await started_core()


def a_bot() -> Bot:
    return Bot("42:test-token", session=FakeBotSession())


async def two_chats(db_session: AsyncSession, clock: FakeClock) -> tuple[Chat, Chat]:
    """The forger's chat A (they are its Admin) and the victim chat B."""
    maker_chats = ChatRepository(db_session)
    chat_a = await maker_chats.create_linked(
        chat_id=next(_chat_ids),
        title="My Chat",
        linker_id=LINKER,
        linked_at=clock.now(),
        chat_language="en",
    )
    chat_b = await maker_chats.create_linked(
        chat_id=next(_chat_ids),
        title="Other Chat",
        linker_id=LINKER,
        linked_at=clock.now(),
        chat_language="en",
    )
    return chat_a, chat_b


async def a_violation(db_session: AsyncSession, chat: Chat, clock: FakeClock) -> Violation:
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
    return await repo.record_violation(
        chat=chat, user_id=MEMBER, check_id=check.id, category="spam", now=clock.now()
    )


async def test_a_lift_press_from_another_chat_decides_nothing(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    chat_a, chat_b = await two_chats(db_session, clock)
    violation = await a_violation(db_session, chat_b, clock)
    bot = a_bot()

    outcome = await lift_violation(
        bot,
        db_session,
        core=core,
        clock=clock,
        chat_id=chat_a.chat_id,  # the forged callback data names the forger's chat
        violation_id=violation.id,
        admin=user(LINKER, username="alpha"),
        locale="en",
    )

    assert outcome.won is False
    assert bot.session.calls == []  # no GetChat, no RestrictChatMember, no edits
    revoked = await db_session.get(Violation, violation.id)
    assert revoked is not None
    assert revoked.revoked_by is None  # the Violation stands


async def test_an_unban_press_from_another_chat_unbans_nothing(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    chat_a, chat_b = await two_chats(db_session, clock)
    violation = await a_violation(db_session, chat_b, clock)
    bot = a_bot()

    await unban_channel(
        bot,
        db_session,
        core=core,
        chat_id=chat_a.chat_id,
        violation_id=violation.id,
        admin=user(LINKER, username="alpha"),
        locale="en",
    )

    assert bot.session.calls == []  # no UnbanChatSenderChat, no edits


async def test_an_appeal_filed_from_another_chat_files_nothing(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    chat_a, chat_b = await two_chats(db_session, clock)
    violation = await a_violation(db_session, chat_b, clock)
    bot = a_bot()

    outcome = await file_appeal(
        bot,
        db_session,
        core=core,
        clock=clock,
        fanout=AlertFanout(core=core, clock=clock),
        admin_cache=AdminCache(clock=clock, ttl_s=300.0),
        chat=chat_a,  # the forged callback data names the forger's chat
        violation=violation,
        presser=user(MEMBER),
    )

    assert outcome.appeal_id is None
    assert outcome.toast is None  # a forged press stays silent
    assert bot.session.calls == []  # no notice edits, no Appeal alerts
    assert (await db_session.execute(select(Appeal))).scalars().all() == []


async def test_an_appeal_decision_from_another_chat_decides_nothing(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock
) -> None:
    chat_a, chat_b = await two_chats(db_session, clock)
    violation = await a_violation(db_session, chat_b, clock)
    appeal = await AppealRepository(db_session).create(violation.id, created_at=clock.now())
    assert appeal is not None
    bot = a_bot()

    decision = await decide_appeal(
        bot,
        db_session,
        core=core,
        clock=clock,
        chat=chat_a,
        appeal_id=appeal.id,
        approve=True,
        admin=user(LINKER, username="alpha"),
        locale="en",
        outcome_visible_s=600,
    )

    assert decision.won is False
    assert bot.session.calls == []  # no GetChat, no RestrictChatMember, no edits
    decided = await db_session.get(Appeal, appeal.id)
    assert decided is not None
    assert decided.status == "pending"  # the first real Admin click still wins
    standing = await db_session.get(Violation, violation.id)
    assert standing is not None
    assert standing.revoked_by is None


async def test_a_suspicion_decision_from_another_chat_decides_nothing(
    db_session: AsyncSession, core: BaseCore, clock: FakeClock, engine: AsyncEngine
) -> None:
    chat_a, chat_b = await two_chats(db_session, clock)
    repo = ModerationRepository(db_session)
    check = await repo.record_check(
        chat_id=chat_b.chat_id,
        user_id=MEMBER,
        message_id=34,
        backend=chat_b.backend,
        model="multilingual",
        spec_version=1,
        outcome="suspicion",
        category="spam",
        created_at=clock.now(),
    )
    suspicion = await SuspicionRepository(db_session).create(
        check_id=check.id,
        chat_id=chat_b.chat_id,
        user_id=MEMBER,
        message_id=34,
        created_at=clock.now(),
    )
    bot = a_bot()

    decision = await decide_suspicion(
        bot,
        db_session,
        core=core,
        clock=clock,
        fanout=AlertFanout(core=core, clock=clock),
        admin_cache=AdminCache(clock=clock, ttl_s=300.0),
        chat=chat_a,  # the forged callback data names the forger's chat
        suspicion_id=suspicion.id,
        punish=True,
        admin=user(LINKER, username="alpha"),
        locale="en",
        max_notice_lifetime_h=24,
        notices=NoticeQueue(clock=clock),
        session_maker=async_sessionmaker(engine, expire_on_commit=False),
    )

    assert decision.won is False
    assert bot.session.calls == []  # no delete, no RestrictChatMember, no edits
    standing = await SuspicionRepository(db_session).get(suspicion.id)
    assert standing is not None
    assert standing.status == "pending"  # the first real Admin click still wins
