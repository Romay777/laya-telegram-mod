"""The per-chat Chat Notice queue (§7) at its public interface.

`NoticeQueue` is the §7 upgrade of the Chat Notice sender: an in-memory
FIFO per chat, drained by a sender task, paced at `per_second` and
`per_minute`, dropping what sits longer than `max_queue_age_s`. What
sending and dropping *mean* belongs to the producers: the queue calls the
`send` and `on_dropped` callables each `PendingNotice` carries.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

from aiogram.types import Chat, Message
from app.clock import FakeClock
from app.notices.queue import NoticeQueue, PendingNotice

T0 = datetime(2026, 1, 1, tzinfo=UTC)
CHAT = Chat(id=-100450, type="supergroup", title="My Chat")


class SendRecorder:
    """A `send` callable that records attempts, and can fail the next one."""

    def __init__(self) -> None:
        self._ids = count(1)
        self.attempts = 0
        self.fail_next_with: Exception | None = None

    async def __call__(self) -> Message:
        self.attempts += 1
        if self.fail_next_with is not None:
            failure, self.fail_next_with = self.fail_next_with, None
            raise failure
        return Message(message_id=next(self._ids), date=T0, chat=CHAT)


class DropRecorder:
    def __init__(self) -> None:
        self.dropped = 0

    async def __call__(self) -> None:
        self.dropped += 1


class QueueFixture:
    """A queue whose sleeps move the FakeClock and are recorded."""

    def __init__(
        self,
        clock: FakeClock,
        *,
        per_second: float = 1,
        per_minute: int = 18,
        max_queue_age_s: int = 300,
    ) -> None:
        self.sleeps: list[float] = []
        self.clock = clock

        async def record_sleep(delay: float) -> None:
            self.sleeps.append(delay)
            clock.advance(timedelta(seconds=delay))

        self.queue = NoticeQueue(
            clock=clock,
            per_second=per_second,
            per_minute=per_minute,
            max_queue_age_s=max_queue_age_s,
            sleep=record_sleep,
        )

    def enqueue(
        self,
        chat_id: int = -100450,
        *,
        send: Callable[[], Awaitable[Any]] | None = None,
        on_dropped: Callable[[], Awaitable[None]] | None = None,
        enqueued_at: datetime | None = None,
    ) -> PendingNotice:
        notice = PendingNotice(
            chat_id=chat_id,
            enqueued_at=enqueued_at if enqueued_at is not None else self.clock.now(),
            send=send if send is not None else SendRecorder(),
            on_dropped=on_dropped if on_dropped is not None else DropRecorder(),
        )
        self.queue.enqueue(notice)
        return notice


async def test_a_queue_with_a_free_slot_sends_at_once() -> None:
    fx = QueueFixture(FakeClock(T0))
    notice = fx.enqueue()

    await fx.queue.flush()

    assert notice.send.attempts == 1  # type: ignore[attr-defined]
    assert notice.on_dropped.dropped == 0  # type: ignore[attr-defined]
    assert fx.sleeps == []  # nothing waited


async def test_sends_keep_one_second_apart() -> None:
    fx = QueueFixture(FakeClock(T0), per_second=1)
    first, second, third = fx.enqueue(), fx.enqueue(), fx.enqueue()

    await fx.queue.flush()

    for notice in (first, second, third):
        assert notice.send.attempts == 1  # type: ignore[attr-defined]
        assert notice.on_dropped.dropped == 0  # type: ignore[attr-defined]
    assert fx.sleeps == [1.0, 1.0]  # a full second between each pair (§7)
    assert fx.clock.now() == T0 + timedelta(seconds=2)


async def test_the_rolling_minute_holds_sends_back() -> None:
    # A fast per-second limit, so only `per_minute` can bind here.
    fx = QueueFixture(FakeClock(T0), per_second=100, per_minute=2)
    first, second, third = fx.enqueue(), fx.enqueue(), fx.enqueue()

    await fx.queue.flush()

    for notice in (first, second, third):
        assert notice.send.attempts == 1  # type: ignore[attr-defined]
    # The third send waits for the first to age out of the rolling minute (§7).
    assert fx.sleeps[0] == 0.01  # the fast per-second gap
    assert fx.sleeps[1] == 59.99  # the rest of the first minute


async def test_the_fifo_order_of_one_chat_is_kept() -> None:
    fx = QueueFixture(FakeClock(T0))
    order: list[str] = []

    async def named(name: str) -> Message:
        order.append(name)
        return Message(message_id=1, date=T0, chat=CHAT)

    fx.enqueue(send=lambda: named("first"))
    fx.enqueue(send=lambda: named("second"))
    await fx.queue.flush()

    assert order == ["first", "second"]


async def test_chats_drain_independently() -> None:
    fx = QueueFixture(FakeClock(T0), per_second=1)
    mine = fx.enqueue(-100450)
    theirs = fx.enqueue(-100999)
    mine_second = fx.enqueue(-100450)

    await fx.queue.flush()

    # The other chat's notice does not wait behind this chat's pace: while
    # this chat's second notice sleeps its second, the other chat's notice
    # goes out at once (§7: the queue is per chat).
    for notice in (mine, theirs, mine_second):
        assert notice.send.attempts == 1  # type: ignore[attr-defined]
    assert fx.sleeps.count(1.0) == 1


async def test_a_429_sleeps_retry_after_and_retries_the_same_notice() -> None:
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import SendMessage

    fx = QueueFixture(FakeClock(T0), per_second=1)
    sender = SendRecorder()
    sender.fail_next_with = TelegramRetryAfter(
        method=SendMessage(chat_id=-100450, text="notice"),
        message="Too Many Requests: retry after 7",
        retry_after=7,
    )
    notice = fx.enqueue(send=sender)

    await fx.queue.flush()

    assert sender.attempts == 2  # the refused attempt, then the retry
    assert fx.sleeps == [7.0]  # the sender slept exactly `retry_after` (§7)
    assert notice.on_dropped.dropped == 0  # a retry is not a drop
    assert fx.clock.now() == T0 + timedelta(seconds=7)


async def test_a_notice_older_than_max_queue_age_is_dropped_without_sending() -> None:
    # A slow pace (one notice per 10 s) against a 5 s queue age: the second
    # notice's slot comes long after it has gone stale (§7).
    fx = QueueFixture(FakeClock(T0), per_second=0.1, max_queue_age_s=5)
    first, second = fx.enqueue(), fx.enqueue()

    await fx.queue.flush()

    assert first.send.attempts == 1  # type: ignore[attr-defined]
    assert first.on_dropped.dropped == 0  # type: ignore[attr-defined]
    assert second.send.attempts == 0  # type: ignore[attr-defined] — never posted
    assert second.on_dropped.dropped == 1  # type: ignore[attr-defined]
    assert fx.sleeps == [10.0]  # it slept out its slot, woke, and dropped it


async def test_the_queue_age_counts_from_enqueue_not_from_the_wake_up() -> None:
    # Two notices queued four seconds apart; the drain only reaches them
    # after six. The older one has aged out of the queue, the newer one has
    # not — the age counts from each `enqueued_at` (§7).
    fx = QueueFixture(FakeClock(T0), per_second=1, max_queue_age_s=5)
    stale = fx.enqueue(enqueued_at=T0)
    fresh = fx.enqueue(enqueued_at=T0 + timedelta(seconds=4))
    fx.clock.advance(timedelta(seconds=6))

    await fx.queue.flush()

    assert fresh.send.attempts == 1  # type: ignore[attr-defined]
    assert fresh.on_dropped.dropped == 0  # type: ignore[attr-defined]
    assert stale.send.attempts == 0  # type: ignore[attr-defined]
    assert stale.on_dropped.dropped == 1  # type: ignore[attr-defined]
