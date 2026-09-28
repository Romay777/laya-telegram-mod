"""Integration: `admin_alert` rows (§9, §12) against real Postgres.

Every alert message the bot sends is recorded — admin, message id, subject —
so that every copy can be edited later, whatever the decision.
"""

from itertools import count

from app.clock import FakeClock
from app.db.models import Chat
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.chats import ChatRepository
from sqlalchemy.ext.asyncio import AsyncSession

# The Postgres container is shared across tests, so every test gets its own chat.
_chat_ids = count(-100890, -1)
_subjects = count(1)


async def a_chat(db_session: AsyncSession, clock: FakeClock) -> Chat:
    return await ChatRepository(db_session).create_linked(
        chat_id=next(_chat_ids),
        title="My Chat",
        linker_id=77,
        linked_at=clock.now(),
        chat_language="en",
    )


async def test_every_sent_alert_is_recorded_and_listed_by_subject(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    repo = AlertRepository(db_session)
    subject_id = next(_subjects)

    first = await repo.record_alert(
        chat.chat_id, admin_id=11, message_id=101, subject_type="violation", subject_id=subject_id
    )
    await repo.record_alert(
        chat.chat_id, admin_id=22, message_id=102, subject_type="violation", subject_id=subject_id
    )

    copies = await repo.alerts_for("violation", subject_id)
    assert [(copy.admin_id, copy.message_id) for copy in copies] == [(11, 101), (22, 102)]
    assert all(copy.chat_id == chat.chat_id for copy in copies)
    assert first.subject_type == "violation"


async def test_alerts_of_other_subjects_are_not_listed(db_session: AsyncSession) -> None:
    clock = FakeClock()
    chat = await a_chat(db_session, clock)
    repo = AlertRepository(db_session)
    subject_id = next(_subjects)

    await repo.record_alert(
        chat.chat_id, admin_id=11, message_id=101, subject_type="violation", subject_id=subject_id
    )
    await repo.record_alert(
        chat.chat_id, admin_id=11, message_id=102, subject_type="appeal", subject_id=subject_id
    )
    await repo.record_alert(
        chat.chat_id,
        admin_id=11,
        message_id=103,
        subject_type="violation",
        subject_id=subject_id + 1,
    )

    copies = await repo.alerts_for("violation", subject_id)
    assert [copy.message_id for copy in copies] == [101]
