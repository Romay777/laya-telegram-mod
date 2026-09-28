"""Integration: editing the Penalty Ladder and the Expiry (§6, §12).

Seam: `ChatRepository.set_ladder` / `set_expiry`. A change touches only the
`chat` row — existing Violations keep their recorded Step, Restriction and
Expiry, and the change applies from the next Violation (§6).
"""

from datetime import timedelta
from itertools import count

from app.clock import FakeClock
from app.db.models import Chat, Violation
from app.db.repositories.chats import ChatRepository
from app.db.repositories.moderation import ModerationRepository
from app.domain.linking import DEFAULT_EXPIRY_SECONDS, DEFAULT_LADDER
from sqlalchemy.ext.asyncio import AsyncSession

# The Postgres container is shared across tests, so every test gets its own chat.
_chat_ids = count(-100970, -1)
_user_ids = count(3600, 1)
_check_ids = count(1)


async def a_chat(db_session: AsyncSession) -> Chat:
    clock = FakeClock()
    return await ChatRepository(db_session).create_linked(
        chat_id=next(_chat_ids),
        title="My Chat",
        linker_id=77,
        linked_at=clock.now(),
        chat_language="en",
    )


async def a_check(repo: ModerationRepository, chat: Chat, user_id: int, when) -> int:
    check = await repo.record_check(
        chat_id=chat.chat_id,
        user_id=user_id,
        message_id=next(_check_ids),
        backend=chat.backend,
        model="multilingual",
        spec_version=1,
        outcome="violation",
        category="spam",
        created_at=when,
    )
    return check.id


async def test_set_ladder_stores_the_new_steps(db_session: AsyncSession) -> None:
    chat = await a_chat(db_session)
    repo = ChatRepository(db_session)

    await repo.set_ladder(chat.chat_id, (300, 0))

    stored = await repo.get(chat.chat_id)
    assert stored is not None
    assert list(stored.ladder) == [300, 0]


async def test_set_expiry_stores_the_new_period(db_session: AsyncSession) -> None:
    chat = await a_chat(db_session)
    repo = ChatRepository(db_session)

    await repo.set_expiry(chat.chat_id, 7 * 86400)

    stored = await repo.get(chat.chat_id)
    assert stored is not None
    assert stored.expiry_seconds == 7 * 86400


async def test_set_expiry_never_stores_violations_away(db_session: AsyncSession) -> None:
    chat = await a_chat(db_session)
    repo = ChatRepository(db_session)
    moderation = ModerationRepository(db_session)
    check_id = await a_check(moderation, chat, next(_user_ids), FakeClock().now())
    violation = await moderation.record_violation(
        chat=chat, user_id=555, check_id=check_id, category="spam", now=FakeClock().now()
    )
    stored_expiry = violation.expires_at

    await repo.set_expiry(chat.chat_id, None)  # Expiry: never, from now on

    stored = await repo.get(chat.chat_id)
    assert stored is not None and stored.expiry_seconds is None
    row = await db_session.get(Violation, violation.id)
    assert row is not None
    assert row.expires_at == stored_expiry  # the old Violation keeps its own Expiry


async def test_a_ladder_edit_applies_from_the_next_violation(
    db_session: AsyncSession,
) -> None:
    chat = await a_chat(db_session)
    chats = ChatRepository(db_session)
    moderation = ModerationRepository(db_session)
    user_id = next(_user_ids)
    clock = FakeClock()
    check_id = await a_check(moderation, chat, user_id, clock.now())
    first = await moderation.record_violation(
        chat=chat, user_id=user_id, check_id=check_id, category="spam", now=clock.now()
    )

    await chats.set_ladder(chat.chat_id, (300,))

    next_check = await a_check(moderation, chat, user_id, clock.now() + timedelta(hours=1))
    second = await moderation.record_violation(
        chat=chat,
        user_id=user_id,
        check_id=next_check,
        category="spam",
        now=clock.now() + timedelta(hours=1),
    )
    assert first.step_index == 0 and first.restriction_seconds == 3600  # untouched
    assert second.step_index == 0 and second.restriction_seconds == 300  # new ladder
    assert list((await chats.get(chat.chat_id)).ladder) == [300]  # type: ignore[union-attr]


async def test_the_defaults_are_untouched_by_the_editor_module(
    db_session: AsyncSession,
) -> None:
    chat = await a_chat(db_session)
    assert list(chat.ladder) == list(DEFAULT_LADDER)
    assert chat.expiry_seconds == DEFAULT_EXPIRY_SECONDS
