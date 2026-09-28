"""Integration: the moderation trail against real Postgres (§12).

Seam: `ModerationRepository`. Recording a Violation must land the §6 maths —
count Active Violations, take the Step, expire on the chat's Expiry — in one
place, and the scheduler queries must be plain due-timestamp reads (§11).
"""

from datetime import timedelta
from itertools import count

from app.clock import FakeClock
from app.db.models import Chat, ChatNotice, FlaggedMessage, MessageCheck
from app.db.repositories.chats import ChatRepository
from app.db.repositories.moderation import ModerationRepository
from sqlalchemy.ext.asyncio import AsyncSession

# The Postgres container is shared across tests, so every test gets its own chat.
_chat_ids = count(-100950, -1)
_user_ids = count(3000, 1)
_check_ids = count(1)

SPAM_PROBABILITIES = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}


async def a_chat(db_session: AsyncSession, clock: FakeClock, **overrides: object) -> Chat:
    chat = await ChatRepository(db_session).create_linked(
        chat_id=next(_chat_ids),
        title="My Chat",
        linker_id=77,
        linked_at=clock.now(),
        chat_language="en",
    )
    for name, value in overrides.items():
        setattr(chat, name, value)
    await db_session.flush()
    return chat


async def a_check(
    repo: ModerationRepository, chat: Chat, user_id: int, when: object
) -> MessageCheck:
    return await repo.record_check(
        chat_id=chat.chat_id,
        user_id=user_id,
        message_id=next(_check_ids),
        backend=chat.backend,
        model="multilingual",
        spec_version=1,
        outcome="clean",
        created_at=when,  # type: ignore[arg-type]
    )


async def test_a_check_row_carries_the_verdict_and_never_text(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    repo = ModerationRepository(db_session)

    check = await repo.record_check(
        chat_id=chat.chat_id,
        user_id=555,
        message_id=10,
        backend="laya",
        model="multilingual",
        spec_version=1,
        outcome="violation",
        category="spam",
        confidence=0.97,
        probabilities=SPAM_PROBABILITIES,
        latency_ms=12,
        created_at=clock.now(),
    )

    stored = await db_session.get(MessageCheck, check.id)
    assert stored is not None
    assert stored.outcome == "violation"
    assert stored.category == "spam"
    assert stored.confidence == 0.97
    assert stored.probabilities == SPAM_PROBABILITIES
    assert stored.latency_ms == 12
    assert stored.created_at == clock.now()
    assert stored.is_edit is False
    assert "text" not in MessageCheck.__table__.columns  # §12: no text, ever


async def test_a_skipped_check_records_no_verdict(db_session: AsyncSession) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    repo = ModerationRepository(db_session)

    check = await repo.record_check(
        chat_id=chat.chat_id,
        user_id=555,
        message_id=11,
        backend="laya",
        model="multilingual",
        spec_version=1,
        outcome="skipped_short",
        created_at=clock.now(),
    )

    stored = await db_session.get(MessageCheck, check.id)
    assert stored is not None
    assert stored.outcome == "skipped_short"
    assert stored.category is None
    assert stored.confidence is None
    assert stored.probabilities is None


async def test_the_first_violation_takes_the_first_step_of_the_default_ladder(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    repo = ModerationRepository(db_session)
    check = await a_check(repo, chat, 555, clock.now())

    violation = await repo.record_violation(
        chat=chat, user_id=555, check_id=check.id, category="spam", now=clock.now()
    )

    assert violation.step_index == 0
    assert violation.restriction_seconds == 3600  # 1 hour
    assert violation.restricted_until == clock.now() + timedelta(hours=1)
    assert violation.expires_at == clock.now() + timedelta(seconds=chat.expiry_seconds or 0)
    assert violation.source == "auto"
    assert violation.revoked_at is None
    assert violation.notice_dropped is False


async def test_active_violations_climb_the_ladder_and_the_last_step_repeats(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    repo = ModerationRepository(db_session)

    steps: list[tuple[int, int]] = []
    for _ in range(4):  # one more Active Violation than the ladder has Steps
        clock.advance(timedelta(seconds=1))
        check = await a_check(repo, chat, 555, clock.now())
        violation = await repo.record_violation(
            chat=chat, user_id=555, check_id=check.id, category="spam", now=clock.now()
        )
        steps.append((violation.step_index, violation.restriction_seconds or 0))

    assert steps == [(0, 3600), (1, 86400), (2, 0), (2, 0)]  # 1h → 24h → forever → forever


async def test_expired_violations_stop_counting_as_active(db_session: AsyncSession) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    repo = ModerationRepository(db_session)
    first = await a_check(repo, chat, 555, clock.now())
    await repo.record_violation(
        chat=chat, user_id=555, check_id=first.id, category="spam", now=clock.now()
    )

    clock.advance(timedelta(days=31))  # past the 30-day Expiry of the first
    second = await a_check(repo, chat, 555, clock.now())
    violation = await repo.record_violation(
        chat=chat, user_id=555, check_id=second.id, category="ads", now=clock.now()
    )

    assert violation.step_index == 0  # the ladder starts over
    assert violation.category == "ads"


async def test_a_member_without_expiry_counts_violations_forever(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock, expiry_seconds=None)  # Expiry: never
    repo = ModerationRepository(db_session)
    first = await a_check(repo, chat, 555, clock.now())
    await repo.record_violation(
        chat=chat, user_id=555, check_id=first.id, category="spam", now=clock.now()
    )

    clock.advance(timedelta(days=3650))
    second = await a_check(repo, chat, 555, clock.now())
    violation = await repo.record_violation(
        chat=chat, user_id=555, check_id=second.id, category="spam", now=clock.now()
    )

    assert violation.step_index == 1  # the first never expired, so it still counts


async def test_forever_restrictions_store_step_zero_and_no_end(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    repo = ModerationRepository(db_session)
    checks = [await a_check(repo, chat, 555, clock.now()) for _ in range(3)]
    for earlier in checks[:2]:  # climb to the last Step first
        await repo.record_violation(
            chat=chat, user_id=555, check_id=earlier.id, category="spam", now=clock.now()
        )

    third = await repo.record_violation(
        chat=chat, user_id=555, check_id=checks[2].id, category="spam", now=clock.now()
    )

    assert third.restriction_seconds == 0  # forever
    assert third.restricted_until is None


async def test_flagged_text_is_stored_becomes_due_and_purges(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    repo = ModerationRepository(db_session)
    check = await a_check(repo, chat, 555, clock.now())
    purge_at = clock.now() + timedelta(days=30)
    await repo.store_flagged(
        check.id,
        text="Buy cheap crypto now",
        entities=[{"type": "url", "offset": 0}],
        purge_at=purge_at,
    )

    due = await repo.due_flagged(clock.now())
    assert due == []  # nothing to purge yet
    later = await repo.due_flagged(purge_at)
    assert [row.check_id for row in later] == [check.id]

    await repo.purge_flagged(check.id)
    stored = await db_session.get(FlaggedMessage, check.id)
    assert stored is not None
    assert stored.text is None  # the text is gone…
    assert stored.entities is None
    assert (await db_session.get(MessageCheck, check.id)) is not None  # …the check stays
    assert await repo.due_flagged(purge_at) == []  # and stays purged


async def test_a_chat_notice_becomes_due_at_its_delete_at(db_session: AsyncSession) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    repo = ModerationRepository(db_session)
    check = await a_check(repo, chat, 555, clock.now())
    violation = await repo.record_violation(
        chat=chat, user_id=555, check_id=check.id, category="spam", now=clock.now()
    )
    notice = await repo.save_notice(
        violation.id, message_id=999, delete_at=clock.now() + timedelta(hours=1)
    )

    assert await repo.due_notices(clock.now()) == []
    assert notice.deleted_at is None

    due = await repo.due_notices(clock.now() + timedelta(hours=1))
    assert due == [(notice, chat.chat_id)]  # the job needs the chat to delete in

    await repo.mark_notice_deleted(violation.id, deleted_at=clock.now() + timedelta(hours=1))
    stored = await db_session.get(ChatNotice, violation.id)
    assert stored is not None and stored.deleted_at is not None
    assert await repo.due_notices(clock.now() + timedelta(hours=2)) == []  # safe to restart
