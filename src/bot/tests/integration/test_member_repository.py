"""The `member` rows (§12): first-seen, checked and flagged counts (§4 step 5).

Seam: `MemberRepository`. The pipeline reads one Member row per check to
compute the §3 signals, and bumps its counters as messages are checked and
flagged.
"""

from datetime import UTC, datetime, timedelta
from itertools import count

from app.clock import FakeClock
from app.db.models import Chat, Member
from app.db.repositories.chats import ChatRepository
from app.db.repositories.members import MemberRepository
from sqlalchemy.ext.asyncio import AsyncSession

# The Postgres container is shared across tests, so every test gets its own chat.
_chat_ids = count(-101050, -1)


async def a_chat(db_session: AsyncSession) -> Chat:
    return await ChatRepository(db_session).create_linked(
        chat_id=next(_chat_ids),
        title="My Chat",
        linker_id=77,
        linked_at=datetime.now(UTC),
        chat_language="en",
    )


async def test_a_first_check_creates_the_row_with_first_seen_and_one_check(
    db_session: AsyncSession,
) -> None:
    chat = await a_chat(db_session)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    seen = await MemberRepository(db_session).note_checked(
        chat.chat_id, 7001, flagged=False, at=now
    )

    assert seen == now
    member = await db_session.get(Member, {"chat_id": chat.chat_id, "user_id": 7001})
    assert member is not None
    assert member.first_seen_at == now
    assert member.checked_count == 1
    assert member.flagged_count == 0


async def test_later_checks_bump_the_counter_and_keep_first_seen(
    db_session: AsyncSession,
) -> None:
    chat = await a_chat(db_session)
    repo = MemberRepository(db_session)
    first = await repo.note_checked(
        chat.chat_id, 7002, flagged=False, at=datetime(2026, 1, 1, tzinfo=UTC)
    )

    seen = await repo.note_checked(chat.chat_id, 7002, flagged=False, at=first + timedelta(hours=5))

    assert seen == first  # the first-seen timestamp never moves
    member = await db_session.get(Member, {"chat_id": chat.chat_id, "user_id": 7002})
    assert member is not None
    assert member.checked_count == 2


async def test_a_flagged_check_bumps_flagged_count(db_session: AsyncSession) -> None:
    chat = await a_chat(db_session)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repo = MemberRepository(db_session)
    await repo.note_checked(chat.chat_id, 7003, flagged=False, at=now)
    await repo.note_checked(chat.chat_id, 7003, flagged=True, at=now)

    member = await db_session.get(Member, {"chat_id": chat.chat_id, "user_id": 7003})
    assert member is not None
    assert member.checked_count == 2
    assert member.flagged_count == 1


async def test_the_row_is_read_back_for_the_signal_facts(db_session: AsyncSession) -> None:
    chat = await a_chat(db_session)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repo = MemberRepository(db_session)
    await repo.note_checked(chat.chat_id, 7004, flagged=True, at=now)

    facts = await repo.facts(chat.chat_id, 7004)

    assert facts is not None
    assert facts.first_seen_at == now
    assert facts.checked_count == 1
    assert facts.flagged_count == 1


async def test_a_member_who_never_spoke_has_no_facts(db_session: AsyncSession) -> None:
    chat = await a_chat(db_session)
    assert await MemberRepository(db_session).facts(chat.chat_id, 999999) is None


async def test_members_of_two_chats_are_tracked_separately(db_session: AsyncSession) -> None:
    first_chat, second_chat = await a_chat(db_session), await a_chat(db_session)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repo = MemberRepository(db_session)
    clock = FakeClock(now)
    await repo.note_checked(first_chat.chat_id, 7005, flagged=False, at=clock.now())

    # The same user in another chat is a different Member row (§12 keys by chat).
    assert await repo.facts(second_chat.chat_id, 7005) is None
    assert await repo.facts(first_chat.chat_id, 7005) is not None
