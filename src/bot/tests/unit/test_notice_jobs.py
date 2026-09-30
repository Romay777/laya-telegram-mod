"""Unit: the Chat Notice job of a Violation (§7) — post, record, drop.

`send` posts first and records after: a record failure is the job's own
business — retried, then logged, never turned into a drop, because the
notice is already out. `on_dropped` runs only when the notice never went
out (§7).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import Chat as TgChat
from aiogram.types import Message
from app.clock import FakeClock
from app.db.models import Chat, Violation
from app.notices.jobs import RECORD_ATTEMPTS, violation_notice
from sqlalchemy.exc import SQLAlchemyError

from tests.support.i18n import started_core

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeSession:
    """A session whose commits fail the next `fail_commits` times."""

    def __init__(self, fail_commits: int = 0) -> None:
        self.fail_commits = fail_commits
        self.commits = 0
        self.saved: list[Any] = []

    def add(self, obj: Any) -> None:
        self.saved.append(obj)

    async def flush(self) -> None:
        pass

    async def get(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commits > 0:
            self.fail_commits -= 1
            raise SQLAlchemyError("database is down")

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False


class FakeSessionMaker:
    """Opens one shared `FakeSession` — enough for the job's closures."""

    def __init__(self, fail_commits: int = 0) -> None:
        self.session = FakeSession(fail_commits)

    def __call__(self) -> FakeSession:
        return self.session


class FakeBot:
    """Posts like Telegram, or refuses once when scripted."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.posted = 0

    async def send_message(self, **kwargs: Any) -> Message:
        self.posted += 1
        if self.fail_with is not None:
            failure, self.fail_with = self.fail_with, None
            raise failure
        return Message(message_id=41, date=T0, chat=TgChat(id=-100450, type="supergroup"))


class FakeFanout:
    def __init__(self) -> None:
        self.not_sent = 0

    async def notice_not_sent(self, *args: Any, **kwargs: Any) -> None:
        self.not_sent += 1


class Job:
    """The job for one Violation, with every seam faked and recorded."""

    def __init__(self, core, *, fail_commits: int = 0, bot: FakeBot | None = None) -> None:
        self.sleeps: list[float] = []
        self.session_maker = FakeSessionMaker(fail_commits)
        self.bot = bot if bot is not None else FakeBot()
        self.fanout = FakeFanout()

        async def record_sleep(delay: float) -> None:
            self.sleeps.append(delay)

        restriction_until = T0 + timedelta(hours=1)
        self.notice = violation_notice(
            session_maker=self.session_maker,
            bot=self.bot,
            core=core,
            clock=FakeClock(T0),
            fanout=self.fanout,
            chat=Chat(chat_id=-100450, chat_language="en", ladder=[3600, 86400, 0]),
            violation=Violation(
                id=7, step_index=0, restriction_seconds=3600, restricted_until=restriction_until
            ),
            member_id=500,
            member_name="Spammer",
            category="spam",
            confidence=0.97,
            flagged_text="Buy my product",
            flagged_entities=None,
            max_lifetime_h=24,
            appeal_violation_id=None,
            record_sleep=record_sleep,
        )

    @property
    def session(self) -> FakeSession:
        return self.session_maker.session


async def test_a_posted_notice_is_recorded_with_its_removal_time() -> None:
    core = await started_core()
    job = Job(core)

    await job.notice.send()

    assert job.bot.posted == 1
    assert job.session.commits == 1
    (saved,) = job.session.saved
    assert saved.violation_id == 7
    assert saved.message_id == 41
    assert saved.delete_at == T0 + timedelta(hours=1)  # the Restriction's end (§7)


async def test_a_record_failure_after_a_post_is_retried_and_never_dropped() -> None:
    core = await started_core()
    job = Job(core, fail_commits=1)

    await job.notice.send()  # raises nothing: the notice is out, so it is not a drop

    assert job.session.commits == 2  # the failed one, then the retry
    assert job.sleeps == [0.5]
    assert job.fanout.not_sent == 0


async def test_a_notice_that_never_records_is_logged_and_never_dropped(caplog) -> None:
    core = await started_core()
    job = Job(core, fail_commits=RECORD_ATTEMPTS)

    await job.notice.send()  # still raises nothing, still not a drop

    assert job.session.commits == RECORD_ATTEMPTS
    assert job.sleeps == [0.5, 2.0]
    assert job.fanout.not_sent == 0
    assert "never recorded" in caplog.text


async def test_a_refused_post_raises_to_the_queue_and_the_producer_is_told() -> None:
    core = await started_core()
    refused = FakeBot(
        fail_with=TelegramBadRequest(
            method=SendMessage(chat_id=-100450, text="notice"),
            message="Bad Request: bot was kicked from the chat",
        )
    )
    job = Job(core, bot=refused)

    with pytest.raises(TelegramBadRequest):
        await job.notice.send()

    assert refused.posted == 1
    assert job.session.commits == 0  # nothing was recorded: nothing went out

    await job.notice.on_dropped()  # what the queue's drop runs (§7)

    assert job.fanout.not_sent == 1  # the Admins learn the notice was not sent (§7)
