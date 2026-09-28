"""Wiring: settings, DB, Bot, Dispatcher, scheduler (§2)."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram_i18n import I18nMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.alerts.fanout import AlertFanout
from app.classifiers.client import SystemOneClient
from app.classifiers.router import BackendRouter, ClassifierBackend
from app.classifiers.spec import LAYA_BASE_URL, LAYA_MODEL
from app.clock import Clock, SystemClock
from app.config import ClassifierSettings, Settings, default_thresholds
from app.db.fsm_storage import PostgresStorage
from app.db.migrate import run_migrations
from app.i18n.middleware import build_i18n_middleware
from app.linking.admin_cache import AdminCache
from app.linking.service import LinkingService
from app.menu.navigator import MenuNavigator
from app.moderation.pipeline import ModerationPipeline, Thresholds
from app.scheduler import Scheduler
from app.telegram.handlers.alerts import create_alerts_router
from app.telegram.handlers.appeals import create_appeals_router
from app.telegram.handlers.group import create_group_router
from app.telegram.handlers.linking import create_linking_router
from app.telegram.handlers.private import create_private_router
from app.telegram.middlewares import BotUserMiddleware, DbSessionMiddleware

# §16: long polling receives exactly these update types.
ALLOWED_UPDATES = ["message", "edited_message", "callback_query", "my_chat_member", "chat_member"]

logger = logging.getLogger(__name__)


def build_session_maker(database_url: str) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(create_async_engine(database_url), expire_on_commit=False)


def build_classifier(
    *,
    base_url: str = LAYA_BASE_URL,
    timeout_s: float = ClassifierSettings().timeout_s,
    max_concurrency: int = ClassifierSettings().max_concurrency,
) -> BackendRouter:
    """The Classifier Backend the Instance checks with: the local Laya (§5).

    Jev and the fallback router are ticket #14; until then Laya is the only
    backend, so the pinned model and the §3 defaults live here.
    """
    return BackendRouter(
        SystemOneClient(base_url, model=LAYA_MODEL, timeout_s=timeout_s),
        max_concurrency=max_concurrency,
    )


def build_dispatcher(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    i18n: I18nMiddleware,
    clock: Clock,
    prompt_delete_after_s: float = 600.0,
    admin_cache_ttl_s: float = 300.0,
    classifier: ClassifierBackend | None = None,
    thresholds: Thresholds | None = None,
    min_words: int = 3,
    flagged_text_days: int = 30,
    max_notice_lifetime_h: int = 24,
    outcome_visible_s: int = 600,
    alerts_pace_s: float = 1.0,
) -> Dispatcher:
    navigator = MenuNavigator(core=i18n.core)
    linking = LinkingService(
        session_maker=session_maker,
        clock=clock,
        core=i18n.core,
        prompt_delete_after_s=prompt_delete_after_s,
    )
    admin_cache = AdminCache(clock=clock, ttl_s=admin_cache_ttl_s)
    fanout = AlertFanout(core=i18n.core, clock=clock, pace_s=alerts_pace_s)
    pipeline = ModerationPipeline(
        clock=clock,
        core=i18n.core,
        backend=classifier if classifier is not None else build_classifier(),
        thresholds=thresholds if thresholds is not None else default_thresholds(),
        min_words=min_words,
        flagged_text_days=flagged_text_days,
        max_notice_lifetime_h=max_notice_lifetime_h,
        fanout=fanout,
    )
    dispatcher = Dispatcher(storage=PostgresStorage(session_maker))
    dispatcher.update.outer_middleware(DbSessionMiddleware(session_maker))
    dispatcher.update.outer_middleware(BotUserMiddleware(clock=clock))
    i18n.setup(dispatcher)  # locale resolution runs after the DB middlewares
    dispatcher.include_router(create_private_router())
    dispatcher.include_router(create_linking_router())
    dispatcher.include_router(create_alerts_router(max_notice_lifetime_h=max_notice_lifetime_h))
    dispatcher.include_router(create_appeals_router(outcome_visible_s=outcome_visible_s))
    dispatcher.include_router(create_group_router())
    dispatcher["navigator"] = navigator
    dispatcher["clock"] = clock
    dispatcher["linking"] = linking
    dispatcher["admin_cache"] = admin_cache
    dispatcher["fanout"] = fanout
    dispatcher["pipeline"] = pipeline
    return dispatcher


def build_bot(token: str, session: BaseSession | None = None) -> Bot:
    return Bot(token=token, session=session) if session is not None else Bot(token=token)


async def run() -> None:
    settings = Settings.load()
    logging.basicConfig(level=settings.log_level)

    run_migrations(settings.database_url)  # applied on start, before polling (§16)

    i18n = build_i18n_middleware()
    session_maker = build_session_maker(settings.database_url)
    clock = SystemClock()
    classifier = build_classifier(
        timeout_s=settings.classifier.timeout_s,
        max_concurrency=settings.classifier.max_concurrency,
    )
    dispatcher = build_dispatcher(
        session_maker=session_maker,
        i18n=i18n,
        clock=clock,
        prompt_delete_after_s=settings.linking.prompt_delete_after_s,
        admin_cache_ttl_s=settings.admin_cache.ttl_s,
        classifier=classifier,
        thresholds=settings.thresholds,
        min_words=settings.moderation.min_words,
        flagged_text_days=settings.retention.flagged_text_days,
        max_notice_lifetime_h=settings.notices.max_lifetime_h,
        outcome_visible_s=settings.notices.outcome_visible_s,
        alerts_pace_s=settings.alerts.pace_s,
    )
    bot = build_bot(settings.bot_token)
    # §11: one loop picks up every due job; Telegram lifts expired
    # Restrictions itself, so no job is needed for that.
    scheduler = Scheduler(bot=bot, session_maker=session_maker, clock=clock)
    scheduler_task = asyncio.create_task(scheduler.run_forever())
    logger.info("starting polling with allowed_updates=%s", ALLOWED_UPDATES)
    try:
        await dispatcher.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        scheduler_task.cancel()
        await i18n.core.shutdown()


if __name__ == "__main__":
    asyncio.run(run())
