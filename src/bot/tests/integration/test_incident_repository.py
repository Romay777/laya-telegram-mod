"""`backend_incident` rows (§12, §5): one row per backend outage.

An incident opens on a backend's first failed check and closes on its first
success; the row records both moments. While it is open the Admins have been
told — the router opens at most one, so a run of failures alerts once, and
a bot restart does not re-open what is already open.
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.db.models import BackendIncident
from app.db.repositories.incidents import IncidentRepository
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def repo(db_session: AsyncSession) -> IncidentRepository:
    return IncidentRepository(db_session)


async def test_a_backend_with_no_trouble_has_no_open_incident(repo: IncidentRepository) -> None:
    assert await repo.open_id("jev") is None


async def test_opening_records_the_backend_and_the_reason(repo: IncidentRepository) -> None:
    incident = await repo.open("jev", reason="authentication failed", opened_at=NOW)

    assert incident.backend == "jev"
    assert incident.reason == "authentication failed"
    assert incident.opened_at == NOW
    assert incident.closed_at is None
    assert await repo.open_id("jev") == incident.id


async def test_a_second_failure_on_the_same_backend_opens_nothing_new(
    repo: IncidentRepository,
) -> None:
    first = await repo.open("jev", reason="rate limited", opened_at=NOW)

    second = await repo.open("jev", reason="rate limited", opened_at=NOW + timedelta(seconds=5))

    assert second.id == first.id
    assert (await repo.all_of("jev"))[0].opened_at == NOW  # still the one row


async def test_incidents_of_different_backends_are_independent(repo: IncidentRepository) -> None:
    await repo.open("jev", reason="rate limited", opened_at=NOW)

    assert await repo.open_id("laya") is None
    laya_incident = await repo.open("laya", reason="timed out", opened_at=NOW)
    assert await repo.open_id("laya") == laya_incident.id
    assert await repo.open_id("jev") != laya_incident.id


async def test_the_first_success_closes_the_open_incident(repo: IncidentRepository) -> None:
    incident = await repo.open("jev", reason="timed out", opened_at=NOW)
    closed_at = NOW + timedelta(minutes=3)

    closed = await repo.close(incident.id, closed_at=closed_at)

    assert closed is True
    row = (await repo.all_of("jev"))[0]
    assert row.closed_at == closed_at
    assert await repo.open_id("jev") is None


async def test_closing_twice_keeps_the_first_moment(repo: IncidentRepository) -> None:
    incident = await repo.open("jev", reason="timed out", opened_at=NOW)

    await repo.close(incident.id, closed_at=NOW + timedelta(minutes=3))
    closed = await repo.close(incident.id, closed_at=NOW + timedelta(minutes=9))

    assert closed is False  # already closed; the recovery alert must not repeat
    assert (await repo.all_of("jev"))[0].closed_at == NOW + timedelta(minutes=3)


async def test_a_reopened_incident_is_a_new_row(repo: IncidentRepository) -> None:
    first = await repo.open("jev", reason="rate limited", opened_at=NOW)
    await repo.close(first.id, closed_at=NOW + timedelta(minutes=3))

    second = await repo.open("jev", reason="rate limited", opened_at=NOW + timedelta(days=1))

    assert second.id != first.id
    rows = await repo.all_of("jev")
    assert [(row.opened_at, row.closed_at) for row in rows] == [
        (NOW + timedelta(days=1), None),
        (NOW, NOW + timedelta(minutes=3)),
    ]


async def test_rows_carry_the_documented_shape(repo: IncidentRepository) -> None:
    """§12: id, backend, reason, opened_at, closed_at — nothing more."""
    incident = await repo.open("laya", reason="timed out", opened_at=NOW)

    stored = await db_get(repo.session, BackendIncident, incident.id)

    assert stored is not None
    assert stored.backend == "laya"
    assert stored.reason == "timed out"
    assert stored.opened_at == NOW
    assert stored.closed_at is None


async def db_get(session: AsyncSession, model: type, key: int) -> object:
    return await session.get(model, key)
