"""Integration: the Linked Chat row the Linking defaults create (§12)."""

from datetime import timedelta
from itertools import count

from app.clock import FakeClock
from app.db.models import AdminSubscription, Chat, ChatCategory
from app.db.repositories.chats import ChatRepository
from app.db.repositories.subscriptions import AdminSubscriptionRepository
from app.domain.linking import DEFAULT_EXPIRY_SECONDS, DEFAULT_LADDER
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# The Postgres container is shared across tests, so every test gets its own chat.
_chat_ids = count(-100900, -10)


async def test_create_linked_applies_the_defaults_of_the_data_model(
    db_session: AsyncSession,
) -> None:
    clock = FakeClock()
    chat_id = next(_chat_ids)
    repo = ChatRepository(db_session)

    chat = await repo.create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=clock.now(),
        chat_language="ru",
    )

    assert chat.chat_id == chat_id
    assert chat.status == "active"
    assert chat.mode == "observation"  # every chat starts in Observation Mode
    assert chat.sensitivity == "balanced"
    assert list(chat.ladder) == list(DEFAULT_LADDER)  # 1 hour → 24 hours → forever
    assert chat.expiry_seconds == DEFAULT_EXPIRY_SECONDS  # 30 days
    assert chat.linker_id == 77
    assert chat.linked_at == clock.now()
    assert chat.removed_at is None


async def test_create_linked_enables_every_builtin_category(db_session: AsyncSession) -> None:
    chat_id = next(_chat_ids)
    await ChatRepository(db_session).create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=FakeClock().now(),
        chat_language="en",
    )

    rows = (
        (await db_session.execute(select(ChatCategory).where(ChatCategory.chat_id == chat_id)))
        .scalars()
        .all()
    )

    assert sorted(row.category_code for row in rows) == ["ads", "insult", "spam"]
    assert all(row.enabled for row in rows)
    assert all(row.violation_threshold is None for row in rows)  # Sensitivity preset wins
    assert all(row.suspicion_threshold is None for row in rows)


async def test_create_linked_twice_keeps_the_existing_chat(db_session: AsyncSession) -> None:
    """The chat is inserted once; a second attempt changes nothing."""
    chat_id = next(_chat_ids)
    repo = ChatRepository(db_session)
    first = await repo.create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=FakeClock().now(),
        chat_language="en",
    )
    first.title = "Renamed"

    second = await repo.create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=88,
        linked_at=FakeClock().now() + timedelta(hours=1),
        chat_language="ru",
    )

    assert second.chat_id == chat_id
    assert second.title == "Renamed"  # the stored row, not a new one
    assert second.linker_id == 77


async def test_get_returns_none_for_an_unlinked_chat(db_session: AsyncSession) -> None:
    assert await ChatRepository(db_session).get(next(_chat_ids)) is None


async def test_the_linkers_subscription_is_set_to_all(db_session: AsyncSession) -> None:
    chat_id = next(_chat_ids)
    await ChatRepository(db_session).create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=FakeClock().now(),
        chat_language="en",
    )
    subscriptions = AdminSubscriptionRepository(db_session)

    await subscriptions.set_mode(chat_id, user_id=77, alert_mode="all")

    row = await db_session.get(AdminSubscription, (chat_id, 77))
    assert row is not None
    assert row.alert_mode == "all"


async def test_set_mode_overwrites_a_previous_subscription(db_session: AsyncSession) -> None:
    chat_id = next(_chat_ids)
    await ChatRepository(db_session).create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=FakeClock().now(),
        chat_language="en",
    )
    subscriptions = AdminSubscriptionRepository(db_session)
    await subscriptions.set_mode(chat_id, user_id=77, alert_mode="all")

    await subscriptions.set_mode(chat_id, user_id=77, alert_mode="off")

    row = await db_session.get(AdminSubscription, (chat_id, 77))
    assert row is not None
    assert row.alert_mode == "off"


async def test_chat_rows_exist_but_are_not_double_created(
    db_session: AsyncSession,
) -> None:
    chat_id = next(_chat_ids)
    repo = ChatRepository(db_session)
    await repo.create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=FakeClock().now(),
        chat_language="en",
    )

    found = await repo.get(chat_id)

    assert isinstance(found, Chat)
    assert found.title == "My Chat"
