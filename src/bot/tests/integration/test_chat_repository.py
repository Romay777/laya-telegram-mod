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


async def test_enabled_categories_lists_every_builtin_in_spec_order_by_default(
    db_session: AsyncSession,
) -> None:
    chat_id = next(_chat_ids)
    await ChatRepository(db_session).create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=FakeClock().now(),
        chat_language="en",
    )

    assert await ChatRepository(db_session).enabled_categories(chat_id) == (
        "spam",
        "ads",
        "insult",
    )


async def test_disabling_a_category_takes_it_out_of_enabled_categories(
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

    await repo.set_category_enabled(chat_id, "ads", enabled=False)

    assert await repo.enabled_categories(chat_id) == ("spam", "insult")
    row = await db_session.get(ChatCategory, (chat_id, "ads"))
    assert row is not None and row.enabled is False  # the toggle is stored, not removed
    other = await db_session.get(ChatCategory, (chat_id, "spam"))
    assert other is not None and other.enabled is True


async def test_re_enabling_a_category_puts_it_back(db_session: AsyncSession) -> None:
    chat_id = next(_chat_ids)
    repo = ChatRepository(db_session)
    await repo.create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=FakeClock().now(),
        chat_language="en",
    )
    await repo.set_category_enabled(chat_id, "insult", enabled=False)

    await repo.set_category_enabled(chat_id, "insult", enabled=True)

    assert await repo.enabled_categories(chat_id) == ("spam", "ads", "insult")


async def test_set_sensitivity_stores_the_preset_choice(db_session: AsyncSession) -> None:
    chat_id = next(_chat_ids)
    repo = ChatRepository(db_session)
    await repo.create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=FakeClock().now(),
        chat_language="en",
    )

    await repo.set_sensitivity(chat_id, "strict")

    chat = await repo.get(chat_id)
    assert chat is not None and chat.sensitivity == "strict"


async def test_set_chat_language_stores_the_chat_language(db_session: AsyncSession) -> None:
    chat_id = next(_chat_ids)
    repo = ChatRepository(db_session)
    await repo.create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=FakeClock().now(),
        chat_language="en",
    )

    await repo.set_chat_language(chat_id, "ru")

    chat = await repo.get(chat_id)
    assert chat is not None and chat.chat_language == "ru"


# --- The chat lifecycle (§10): Suspended, Removed, and the way back ----------


async def linked_chat(db_session: AsyncSession) -> tuple[ChatRepository, int]:
    chat_id = next(_chat_ids)
    await ChatRepository(db_session).create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=FakeClock().now(),
        chat_language="en",
    )
    return ChatRepository(db_session), chat_id


async def test_mark_suspended_and_mark_active_set_the_status(db_session: AsyncSession) -> None:
    repo, chat_id = await linked_chat(db_session)

    await repo.mark_suspended(chat_id)
    assert (await repo.get(chat_id)).status == "suspended"

    await repo.mark_active(chat_id)
    chat = await repo.get(chat_id)
    assert chat.status == "active"
    assert chat.removed_at is None


async def test_mark_removed_records_when_the_bot_left(db_session: AsyncSession) -> None:
    repo, chat_id = await linked_chat(db_session)
    now = FakeClock().now()

    await repo.mark_removed(chat_id, removed_at=now)

    chat = await repo.get(chat_id)
    assert chat.status == "removed"
    assert chat.removed_at == now


async def test_due_removed_chats_lists_only_past_the_retention(db_session: AsyncSession) -> None:
    repo, chat_id = await linked_chat(db_session)
    removed_at = FakeClock().now()
    await repo.mark_removed(chat_id, removed_at=removed_at)
    later = ChatRepository(db_session)
    fresh_id = next(_chat_ids)
    await later.create_linked(
        chat_id=fresh_id,
        title="Fresh",
        linker_id=77,
        linked_at=removed_at,
        chat_language="en",
    )
    await later.mark_removed(fresh_id, removed_at=removed_at)

    due_at_29_days = await repo.due_removed_chats(
        removed_at + timedelta(days=29), removed_chat_days=30
    )
    assert due_at_29_days == []

    due_at_30_days = await repo.due_removed_chats(
        removed_at + timedelta(days=30), removed_chat_days=30
    )
    assert sorted(chat.chat_id for chat in due_at_30_days) == sorted([chat_id, fresh_id])


async def test_purge_deletes_the_chat_row(db_session: AsyncSession) -> None:
    repo, chat_id = await linked_chat(db_session)

    await repo.purge(chat_id)

    assert await repo.get(chat_id) is None


async def test_relinking_a_removed_chat_within_the_window_restores_it(
    db_session: AsyncSession,
) -> None:
    """The row is kept for 30 days, so a re-add brings the settings back (§10)."""
    repo, chat_id = await linked_chat(db_session)
    stored = await repo.get(chat_id)
    stored.mode = "auto"
    stored.sensitivity = "strict"
    removed_at = FakeClock().now()
    await repo.mark_removed(chat_id, removed_at=removed_at)
    repo.session.expire_all()  # read back from the DB, not the session cache

    restored = await repo.create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=88,
        linked_at=removed_at + timedelta(days=5),
        chat_language="en",
    )

    assert restored.status == "active"
    assert restored.mode == "auto"  # the previous settings survive
    assert restored.sensitivity == "strict"
    assert restored.removed_at is None
    assert restored.linker_id == 88  # the new Linker takes over


async def test_create_linked_keeps_an_active_chat_as_is(db_session: AsyncSession) -> None:
    repo, chat_id = await linked_chat(db_session)

    again = await repo.create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=FakeClock().now(),
        chat_language="en",
    )

    assert again.status == "active"
    assert again.removed_at is None
