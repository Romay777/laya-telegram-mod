"""Integration: the Notice Template row per chat (§12, §14).

One template per chat; deleting it returns the chat to the default text.
"""

from itertools import count

from app.clock import FakeClock
from app.db.models import NoticeTemplate
from app.db.repositories.chats import ChatRepository
from app.db.repositories.notice_templates import NoticeTemplateRepository
from sqlalchemy.ext.asyncio import AsyncSession

# The Postgres container is shared across tests, so every test gets its own chat.
_chat_ids = count(-1003000, -10)


async def linked_chat(session: AsyncSession) -> int:
    chat_id = next(_chat_ids)
    await ChatRepository(session).create_linked(
        chat_id=chat_id,
        title="My Chat",
        linker_id=77,
        linked_at=FakeClock().now(),
        chat_language="en",
    )
    return chat_id


async def test_a_chat_without_a_template_has_none(db_session: AsyncSession) -> None:
    assert await NoticeTemplateRepository(db_session).get(await linked_chat(db_session)) is None


async def test_save_then_get_round_trips_text_and_entities(
    db_session: AsyncSession,
) -> None:
    chat_id = await linked_chat(db_session)
    entities = [{"type": "bold", "offset": 0, "length": 5}]

    await NoticeTemplateRepository(db_session).save(
        chat_id, text="{user} broke the rules", entities=entities, updated_by=77
    )

    stored = await NoticeTemplateRepository(db_session).get(chat_id)
    assert isinstance(stored, NoticeTemplate)
    assert stored.text == "{user} broke the rules"
    assert stored.entities == entities
    assert stored.updated_by == 77


async def test_saving_again_replaces_the_one_template_per_chat(
    db_session: AsyncSession,
) -> None:
    chat_id = await linked_chat(db_session)
    repo = NoticeTemplateRepository(db_session)
    await repo.save(chat_id, text="first", entities=[], updated_by=77)

    await repo.save(chat_id, text="second", entities=[], updated_by=88)

    stored = await repo.get(chat_id)
    assert isinstance(stored, NoticeTemplate)
    assert stored.text == "second"
    assert stored.updated_by == 88


async def test_delete_returns_the_chat_to_the_default(db_session: AsyncSession) -> None:
    chat_id = await linked_chat(db_session)
    repo = NoticeTemplateRepository(db_session)
    await repo.save(chat_id, text="custom", entities=[], updated_by=77)

    await repo.delete(chat_id)

    assert await repo.get(chat_id) is None
