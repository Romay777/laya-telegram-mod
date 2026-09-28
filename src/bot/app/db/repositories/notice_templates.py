"""Repository for `notice_template` rows (§12, §14): one template per chat."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock
from app.db.models import NoticeTemplate


class NoticeTemplateRepository:
    def __init__(self, session: AsyncSession, clock: Clock | None = None) -> None:
        self.session = session
        self.clock = clock

    async def get(self, chat_id: int) -> NoticeTemplate | None:
        """The chat's Notice Template, or None when the default text applies (§14)."""
        return await self.session.get(NoticeTemplate, chat_id)

    async def save(
        self,
        chat_id: int,
        *,
        text: str,
        entities: list[dict[str, Any]],
        updated_by: int,
    ) -> NoticeTemplate:
        """Save the Admin's message as received: `text` and `entities` (§14).

        One template applies per chat, so saving replaces the row.
        """
        stored = await self.get(chat_id)
        now = self.clock.now() if self.clock is not None else datetime.now(UTC)
        if stored is None:
            stored = NoticeTemplate(
                chat_id=chat_id, text=text, entities=entities, updated_by=updated_by, updated_at=now
            )
            self.session.add(stored)
        else:
            stored.text = text
            stored.entities = entities
            stored.updated_by = updated_by
            stored.updated_at = now
        await self.session.flush()
        return stored

    async def delete(self, chat_id: int) -> None:
        """Drop the template: the default text in the Chat Language applies (§14)."""
        stored = await self.get(chat_id)
        if stored is not None:
            await self.session.delete(stored)
            await self.session.flush()
