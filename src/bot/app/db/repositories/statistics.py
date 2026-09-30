"""Repository for the Statistics screen (§13).

Statistics counts one chat's checks, Violations, Suspicions, Appeals and
False Positives over a window — 7 or 30 days — so the two windows never
double-count.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Appeal, MessageCheck, Suspicion, Violation


@dataclass(frozen=True, slots=True)
class ChatStatistics:
    """The §13 Statistics counts for one window: 7 or 30 days."""

    checked: int
    violations_by_category: dict[str, int]
    suspicions: int
    appeals: int
    false_positives: int


class StatisticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def statistics(self, chat_id: int, *, since: datetime) -> ChatStatistics:
        """The Statistics counts for the window since `since` (§13).

        Every count anchors on when it happened: checks, Violations,
        Suspicions and Appeals by their own `created_at`, and the False
        Positives by their `revoked_at` — the lift is when the False
        Positive happened, so one made today counts today however old its
        Violation is. A revoked Violation still counts in its Category —
        the False Positives line is where it shows up.
        """
        checked = await self._count(
            select(func.count())
            .select_from(MessageCheck)
            .where(MessageCheck.chat_id == chat_id, MessageCheck.created_at >= since)
        )
        category_rows = await self.session.execute(
            select(Violation.category, func.count())
            .where(Violation.chat_id == chat_id, Violation.created_at >= since)
            .group_by(Violation.category)
        )
        suspicions = await self._count(
            select(func.count())
            .select_from(Suspicion)
            .where(Suspicion.chat_id == chat_id, Suspicion.created_at >= since)
        )
        appeals = await self._count(
            select(func.count())
            .select_from(Appeal)
            .join(Violation, Appeal.violation_id == Violation.id)
            .where(Violation.chat_id == chat_id, Appeal.created_at >= since)
        )
        false_positives = await self._count(
            select(func.count())
            .select_from(Violation)
            .where(Violation.chat_id == chat_id, Violation.revoked_at >= since)
        )
        return ChatStatistics(
            checked=checked,
            violations_by_category=dict(category_rows.all()),
            suspicions=suspicions,
            appeals=appeals,
            false_positives=false_positives,
        )

    async def _count(self, query: Any) -> int:
        return await self.session.scalar(query) or 0
