"""Integration: the appeal repository (§8, §12).

One Appeal per Violation, enforced by the UNIQUE constraint: the first
insert wins, a second returns nothing. Deciding is first-click-wins (§9):
a conditional update fires only while the Appeal is still pending.
"""

from datetime import timedelta
from itertools import count

from app.clock import FakeClock
from app.db.models import Chat
from app.db.repositories.appeals import AppealRepository
from app.db.repositories.chats import ChatRepository
from app.db.repositories.moderation import ModerationRepository
from app.db.repositories.subscriptions import AdminSubscriptionRepository
from sqlalchemy.ext.asyncio import AsyncSession

_chat_ids = count(-100920, -1)
_user_ids = count(4200, 1)
_message_ids = count(1)


async def a_chat(db_session: AsyncSession, clock: FakeClock) -> Chat:
    return await ChatRepository(db_session).create_linked(
        chat_id=next(_chat_ids),
        title="My Chat",
        linker_id=next(_user_ids),
        linked_at=clock.now(),
        chat_language="en",
    )


async def a_violation(db_session: AsyncSession, chat: Chat, clock: FakeClock, user_id: int):
    repo = ModerationRepository(db_session)
    check = await repo.record_check(
        chat_id=chat.chat_id,
        user_id=user_id,
        message_id=next(_message_ids),
        backend=chat.backend,
        model="multilingual",
        spec_version=1,
        outcome="violation",
        category="spam",
        created_at=clock.now(),
    )
    return await repo.record_violation(
        chat=chat, user_id=user_id, check_id=check.id, category="spam", now=clock.now()
    )


async def test_the_first_appeal_is_created_pending_and_a_second_is_refused(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    user_id = next(_user_ids)
    violation = await a_violation(db_session, chat, clock, user_id)
    repo = AppealRepository(db_session)

    appeal = await repo.create(violation.id, created_at=clock.now())

    assert appeal is not None
    assert appeal.violation_id == violation.id
    assert appeal.status == "pending"
    assert appeal.decided_by is None
    assert appeal.decided_at is None
    # One Appeal per Violation (§12): the UNIQUE constraint refuses a second.
    assert await repo.create(violation.id, created_at=clock.now()) is None
    found = await repo.by_violation(violation.id)
    assert found is not None
    assert found.id == appeal.id


async def test_the_first_decision_wins_and_a_late_one_changes_nothing(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    violation = await a_violation(db_session, chat, clock, next(_user_ids))
    repo = AppealRepository(db_session)
    appeal = await repo.create(violation.id, created_at=clock.now())
    assert appeal is not None

    assert await repo.decide(appeal.id, status="approved", by=11, at=clock.now()) is True

    later = clock.now() + timedelta(minutes=5)
    # A late click decides nothing: first click wins (§9).
    assert await repo.decide(appeal.id, status="rejected", by=22, at=later) is False

    decided = await repo.by_violation(violation.id)
    assert decided is not None
    assert decided.status == "approved"
    assert decided.decided_by == 11
    assert decided.decided_at == clock.now()


async def test_appeal_recipients_cover_both_appealing_modes(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    subs = AdminSubscriptionRepository(db_session)
    await subs.set_mode(chat.chat_id, user_id=51, alert_mode="all")
    await subs.set_mode(chat.chat_id, user_id=52, alert_mode="appeals")
    await subs.set_mode(chat.chat_id, user_id=53, alert_mode="off")

    recipients = await subs.user_ids_with_modes(chat.chat_id, modes=("all", "appeals"))

    assert recipients == [51, 52]
