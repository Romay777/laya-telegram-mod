"""Repository for the moderation trail (§12): checks, flagged text, violations, notices.

Recording a Violation carries the §6 maths: count the Member's Active
Violations, take the Step that count names, and insert the Violation with
its Expiry — one transaction, because the count and the insert belong
together. The scheduler jobs (§11) are plain due-timestamp reads, so they
are safe across restarts.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chat, ChatNotice, FlaggedMessage, MessageCheck, Violation
from app.domain.ladder import restricted_until, select_step


class ModerationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_check(
        self,
        *,
        chat_id: int,
        user_id: int,
        message_id: int,
        backend: str,
        model: str,
        spec_version: int,
        outcome: str,
        created_at: datetime,
        category: str | None = None,
        confidence: float | None = None,
        probabilities: dict[str, float] | None = None,
        latency_ms: int | None = None,
    ) -> MessageCheck:
        """One pipeline check: the Verdict and probabilities, never the text (§12)."""
        check = MessageCheck(
            chat_id=chat_id,
            user_id=user_id,
            message_id=message_id,
            backend=backend,
            model=model,
            spec_version=spec_version,
            outcome=outcome,
            category=category,
            confidence=confidence,
            probabilities=probabilities,
            latency_ms=latency_ms,
            created_at=created_at,
        )
        self.session.add(check)
        await self.session.flush()
        return check

    async def store_flagged(
        self, check_id: int, *, text: str, entities: list[dict], purge_at: datetime
    ) -> None:
        """Keep the deleted message's text until `purge_at` (§4 step 9, §12)."""
        self.session.add(
            FlaggedMessage(check_id=check_id, text=text, entities=entities, purge_at=purge_at)
        )
        await self.session.flush()

    async def record_violation(
        self,
        *,
        chat: Chat,
        user_id: int,
        check_id: int,
        category: str,
        now: datetime,
        source: str = "auto",
    ) -> Violation:
        """Record one Violation and the Restriction it applies (§6, in order).

        `active` counts the Member's unexpired, unrevoked Violations; the Step
        taken is `ladder[min(active, len(ladder)) - 1]`. The Restriction fields
        store what was applied, so the pipeline restricts from the row.
        """
        active = await self.count_active(chat.chat_id, user_id, now)
        step_index, step_seconds = select_step(chat.ladder, active_count=active + 1)
        expires_at = (
            now + timedelta(seconds=chat.expiry_seconds)
            if chat.expiry_seconds is not None
            else None  # Expiry: never (§12)
        )
        violation = Violation(
            chat_id=chat.chat_id,
            user_id=user_id,
            check_id=check_id,
            category=category,
            source=source,
            step_index=step_index,
            restriction_seconds=step_seconds,
            restricted_until=restricted_until(now, step_seconds),
            expires_at=expires_at,
            created_at=now,
        )
        self.session.add(violation)
        await self.session.flush()
        return violation

    async def count_active(self, chat_id: int, user_id: int, now: datetime) -> int:
        """Active Violations (§6): not revoked, and not yet expired."""
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(Violation)
                .where(
                    Violation.chat_id == chat_id,
                    Violation.user_id == user_id,
                    Violation.revoked_at.is_(None),
                    or_(Violation.expires_at.is_(None), Violation.expires_at > now),
                )
            )
            or 0
        )

    async def revoke_violation(self, violation_id: int, *, by: int, at: datetime) -> bool:
        """Turn a Violation into a False Positive (§6): first click wins (§9).

        The conditional update fires only while the Violation still stands,
        so of several Admins lifting the same Restriction exactly the first
        one revokes it, and records who and when.
        """
        result = await self.session.execute(
            update(Violation)
            .where(Violation.id == violation_id, Violation.revoked_at.is_(None))
            .values(revoked_at=at, revoked_by=by)
        )
        if not result.rowcount:
            return False
        # The conditional UPDATE bypasses the session's identity map; keep the
        # session's copy of the row in step with what was written.
        row = await self.session.get(Violation, violation_id)
        if row is not None:
            row.revoked_at = at
            row.revoked_by = by
        await self.session.flush()
        return True

    async def save_notice(
        self, violation_id: int, *, message_id: int, delete_at: datetime
    ) -> ChatNotice:
        """Remember the posted notice and when it must delete itself (§7)."""
        notice = ChatNotice(violation_id=violation_id, message_id=message_id, delete_at=delete_at)
        self.session.add(notice)
        await self.session.flush()
        return notice

    async def due_notices(self, now: datetime) -> list[tuple[ChatNotice, int]]:
        """Notices whose `delete_at` has passed (§11), with the chat to delete in."""
        rows = await self.session.execute(
            select(ChatNotice, Violation.chat_id)
            .join(Violation, ChatNotice.violation_id == Violation.id)
            .where(ChatNotice.delete_at <= now, ChatNotice.deleted_at.is_(None))
            .order_by(ChatNotice.delete_at)
        )
        return [(notice, chat_id) for notice, chat_id in rows.all()]

    async def mark_notice_deleted(self, violation_id: int, *, deleted_at: datetime) -> None:
        notice = await self.session.get(ChatNotice, violation_id)
        if notice is not None:
            notice.deleted_at = deleted_at
            await self.session.flush()

    async def due_flagged(self, now: datetime) -> list[FlaggedMessage]:
        """Stored texts whose retention has passed (§11)."""
        rows = await self.session.scalars(
            select(FlaggedMessage)
            .where(FlaggedMessage.purge_at <= now, FlaggedMessage.text.is_not(None))
            .order_by(FlaggedMessage.purge_at)
        )
        return list(rows)

    async def purge_flagged(self, check_id: int) -> None:
        """Remove the stored text and entities; the check row stays (§8)."""
        flagged = await self.session.get(FlaggedMessage, check_id)
        if flagged is not None:
            flagged.text = None
            flagged.entities = None
            await self.session.flush()
