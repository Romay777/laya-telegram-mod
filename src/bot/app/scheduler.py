"""The scheduler loop (§11): due-timestamp jobs every 30 seconds.

Every job is a query over due timestamps, so the work is safe across
restarts: whatever a restart interrupts is still due afterwards. This
ticket adds the Suspicion jobs: auto-closing what nobody decided and the
one Observation summary; the other §11 jobs land with their tickets.
"""

import asyncio
import contextlib
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.summary import send_observation_summary
from app.clock import Clock
from app.db.models import Chat
from app.db.repositories.chats import ChatRepository
from app.db.repositories.moderation import ModerationRepository
from app.db.repositories.suspicions import SuspicionRepository

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
        core: BaseCore,
        auto_close_h: int = 24,
        summary_after_h: int = 48,
        removed_chat_days: int = 30,
        interval_s: float = INTERVAL_S,
    ) -> None:
        self._bot = bot
        self._session_maker = session_maker
        self._clock = clock
        self._core = core
        self._auto_close_h = auto_close_h
        self._summary_after_h = summary_after_h
        self._removed_chat_days = removed_chat_days
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

            for suspicion in await SuspicionRepository(session).due(
                now, auto_close_h=self._auto_close_h
            ):
                # Nobody decided: the Suspicion expires without a decider (§9).
                await SuspicionRepository(session).decide(
                    suspicion.id, status="expired", by=None, at=now
                )

            for chat in await ChatRepository(session).due_observation_summaries(now):
                await self._send_summary(session, chat)

            # §10: a Removed Chat's settings are kept for the retention
            # window, then the row goes — and every chat-scoped row with it
            # by cascade (ADR-0001).
            for chat in await ChatRepository(session).due_removed_chats(
                now, removed_chat_days=self._removed_chat_days
            ):
                await ChatRepository(session).purge(chat.chat_id)

            await session.commit()

    async def _send_summary(self, session: AsyncSession, chat: Chat) -> None:
        """One Observation summary; a transient failure stays due (§11)."""
        try:
            await send_observation_summary(
                self._bot,
                session,
                core=self._core,
                clock=self._clock,
                chat=chat,
                window_h=self._summary_after_h,
            )
        except TelegramAPIError:
            logger.exception("observation summary for chat %s failed", chat.chat_id)

    async def run_forever(self) -> None:
        """The §11 loop: every job, every 30 seconds, until the bot stops."""
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("scheduler tick failed")
            await asyncio.sleep(self._interval_s)
