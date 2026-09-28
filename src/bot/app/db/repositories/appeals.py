"""Repository for `appeal` rows (§8, §12): a Member's request to lift a Restriction.

One Appeal per Violation — the UNIQUE constraint backs it, so the first
insert wins and a second filing finds the row already there. Deciding an
Appeal is first-click-wins (§9): a conditional update fires only while the
Appeal is still `pending`, so of several Admins pressing exactly the first
one decides.
"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Appeal


class AppealRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, violation_id: int, *, created_at: datetime) -> Appeal | None:
        """File the first Appeal for the Violation; None when one already exists."""
        result = await self.session.execute(
            insert(Appeal).values(violation_id=violation_id, created_at=created_at)
            .on_conflict_do_nothing(index_elements=["violation_id"])
            .returning(Appeal.id)
        )
        if result.scalar_one_or_none() is None:
            return None
        appeal = await self.by_violation(violation_id)
        assert appeal is not None  # the insert above just created it
        return appeal

    async def by_violation(self, violation_id: int) -> Appeal | None:
        """The Violation's Appeal, whatever its state."""
        return await self.session.scalar(
            select(Appeal).where(Appeal.violation_id == violation_id)
        )

    async def decide(
        self, appeal_id: int, *, status: str, by: int, at: datetime
    ) -> bool:
        """Decide a pending Appeal (§8): the first click wins (§9).

        The conditional update fires only while the Appeal is still pending,
        so a late click changes nothing and returns False.
        """
        result = await self.session.execute(
            update(Appeal)
            .where(Appeal.id == appeal_id, Appeal.status == "pending")
            .values(status=status, decided_by=by, decided_at=at)
        )
        if not result.rowcount:
            return False
        # The conditional UPDATE bypasses the session's identity map; keep the
        # session's copy of the row in step with what was written.
        row = await self.session.get(Appeal, appeal_id)
        if row is not None:
            row.status = status
            row.decided_by = by
            row.decided_at = at
        await self.session.flush()
        return True
