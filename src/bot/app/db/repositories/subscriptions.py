"""Repository for `admin_subscription` rows (§12): who gets which Admin Alerts."""

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
