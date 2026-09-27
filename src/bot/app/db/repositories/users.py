"""Repository for `bot_user` rows (§12)."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BotUser


class BotUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: int) -> BotUser | None:
        return await self.session.get(BotUser, user_id)

    async def get_or_create(self, user_id: int, started_at: datetime) -> BotUser:
        user = await self.get(user_id)
        if user is None:
            user = BotUser(user_id=user_id, started_at=started_at)
            self.session.add(user)
            await self.session.flush()
        return user

    async def all_started(self) -> list[BotUser]:
        rows = await self.session.execute(select(BotUser))
        return list(rows.scalars())
