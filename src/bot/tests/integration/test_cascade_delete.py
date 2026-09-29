"""Integration: the cascade delete of a purged Removed Chat (§10, §11, §12).

Every chat-scoped table carries `chat_id` with ON DELETE CASCADE
(ADR-0001), so deleting the `chat` row leaves nothing behind. This test
seeds one row in *every* chat-scoped table — the direct children plus the
grandchildren reached through `message_check` and `violation` — purges the
chat, and asserts each table is empty.
"""

from datetime import timedelta
from itertools import count

from app.clock import FakeClock
from app.db.models import (
    AdminAlert,
    AdminSubscription,
    Appeal,
    BotUser,
    Category,
    Chat,
    ChatCategory,
    ChatNotice,
    FlaggedMessage,
    Member,
    MessageCheck,
    NoticeTemplate,
    Suspicion,
    Violation,
)
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.appeals import AppealRepository
from app.db.repositories.chats import ChatRepository
from app.db.repositories.members import MemberRepository
from app.db.repositories.moderation import ModerationRepository
from app.db.repositories.notice_templates import NoticeTemplateRepository
from app.db.repositories.subscriptions import AdminSubscriptionRepository
from app.db.repositories.suspicions import SuspicionRepository
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

# The Postgres container is shared across tests, so every test gets its own chat.
_chat_ids = count(-100980, -1)
_user_ids = count(4000, 1)

SPAM_PROBABILITIES = {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01}

#: The chat's transitive closure, table by table: everything a purge must take.
CHAT_SCOPED_TABLES = (
    ChatCategory,
    NoticeTemplate,
    AdminSubscription,
    Member,
    MessageCheck,
    FlaggedMessage,
    Suspicion,
    Violation,
    ChatNotice,
    Appeal,
    AdminAlert,
)


async def seed_every_chat_scoped_row(db_session: AsyncSession) -> Chat:
    """One Linked Chat with a full moderation trail and a purge-due clock."""
    clock = FakeClock()
    chat_id = next(_chat_ids)
    user_id = next(_user_ids)
    chat = await ChatRepository(db_session).create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=user_id,
        linked_at=clock.now(),
        chat_language="en",
    )
    db_session.add(BotUser(user_id=user_id, started_at=clock.now()))
    await db_session.flush()

    await AdminSubscriptionRepository(db_session).set_mode(
        chat_id, user_id=user_id, alert_mode="all"
    )
    await NoticeTemplateRepository(db_session).save(
        chat_id, text="text", entities=[], updated_by=user_id
    )
    await MemberRepository(db_session).note_checked(chat_id, user_id, flagged=False, at=clock.now())

    moderation = ModerationRepository(db_session)
    check = await moderation.record_check(
        chat_id=chat_id,
        user_id=user_id,
        message_id=10,
        backend=chat.backend,
        model="multilingual",
        spec_version=1,
        outcome="violation",
        category="spam",
        confidence=0.97,
        probabilities=SPAM_PROBABILITIES,
        created_at=clock.now(),
    )
    await moderation.store_flagged(
        check.id, text="spammy", entities=[], purge_at=clock.now() + timedelta(days=30)
    )
    violation = await moderation.record_violation(
        chat=chat, user_id=user_id, check_id=check.id, category="spam", now=clock.now()
    )
    await moderation.save_notice(
        violation_id=violation.id,
        message_id=11,
        delete_at=clock.now() + timedelta(hours=1),
    )
    suspicion = await SuspicionRepository(db_session).create(
        check_id=check.id,
        chat_id=chat_id,
        user_id=user_id,
        message_id=10,
        created_at=clock.now(),
    )
    await AlertRepository(db_session).record_alert(
        chat_id,
        admin_id=user_id,
        message_id=12,
        subject_type="suspicion",
        subject_id=suspicion.id,
    )
    await AppealRepository(db_session).create(violation_id=violation.id, created_at=clock.now())
    return chat


async def test_purging_a_chat_cascades_into_every_chat_scoped_table(
    db_session: AsyncSession,
) -> None:
    chat = await seed_every_chat_scoped_row(db_session)

    # Nothing is empty before the purge: the test seeds what it claims to.
    for table in (*CHAT_SCOPED_TABLES, Chat):
        assert await db_session.scalar(select(func.count()).select_from(table)) > 0

    await ChatRepository(db_session).purge(chat.chat_id)

    for table in (*CHAT_SCOPED_TABLES, Chat):
        count = await db_session.scalar(select(func.count()).select_from(table))
        assert count == 0, f"{table.__tablename__} survived the cascade delete"


async def test_purging_leaves_the_category_seed_and_other_chats_alone(
    db_session: AsyncSession,
) -> None:
    chat = await seed_every_chat_scoped_row(db_session)
    other_id = next(_chat_ids)
    other = await ChatRepository(db_session).create_linked(
        chat_id=other_id,
        title="Other",
        linker_id=next(_user_ids),
        linked_at=FakeClock().now(),
        chat_language="en",
    )

    await ChatRepository(db_session).purge(chat.chat_id)

    # The seeded builtin Categories stay, and the other chat keeps its rows.
    codes = await db_session.scalars(select(Category.code))
    assert sorted(code for code in codes) == ["ads", "insult", "spam"]
    assert await db_session.get(Chat, other.chat_id) is not None
    assert (await db_session.get(Chat, other.chat_id)).status == "active"


async def test_the_seed_cleans_up_after_itself(db_session: AsyncSession) -> None:
    """The seeding helper is shared: a wipe keeps the container fresh for the next test."""
    chat = await seed_every_chat_scoped_row(db_session)

    async with db_session.begin_nested():
        await db_session.execute(delete(Chat).where(Chat.chat_id == chat.chat_id))

    for table in (*CHAT_SCOPED_TABLES, Chat):
        count = await db_session.scalar(select(func.count()).select_from(table))
        assert count == 0, f"{table.__tablename__} survived the cascade delete"
