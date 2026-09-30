"""The per-chat Chat Notice queue (§7): the rate-limited sender.

Each chat has an in-memory FIFO drained by a sender task. The drain paces
itself through `domain.rate_limit` — at most `per_second` messages per
second and `per_minute` per rolling minute — sleeps out a Telegram
`retry_after` on a 429 and retries the same notice, drops any notice that
sat in the queue longer than `max_queue_age_s`, and drops a notice Telegram
refuses outright (or whose bookkeeping fails) — telling its producer so the
Violation can be marked `notice_dropped` (§7). One failed notice never ends
the drain: the notices behind it go out all the same.

What sending and dropping *mean* is the producer's business: every
`PendingNotice` carries its own `send` and `on_dropped` callables. The
queue is not persisted; anything still queued is lost on restart, but the
Restriction is already in place (§7).
"""

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from sqlalchemy.exc import SQLAlchemyError

from app.clock import Clock
from app.domain.rate_limit import next_send_at

logger = logging.getLogger(__name__)

#: One notice sits no longer than this in the queue (§7 `max_queue_age_s`).
DEFAULT_MAX_QUEUE_AGE_S = 300

_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class PendingNotice:
    """One Chat Notice waiting for its slot: what to do, and since when.

    `send` posts the notice and records it; it may raise
    `TelegramRetryAfter`, which only delays it. `on_dropped` runs when the
    notice aged out of the queue instead — the Violation's business (§7).
    """

    chat_id: int
    enqueued_at: datetime
    send: Callable[[], Awaitable[Any]]
    on_dropped: Callable[[], Awaitable[None]]


class NoticeQueue:
    def __init__(
        self,
        *,
        clock: Clock,
        per_second: float = 1,
        per_minute: int = 18,
        max_queue_age_s: int = DEFAULT_MAX_QUEUE_AGE_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._per_second = per_second
        self._per_minute = per_minute
        self._max_queue_age = timedelta(seconds=max_queue_age_s)
        self._sleep = sleep
        self._pending: dict[int, deque[PendingNotice]] = {}
        self._sent: dict[int, deque[datetime]] = {}
        self._drains: dict[int, asyncio.Task[None]] = {}

    def enqueue(self, notice: PendingNotice) -> None:
        """Hand one notice to its chat's FIFO and make sure a drain is running."""
        self._pending.setdefault(notice.chat_id, deque()).append(notice)
        drain = self._drains.get(notice.chat_id)
        if drain is None or drain.done():
            self._drains[notice.chat_id] = asyncio.create_task(self._drain(notice.chat_id))

    async def flush(self) -> None:
        """Drain every chat to emptiness now.

        The shutdown path uses it to finish (or drop) what is queued; the
        e2e harness uses it to make the queue's work observable (§17).
        """
        while self._drains:
            await asyncio.gather(*list(self._drains.values()))

    async def _drain(self, chat_id: int) -> None:
        """Send one chat's notices, in order, at the chat's own pace (§7)."""
        pending = self._pending.setdefault(chat_id, deque())
        sent = self._sent.setdefault(chat_id, deque())
        try:
            await self._drain_loop(chat_id, pending, sent)
        finally:
            # An idle drain leaves, so flush() has living work to wait on.
            if self._drains.get(chat_id) is asyncio.current_task():
                del self._drains[chat_id]

    async def _drain_loop(
        self,
        chat_id: int,
        pending: deque[PendingNotice],
        sent: deque[datetime],
    ) -> None:
        while pending:
            notice = pending[0]
            now = self._clock.now()

            # Staleness comes first: a notice too old to post is dropped,
            # and so is every notice behind it that has aged the same (§7).
            if now - notice.enqueued_at > self._max_queue_age:
                pending.popleft()
                await self._drop(chat_id, notice)
                continue

            slot = next_send_at(now, sent, per_second=self._per_second, per_minute=self._per_minute)
            if slot > now:
                await self._sleep((slot - now).total_seconds())
                continue

            pending.popleft()
            try:
                await notice.send()
            except TelegramRetryAfter as retry:
                # A 429: sleep for `retry_after`, then retry the same notice (§7).
                pending.appendleft(notice)
                await self._sleep(float(retry.retry_after))
                continue
            except (TelegramAPIError, SQLAlchemyError):
                # Telegram refused the notice (the bot was kicked, say), or
                # its bookkeeping failed: drop it like a stale one and go on —
                # one failed notice never ends the drain (§7). Anything else
                # stays an error: a bug in the queue's own work must be loud.
                logger.warning("notice for chat %s failed to send; dropped", chat_id, exc_info=True)
                await self._drop(chat_id, notice)
                continue

            sent.append(self._clock.now())
            cutoff = self._clock.now() - _MINUTE
            while sent and sent[0] <= cutoff:
                sent.popleft()

    async def _drop(self, chat_id: int, notice: PendingNotice) -> None:
        """Tell the producer its notice was dropped; the drain goes on regardless (§7).

        The drop-bookkeeping opens its own session — and even when that
        fails, the notice is already out of the queue: nothing is gained by
        letting the failure end the drain.
        """
        try:
            await notice.on_dropped()
        except (TelegramAPIError, SQLAlchemyError):
            logger.warning("drop bookkeeping for chat %s failed", chat_id, exc_info=True)
