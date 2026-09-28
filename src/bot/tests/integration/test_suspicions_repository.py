"""Integration: the suspicion repository and the scheduler queries (§11, §12).

Suspicions follow the §9 first-click-wins rule of every alert decision: the
conditional update fires only while the Suspicion is still `pending`. The
auto-close and summary jobs are due-timestamp reads, so they are safe
across restarts.
"""

from datetime import datetime, timedelta
from itertools import count

from app.clock import FakeClock
from app.db.models import Chat
from app.db.repositories.chats import ChatRepository
from app.db.repositories.moderation import ModerationRepository
from app.db.repositories.suspicions import SuspicionRepository
from sqlalchemy.ext.asyncio import AsyncSession

_chat_ids = count(-100940, -1)
_user_ids = count(4400, 1)
_message_ids = count(1)


async def a_chat(db_session: AsyncSession, clock: FakeClock) -> Chat:
    return await ChatRepository(db_session).create_linked(
        chat_id=next(_chat_ids),
        title="My Chat",
        linker_id=next(_user_ids),
        linked_at=clock.now(),
        chat_language="en",
    )


async def a_suspicion(
    db_session: AsyncSession,
    chat: Chat,
    clock: FakeClock,
    *,
    user_id: int | None = None,
    message_id: int | None = None,
    created_at: datetime | None = None,
):
    when = created_at if created_at is not None else clock.now()
    repo = ModerationRepository(db_session)
    check = await repo.record_check(
        chat_id=chat.chat_id,
        user_id=user_id or next(_user_ids),
        message_id=message_id or next(_message_ids),
        backend=chat.backend,
        model="multilingual",
        spec_version=1,
        outcome="suspicion",
        category="spam",
        created_at=when,
    )
    return await SuspicionRepository(db_session).create(
        check_id=check.id,
        chat_id=chat.chat_id,
        user_id=check.user_id,
        message_id=check.message_id,
        created_at=when,
    )


async def test_a_suspicion_is_created_pending_with_its_message(db_session: AsyncSession) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    user_id = next(_user_ids)

    suspicion = await a_suspicion(db_session, chat, clock, user_id=user_id, message_id=77)

    assert suspicion.status == "pending"
    assert (suspicion.chat_id, suspicion.user_id) == (chat.chat_id, user_id)
    assert suspicion.message_id == 77
    assert suspicion.decided_by is None
    assert suspicion.decided_at is None
    assert suspicion.created_at == clock.now()


async def test_the_first_decision_wins_and_a_late_one_changes_nothing(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    suspicion = await a_suspicion(db_session, chat, clock)
    repo = SuspicionRepository(db_session)

    assert await repo.decide(suspicion.id, status="punished", by=11, at=clock.now()) is True

    later = clock.now() + timedelta(minutes=5)
    # A late click decides nothing: first click wins (§9).
    assert await repo.decide(suspicion.id, status="dismissed", by=22, at=later) is False

    decided = await repo.get(suspicion.id)
    assert decided is not None
    assert decided.status == "punished"
    assert decided.decided_by == 11
    assert decided.decided_at == clock.now()


async def test_auto_close_only_touches_pending_suspicions(db_session: AsyncSession) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    decided = await a_suspicion(db_session, chat, clock)
    await SuspicionRepository(db_session).decide(
        decided.id, status="dismissed", by=7, at=clock.now()
    )

    # One day later: none of this chat's rows is pending any more (§11).
    later = clock.now() + timedelta(hours=24)
    due = await SuspicionRepository(db_session).due(later, auto_close_h=24)
    assert [row.id for row in due if row.chat_id == chat.chat_id] == []


async def test_a_pending_suspicion_is_due_after_auto_close_h(db_session: AsyncSession) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    suspicion = await a_suspicion(db_session, chat, clock)
    repo = SuspicionRepository(db_session)

    # A minute before `auto_close_h` nothing is due; at the mark it is (§11).
    early = clock.now() + timedelta(hours=24) - timedelta(minutes=1)
    assert [
        row.id for row in await repo.due(early, auto_close_h=24) if row.chat_id == chat.chat_id
    ] == []

    due = await repo.due(clock.now() + timedelta(hours=24), auto_close_h=24)
    assert [row.id for row in due if row.chat_id == chat.chat_id] == [suspicion.id]


async def test_summary_counts_cover_the_window_and_the_punisher(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    repo = SuspicionRepository(db_session)
    linker_id = chat.linker_id
    member_id = next(_user_ids)

    punished = await a_suspicion(db_session, chat, clock, user_id=member_id)
    await repo.decide(punished.id, status="punished", by=linker_id, at=clock.now())
    dismissed = await a_suspicion(db_session, chat, clock, user_id=member_id)
    await repo.decide(dismissed.id, status="dismissed", by=linker_id, at=clock.now())
    await a_suspicion(db_session, chat, clock, user_id=member_id)  # still pending

    # A fourth one, punished by another Admin: it counts as a Suspicion,
    # not as one the Linker punished (§9).
    other = await a_suspicion(db_session, chat, clock, user_id=member_id)
    await repo.decide(other.id, status="punished", by=999, at=clock.now())

    # And a fifth one from before the window: outside the last 48 hours.
    old_clock = FakeClock(clock.now() - timedelta(hours=49))
    await a_suspicion(db_session, chat, old_clock, user_id=member_id)

    total, punished_by_linker = await repo.summary_counts(
        chat.chat_id,
        since=clock.now() - timedelta(hours=48),
        punisher_id=linker_id,
    )

    assert total == 4
    assert punished_by_linker == 1


async def test_due_summaries_pick_up_due_observation_chats_once(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chats = ChatRepository(db_session)
    due_chat = await a_chat(db_session, clock)
    due_chat.observation_summary_at = clock.now()
    await a_chat(db_session, clock)  # linked, but never scheduled: no summary
    auto_chat = await a_chat(db_session, clock)
    auto_chat.mode = "auto"
    sent_chat = await a_chat(db_session, clock)
    sent_chat.observation_summary_at = clock.now()
    sent_chat.summary_sent = True
    later_chat = await a_chat(db_session, clock)
    later_chat.observation_summary_at = clock.now() + timedelta(hours=1)
    await db_session.flush()

    due = await chats.due_observation_summaries(clock.now())

    assert [chat.chat_id for chat in due] == [due_chat.chat_id]
