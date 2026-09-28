"""Repository for `admin_subscription` rows (§12): who gets which Admin Alerts."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminSubscription


class AdminSubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set_mode(self, chat_id: int, *, user_id: int, alert_mode: str) -> None:
        await self.session.execute(
            insert(AdminSubscription)
            .values(chat_id=chat_id, user_id=user_id, alert_mode=alert_mode)
            .on_conflict_do_update(
                index_elements=["chat_id", "user_id"],
                set_={"alert_mode": alert_mode},
            )
        )
        await self.session.flush()

    async def get_mode(self, chat_id: int, user_id: int) -> str:
        """The Admin's alert mode for the chat; other Admins default to Off (§9)."""
        row = await self.session.get(AdminSubscription, (chat_id, user_id))
        return row.alert_mode if row is not None else "off"

    async def user_ids_with_mode(self, chat_id: int, *, alert_mode: str) -> list[int]:
        """The Admins subscribed at one mode for the chat, in stable order."""
        return await self.user_ids_with_modes(chat_id, modes=(alert_mode,))

    async def user_ids_with_modes(self, chat_id: int, *, modes: tuple[str, ...]) -> list[int]:
        """The Admins subscribed at any of the modes, in stable order (§9)."""
        rows = await self.session.scalars(
            select(AdminSubscription.user_id)
            .where(AdminSubscription.chat_id == chat_id, AdminSubscription.alert_mode.in_(modes))
            .order_by(AdminSubscription.user_id)
        )
        return list(rows)
