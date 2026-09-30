"""Repository for the Journal screen (§13).

The Journal pages the chat's Violations newest first, and the Violation
card joins the row with its check (the confidence), its stored text and
its current state (§6). The Statistics counts live in the statistics
repository beside this one.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FlaggedMessage, MessageCheck, Violation
from app.domain.ladder import ViolationState, violation_state

#: The Journal shows five Violations per page (§13).
PAGE_SIZE = 5


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One Violation as the Journal list shows it (§13)."""

    violation_id: int
    category: str
    user_id: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ViolationCard:
    """A Journal entry opened (§13): the Violation with its check and text.

    `step_seconds` is the stored Restriction (0 = forever, None = a
    sender-chat ban, which took no Step); `flagged_text` and
    `flagged_entities` are None once the retention has purged them (§8).
    """

    violation_id: int
    chat_id: int
    user_id: int
    category: str
    confidence: float | None
    step_seconds: int | None
    state: ViolationState
    flagged_text: str | None
    flagged_entities: list[dict[str, Any]] | None
    created_at: datetime


class JournalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(self, chat_id: int, *, page: int) -> tuple[list[JournalEntry], int]:
        """One Journal page, newest first, with the total to steer the ends (§13)."""
        total = await self._count(
            select(func.count()).select_from(Violation).where(Violation.chat_id == chat_id)
        )
        rows = await self.session.execute(
            select(Violation.id, Violation.category, Violation.user_id, Violation.created_at)
            .where(Violation.chat_id == chat_id)
            .order_by(Violation.created_at.desc(), Violation.id.desc())
            .offset(max(page, 0) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        entries = [
            JournalEntry(
                violation_id=violation_id,
                category=category,
                user_id=user_id,
                created_at=created_at,
            )
            for violation_id, category, user_id, created_at in rows.all()
        ]
        return entries, total

    async def card(self, violation_id: int, *, now: datetime) -> ViolationCard | None:
        """The Violation card (§13): the row joined with its check and text."""
        row = await self.session.execute(
            select(Violation, MessageCheck, FlaggedMessage)
            .join(MessageCheck, Violation.check_id == MessageCheck.id)
            .outerjoin(FlaggedMessage, FlaggedMessage.check_id == MessageCheck.id)
            .where(Violation.id == violation_id)
        )
        found = row.first()
        if found is None:
            return None
        violation, check, flagged = found
        return ViolationCard(
            violation_id=violation.id,
            chat_id=violation.chat_id,
            user_id=violation.user_id,
            category=violation.category,
            confidence=check.confidence,
            step_seconds=violation.restriction_seconds,
            state=violation_state(
                revoked_at=violation.revoked_at, expires_at=violation.expires_at, now=now
            ),
            flagged_text=flagged.text if flagged is not None else None,
            flagged_entities=flagged.entities if flagged is not None else None,
            created_at=violation.created_at,
        )

    async def _count(self, query: Any) -> int:
        return await self.session.scalar(query) or 0
