"""Integration: lifting a Restriction in the repository (§6, §9).

Revoking a Violation — the first click of 🟢 Lift restriction — must be a
conditional update (§9: UPDATE … WHERE it still stands), so that only the
first Admin's click wins, and the False Positive must stop counting as
Active on the Penalty Ladder (§6).
"""

from datetime import timedelta
from itertools import count

from app.clock import FakeClock
from app.db.models import Chat
from app.db.repositories.chats import ChatRepository
from app.db.repositories.moderation import ModerationRepository
from sqlalchemy.ext.asyncio import AsyncSession

_chat_ids = count(-100860, -1)
_user_ids = count(3900, 1)
_message_ids = count(1)


async def a_chat(db_session: AsyncSession, clock: FakeClock) -> Chat:
    return await ChatRepository(db_session).create_linked(
        chat_id=next(_chat_ids),
        title="My Chat",
        linker_id=77,
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


async def test_the_first_revoke_wins_and_records_who_and_when(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    user_id = next(_user_ids)
    violation = await a_violation(db_session, chat, clock, user_id)

    repo = ModerationRepository(db_session)
    won = await repo.revoke_violation(violation.id, by=11, at=clock.now())

    assert won is True
    revoked = await db_session.get(type(violation), violation.id)
    assert revoked is not None
    assert revoked.revoked_by == 11
    assert revoked.revoked_at == clock.now()


async def test_a_late_revoke_changes_nothing(db_session: AsyncSession) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    user_id = next(_user_ids)
    violation = await a_violation(db_session, chat, clock, user_id)

    repo = ModerationRepository(db_session)
    assert await repo.revoke_violation(violation.id, by=11, at=clock.now()) is True

    later = clock.now() + timedelta(minutes=5)
    lost = await repo.revoke_violation(violation.id, by=22, at=later)

    assert lost is False
    revoked = await db_session.get(type(violation), violation.id)
    assert revoked is not None
    assert revoked.revoked_by == 11  # the first click's record stands
    assert revoked.revoked_at == clock.now()


async def test_a_revoked_violation_stops_counting_and_the_ladder_starts_over(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    user_id = next(_user_ids)
    repo = ModerationRepository(db_session)

    first = await a_violation(db_session, chat, clock, user_id)
    assert await repo.count_active(chat.chat_id, user_id, clock.now()) == 1

    await repo.revoke_violation(first.id, by=11, at=clock.now())
    assert await repo.count_active(chat.chat_id, user_id, clock.now()) == 0

    second = await a_violation(db_session, chat, clock, user_id)
    assert second.step_index == 0  # the False Positive no longer pushes the ladder
    assert second.restriction_seconds == 3600  # Step 1 again, not Step 2
