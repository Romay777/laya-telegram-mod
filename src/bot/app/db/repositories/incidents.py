"""Repository for `backend_incident` rows (§12): the lifecycle of an outage.

An incident opens on a backend's first failed check and closes on its first
success (§5). Opening is idempotent while one is already open — a run of
failures is one incident, one alert — and the `closed_at` conditional update
fires only once, so the recovery follow-up is not repeated.
"""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BackendIncident


class IncidentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def open_id(self, backend: str) -> int | None:
        """The id of the incident still open for `backend`, if any (§5)."""
        return await self.session.scalar(
            select(BackendIncident.id)
            .where(BackendIncident.backend == backend, BackendIncident.closed_at.is_(None))
            .order_by(BackendIncident.id.desc())
            .limit(1)
        )

    async def open(self, backend: str, *, reason: str, opened_at: datetime) -> BackendIncident:
        """Open an incident for `backend`, or return the one already open (§5)."""
        open_id = await self.open_id(backend)
        if open_id is not None:
            existing = await self.session.get(BackendIncident, open_id)
            assert existing is not None  # the id came from this very table
            return existing
        incident = BackendIncident(backend=backend, reason=reason, opened_at=opened_at)
        self.session.add(incident)
        await self.session.flush()
        return incident

    async def close(self, incident_id: int, *, closed_at: datetime) -> bool:
        """Close an incident once (§5): True only for the first close."""
        result = await self.session.execute(
            update(BackendIncident)
            .where(BackendIncident.id == incident_id, BackendIncident.closed_at.is_(None))
            .values(closed_at=closed_at)
        )
        if not result.rowcount:
            return False
        # The conditional UPDATE bypasses the session's identity map; keep the
        # session's copy of the row in step with what was written.
        row = await self.session.get(BackendIncident, incident_id)
        if row is not None:
            row.closed_at = closed_at
        await self.session.flush()
        return True

    async def all_of(self, backend: str) -> list[BackendIncident]:
        """Every incident of one backend, the newest first."""
        rows = await self.session.scalars(
            select(BackendIncident)
            .where(BackendIncident.backend == backend)
            .order_by(BackendIncident.id.desc())
        )
        return list(rows)

    async def count(self) -> int:
        return await self.session.scalar(select(func.count()).select_from(BackendIncident)) or 0
