"""The scheduler loop (§11): due-timestamp jobs every 30 seconds.

Every job is a query over due timestamps, so the work is safe across
restarts: whatever a restart interrupts is still due afterwards. This
ticket carries the two jobs the moderation tracer needs — removing Chat
Notices at `delete_at` and purging flagged text at `purge_at`; the other
§11 jobs land with their tickets.
"""

import asyncio
import contextlib
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import Clock
from app.db.repositories.moderation import ModerationRepository

logger = logging.getLogger(__name__)

#: §11: one loop tick every 30 seconds.
INTERVAL_S = 30.0


class Scheduler:
    def __init__(
        self,
        *,
        bot: Bot,
        session_maker: async_sessionmaker[AsyncSession],
        clock: Clock,
        interval_s: float = INTERVAL_S,
    ) -> None:
        self._bot = bot
        self._session_maker = session_maker
        self._clock = clock
        self._interval_s = interval_s

    async def run_once(self) -> None:
        """Run every due job once; each job is a due-timestamp query (§11)."""
        async with self._session_maker() as session:
            repo = ModerationRepository(session)
            now = self._clock.now()

            for notice, chat_id in await repo.due_notices(now):
                # A notice Telegram already dropped is gone all the same.
                with contextlib.suppress(TelegramAPIError):
                    await self._bot.delete_message(chat_id=chat_id, message_id=notice.message_id)
                await repo.mark_notice_deleted(notice.violation_id, deleted_at=now)

            for flagged in await repo.due_flagged(now):
                await repo.purge_flagged(flagged.check_id)

            await session.commit()

    async def run_forever(self) -> None:
        """The §11 loop: every job, every 30 seconds, until the bot stops."""
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("scheduler tick failed")
            await asyncio.sleep(self._interval_s)
