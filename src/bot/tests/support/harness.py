"""The e2e app: real wiring, fake Telegram.

Everything downstream of the Dispatcher is the real app (Postgres FSM
storage, i18n, handlers); the only fakes are the Bot API session, which
records every outgoing call, the Clock, and the classifier backend (§17).
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.methods import GetChatMember
from aiogram_i18n import I18nMiddleware
from app.classifiers.router import BackendRouter
from app.clock import FakeClock
from app.main import build_dispatcher, build_i18n_middleware
from app.notices.queue import NoticeQueue
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.support.backend import ControllableHealth, FakeBackend
from tests.support.fake_session import FakeBotSession
from tests.support.telegram import member_owner
from tests.support.updates import (
    Update,
    my_chat_member_update,
    private_callback_update,
    start_update,
    user,
)

TEST_BOT_TOKEN = "42:test-token"
FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class TestApp:
    __test__ = False  # a harness, not a test class: keep pytest from collecting it

    dispatcher: Dispatcher
    bot: Bot
    session: FakeBotSession
    session_maker: async_sessionmaker[AsyncSession]
    engine: AsyncEngine
    clock: FakeClock
    backend: FakeBackend
    backends: dict[str, FakeBackend]
    laya_health: ControllableHealth
    i18n: I18nMiddleware
    notices: NoticeQueue
    _update_id: int = 0

    async def feed(self, update: Update) -> None:
        self._update_id += 1
        # aiogram types are frozen: rebuild the Update with the next id.
        update = Update(
            update_id=self._update_id,
            **update.model_dump(exclude={"update_id"}, exclude_none=True),
        )
        await self.dispatcher.feed_update(self.bot, update)

    async def aclose(self) -> None:
        await self.engine.dispose()


async def build_app(
    postgres_url: str,
    *,
    prompt_delete_after_s: float = 600.0,
    backend: FakeBackend | None = None,
    backends: dict[str, FakeBackend] | None = None,
    laya_deployed: bool = True,
    laya_healthy: bool = True,
    notices_per_second: float = 1,
    notices_per_minute: int = 18,
    max_queue_age_s: int = 300,
    notices_gate: asyncio.Event | None = None,
    shifts: dict[str, float] | None = None,
) -> TestApp:
    i18n = build_i18n_middleware()
    await i18n.core.startup()  # the Dispatcher's startup hook does this in production

    engine = create_async_engine(postgres_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    clock = FakeClock(FIXED_NOW)
    laya_health = ControllableHealth(deployed=laya_deployed, healthy=laya_healthy)
    if backends is not None:
        # The real BackendRouter over fakes as its two clients, so the tests
        # exercise the production fallback decision (§5).
        classifier: Any = BackendRouter(
            laya=backends["laya"], jev=backends["jev"], health=laya_health
        )
        default_backend = backends["laya"]
    else:
        classifier = backend if backend is not None else FakeBackend()
        default_backend = classifier
    # The queue's waiting moves the FakeClock, so the e2e tests see the
    # paced schedule the way production lives it — just instantly (§17).
    notice_queue = NoticeQueue(
        clock=clock,
        per_second=notices_per_second,
        per_minute=notices_per_minute,
        max_queue_age_s=max_queue_age_s,
        sleep=_clock_moving_sleep(clock, notices_gate),
    )
    dispatcher = build_dispatcher(
        session_maker=session_maker,
        i18n=i18n,
        clock=clock,
        # The default is 600 s (§10); tests shrink it so the self-deleting
        # group prompt actually deletes inside the test.
        prompt_delete_after_s=prompt_delete_after_s,
        classifier=classifier,
        # No models are passed: the pipeline records the classifier's own
        # (§12), the way `run()` wires production.
        notices=notice_queue,
        # The default pace is one alert per second per Admin (§9); tests
        # must not wait on it.
        alerts_pace_s=0.0,
        shifts=shifts,
    )

    session = FakeBotSession()
    bot = Bot(TEST_BOT_TOKEN, session=session)
    return TestApp(
        dispatcher=dispatcher,
        bot=bot,
        session=session,
        session_maker=session_maker,
        engine=engine,
        clock=clock,
        backend=default_backend,
        backends=backends if backends is not None else {"laya": default_backend},
        laya_health=laya_health,
        i18n=i18n,
        notices=notice_queue,
    )


def _clock_moving_sleep(
    clock: FakeClock, gate: asyncio.Event | None = None
) -> Callable[[float], Awaitable[None]]:
    async def sleep(delay: float) -> None:
        # A gate holds the queue's pace until the test says go: feeding the
        # raid then finishes before any wait is slept out, the way it does
        # when the waits are real seconds (§17).
        if gate is not None:
            await gate.wait()
        clock.advance(timedelta(seconds=delay))

    return sleep


@asynccontextmanager
async def app_fixture(
    postgres_url: str,
    *,
    prompt_delete_after_s: float = 600.0,
    backend: FakeBackend | None = None,
    backends: dict[str, FakeBackend] | None = None,
    laya_deployed: bool = True,
    laya_healthy: bool = True,
    notices_per_second: float = 1,
    notices_per_minute: int = 18,
    max_queue_age_s: int = 300,
    notices_gate: asyncio.Event | None = None,
    shifts: dict[str, float] | None = None,
) -> AsyncIterator[TestApp]:
    app = await build_app(
        postgres_url,
        prompt_delete_after_s=prompt_delete_after_s,
        backend=backend,
        backends=backends,
        laya_deployed=laya_deployed,
        laya_healthy=laya_healthy,
        notices_per_second=notices_per_second,
        notices_per_minute=notices_per_minute,
        max_queue_age_s=max_queue_age_s,
        notices_gate=notices_gate,
        shifts=shifts,
    )
    try:
        yield app
    finally:
        await app.aclose()


async def started_admin(app: TestApp, admin_id: int) -> int:
    """/start with the language picked; returns the Admin's Menu message id."""
    await app.feed(start_update(admin_id, "en"))
    menu_message_id = app.session.calls_of("SendMessage")[0].result.message_id
    await app.feed(
        private_callback_update(
            admin_id, "menu:set-language:en", menu_message_id, language_code="en"
        )
    )
    return menu_message_id


async def linked_via_deeplink(
    app: TestApp, admin_id: int, chat_id: int, title: str = "My Chat"
) -> int:
    """Add to chat → the promotion update; returns the Admin's Menu message id."""
    menu_message_id = await started_admin(app, admin_id)
    await app.feed(
        private_callback_update(admin_id, "menu:add-to-chat:", menu_message_id, language_code="en")
    )
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(my_chat_member_update(chat_id, "supergroup", linker_id=admin_id, title=title))
    app.session.calls.clear()
    return menu_message_id


async def auto_moderation_chat(app: TestApp, admin_id: int, chat_id: int) -> int:
    """Link the chat, open it, and arm Auto-moderation on the Mode screen (§13)."""
    menu = await linked_via_deeplink(app, admin_id, chat_id)
    app.session.script(GetChatMember, member_owner(user(admin_id)))
    await app.feed(private_callback_update(admin_id, f"chat:{chat_id}", menu, language_code="en"))
    await app.feed(
        private_callback_update(admin_id, f"chat-settings:{chat_id}", menu, language_code="en")
    )
    await app.feed(
        private_callback_update(admin_id, f"chat-mode:{chat_id}", menu, language_code="en")
    )
    app.session.calls.clear()
    return menu
