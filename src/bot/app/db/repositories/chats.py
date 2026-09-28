"""Repository for `chat` rows (§12): Linked Chats and their defaults."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category, Chat, ChatCategory


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, chat_id: int) -> Chat | None:
        return await self.session.get(Chat, chat_id)

    async def create_linked(
        self,
        *,
        chat_id: int,
        title: str | None,
        linker_id: int,
        linked_at: datetime,
        chat_language: str,
    ) -> Chat:
        """Insert the Linked Chat with the §12 defaults, or return the stored one.

        Column defaults carry mode, sensitivity, ladder and expiry; the caller
        passes what Linking knows: the chat itself and the Linker. Every
        Category in the table starts enabled (§12: custom Categories are
        reserved for later, so today that is all of them).
        """
        chat = await self.get(chat_id)
        if chat is not None:
            return chat

        chat = Chat(
            chat_id=chat_id,
            title=title,
            linker_id=linker_id,
            linked_at=linked_at,
            chat_language=chat_language,
        )
        self.session.add(chat)
        await self.session.flush()

        for (code,) in (await self.session.execute(select(Category.code))).all():
            self.session.add(ChatCategory(chat_id=chat_id, category_code=code, enabled=True))
        await self.session.flush()
        return chat
