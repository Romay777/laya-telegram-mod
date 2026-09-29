"""Repository for `member` rows (§12): the Member tracking of §4 step 5.

One row per (chat, member), created on the Member's first checked message.
It feeds the §3 signals — first-seen age, checked and flagged counts — and
its counters move with every check.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Member


class MemberFacts:
    """What the §3 signals ask about one Member: the row's three facts."""

    __slots__ = ("checked_count", "first_seen_at", "flagged_count")

    def __init__(self, *, first_seen_at: datetime, checked_count: int, flagged_count: int) -> None:
        self.first_seen_at = first_seen_at
        self.checked_count = checked_count
        self.flagged_count = flagged_count


class MemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def facts(self, chat_id: int, user_id: int) -> MemberFacts | None:
        """The Member's row as the signals read it, or None before any check."""
        member = await self.session.get(Member, {"chat_id": chat_id, "user_id": user_id})
        if member is None:
            return None
        return MemberFacts(
            first_seen_at=member.first_seen_at,
            checked_count=member.checked_count,
            flagged_count=member.flagged_count,
        )

    async def note_checked(
        self, chat_id: int, user_id: int, *, flagged: bool, at: datetime
    ) -> datetime:
        """One checked message: create the row or bump it; first-seen back (§12).

        The check that flags the message also counts on `flagged_count`, so
        "never flagged" (the Established Member condition, §3) means the
        Member was never confirmed on any check.
        """
        member = await self.session.get(Member, {"chat_id": chat_id, "user_id": user_id})
        if member is None:
            member = Member(
                chat_id=chat_id,
                user_id=user_id,
                first_seen_at=at,
                checked_count=1,
                flagged_count=1 if flagged else 0,
            )
            self.session.add(member)
            await self.session.flush()
            return member.first_seen_at
        member.checked_count += 1
        if flagged:
            member.flagged_count += 1
        await self.session.flush()
        return member.first_seen_at

    async def known_ids(self, chat_id: int) -> set[int]:
        """The users with a Member row in one chat (not used by v1 checks)."""
        rows = await self.session.scalars(select(Member.user_id).where(Member.chat_id == chat_id))
        return set(rows)
