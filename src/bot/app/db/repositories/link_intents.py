"""Repository for `link_intent` rows (§12): one-time startgroup tokens (§10)."""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LinkIntent


class LinkIntentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, token: str, user_id: int, expires_at: datetime) -> LinkIntent:
        intent = LinkIntent(token=token, user_id=user_id, expires_at=expires_at)
        self.session.add(intent)
        await self.session.flush()
        return intent

    async def find_valid(self, user_id: int, *, now: datetime) -> LinkIntent | None:
        """The newest unexpired intent of `user_id`, left in place.

        The gate for "is this promotion a Linking attempt?"; the intent is
        only taken out by `consume_valid` when the link completes.
        """
        return await self.session.scalar(
            select(LinkIntent)
            .where(LinkIntent.user_id == user_id, LinkIntent.expires_at > now)
            .order_by(LinkIntent.expires_at.desc(), LinkIntent.token)
            .limit(1)
        )

    async def consume_valid(self, user_id: int, *, now: datetime) -> LinkIntent | None:
        """Take the newest unexpired intent of `user_id` out of the table.

        This is what makes the token single-use: the first consume wins, and an
        expired token finds nothing. A failed Linking never consumes.
        """
        intent = await self.session.scalar(
            select(LinkIntent)
            .where(LinkIntent.user_id == user_id, LinkIntent.expires_at > now)
            .order_by(LinkIntent.expires_at.desc(), LinkIntent.token)
            .limit(1)
        )
        if intent is None:
            return None
        await self.session.execute(delete(LinkIntent).where(LinkIntent.token == intent.token))
        await self.session.flush()
        return intent
