"""Repository for `suspicion` rows (§12): the middle band awaiting a decision.

Creating is the pipeline's business; deciding is first-click-wins like
every alert decision (§9) — the conditional update fires only while the
Suspicion is `pending`, so of several Admins exactly the first one lands.
The §11 jobs are plain due-timestamp reads, safe across restarts.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Suspicion


class SuspicionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        check_id: int,
        chat_id: int,
        user_id: int,
        message_id: int,
        created_at: datetime,
    ) -> Suspicion:
        """One open Suspicion for a flagged message (§4 step 9 stores the text)."""
        suspicion = Suspicion(
            check_id=check_id,
            chat_id=chat_id,
            user_id=user_id,
            message_id=message_id,
            created_at=created_at,
        )
        self.session.add(suspicion)
        await self.session.flush()
        return suspicion

    async def get(self, suspicion_id: int) -> Suspicion | None:
        return await self.session.get(Suspicion, suspicion_id)

    async def open_id(self, chat_id: int, message_id: int) -> int | None:
        """The id of the message's open (pending) Suspicion, if any (§4).

        Edits are re-checked from scratch; a message whose original was
        already flagged must not be alerted on twice.
        """
        return await self.session.scalar(
            select(Suspicion.id).where(
                Suspicion.chat_id == chat_id,
                Suspicion.message_id == message_id,
                Suspicion.status == "pending",
            )
        )

    async def supersede_open(self, chat_id: int, message_id: int, *, at: datetime) -> bool:
        """Close the message's open Suspicion as `superseded` (§4).

        A new Violation on the same message — the original flagged as a
        Suspicion, then the edit confirmed as a Violation — wins without a
        click: `decided_by` stays empty because no Admin decided.
        """
        result = await self.session.execute(
            update(Suspicion)
            .where(
                Suspicion.chat_id == chat_id,
                Suspicion.message_id == message_id,
                Suspicion.status == "pending",
            )
            .values(status="superseded", decided_at=at)
        )
        return bool(result.rowcount)

    async def decide(self, suspicion_id: int, *, status: str, by: int | None, at: datetime) -> bool:
        """Apply one decision press; `False` when somebody was faster (§9).

        `by=None` is the scheduler's auto-close: the Suspicion expires
        without a deciding Admin.
        """
        result = await self.session.execute(
            update(Suspicion)
            .where(Suspicion.id == suspicion_id, Suspicion.status == "pending")
            .values(status=status, decided_by=by, decided_at=at)
        )
        if not result.rowcount:
            return False
        # The conditional UPDATE bypasses the session's identity map; keep the
        # session's copy of the row in step with what was written.
        row = await self.session.get(Suspicion, suspicion_id)
        if row is not None:
            row.status = status
            row.decided_by = by
            row.decided_at = at
        await self.session.flush()
        return True

    async def due(self, now: datetime, *, auto_close_h: int) -> list[Suspicion]:
        """Pending Suspicions nobody acted on for `auto_close_h` (§11)."""
        rows = await self.session.scalars(
            select(Suspicion)
            .where(
                Suspicion.status == "pending",
                Suspicion.created_at <= now - timedelta(hours=auto_close_h),
            )
            .order_by(Suspicion.created_at)
        )
        return list(rows)

    async def summary_counts(
        self, chat_id: int, *, since: datetime, punisher_id: int
    ) -> tuple[int, int]:
        """(Suspicions raised since `since`, how many the Admin punished) (§9)."""
        total = await self.session.scalar(
            select(func.count())
            .select_from(Suspicion)
            .where(Suspicion.chat_id == chat_id, Suspicion.created_at >= since)
        )
        punished = await self.session.scalar(
            select(func.count())
            .select_from(Suspicion)
            .where(
                Suspicion.chat_id == chat_id,
                Suspicion.created_at >= since,
                Suspicion.status == "punished",
                Suspicion.decided_by == punisher_id,
            )
        )
        return total or 0, punished or 0
