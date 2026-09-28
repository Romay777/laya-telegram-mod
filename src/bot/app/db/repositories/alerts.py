"""Repository for `admin_alert` rows (§12): every sent Admin Alert copy.

A decision must reach every copy of the alert, so each sent message is
recorded with its admin, its message id and its subject, and the copies of
one subject are listed back when that subject is decided (§9).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminAlert


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_alert(
        self, chat_id: int, *, admin_id: int, message_id: int, subject_type: str, subject_id: int
    ) -> AdminAlert:
        alert = AdminAlert(
            chat_id=chat_id,
            admin_id=admin_id,
            message_id=message_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        self.session.add(alert)
        await self.session.flush()
        return alert

    async def alerts_for(self, subject_type: str, subject_id: int) -> list[AdminAlert]:
        """Every recorded copy of one subject, in the order they were sent."""
        rows = await self.session.scalars(
            select(AdminAlert)
            .where(AdminAlert.subject_type == subject_type, AdminAlert.subject_id == subject_id)
            .order_by(AdminAlert.id)
        )
        return list(rows)
