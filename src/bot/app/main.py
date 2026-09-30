"""Wiring: settings, DB, Bot, Dispatcher, scheduler (§2)."""

import asyncio
import logging
from collections.abc import Mapping

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram_i18n import I18nMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.alerts.fanout import AlertFanout
from app.classifiers.client import SystemOneClient
from app.classifiers.health import LayaHealth
from app.classifiers.router import BackendRouter, ClassifierBackend
from app.classifiers.spec import JEV_MODEL_DEFAULT, LAYA_BASE_URL, LAYA_MODEL
from app.clock import Clock, SystemClock
from app.config import ClassifierSettings, Settings, default_thresholds
from app.db.fsm_storage import PostgresStorage
from app.db.migrate import run_migrations
from app.domain.backends import JEV, LAYA, jev_available
from app.i18n.middleware import build_i18n_middleware
from app.lifecycle.service import ChatLifecycleService
from app.linking.admin_cache import AdminCache
from app.linking.service import LinkingService
from app.menu.navigator import MenuNavigator
from app.moderation.pipeline import ModerationPipeline, Thresholds
from app.notices.queue import NoticeQueue
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


def build_models(jev_model: str) -> dict[str, str]:
    """The pinned model of every backend, for the `message_check` rows (§12).

    Both backends are named even when Jev has no key: a stale chat choice
    (§5) still records its checks and skips against Jev, and a missing
    model must never KeyError the pipeline.
    """
    return {LAYA: LAYA_MODEL, JEV: jev_model}


#: The recorded models when the caller brings no router of their own (§12):
#: Laya pins its checkpoint, Jev the ticket's pinned default.
DEFAULT_MODELS = build_models(JEV_MODEL_DEFAULT)


def build_classifier(
    *,
    base_url: str = LAYA_BASE_URL,
    timeout_s: float = ClassifierSettings().timeout_s,
    max_concurrency: int = ClassifierSettings().max_concurrency,
    jev_api_key: str | None = None,
    jev_base_url: str = "https://api.typesafe.ai/v1",
    jev_model: str = "jev-1.13.0",
) -> BackendRouter:
    """The Classifier Backend: both clients behind one router (§5).

    Laya always gets a client — whether its service is actually up is the
    prober's business, and a missing deployment skips quietly. Jev gets one
    only when the Operator set a key; without it Jev is absent from the
    Instance, so the Menu disables it and a stale chat choice skips without
    an incident (absence is not an outage).
    """
    return BackendRouter(
        laya=SystemOneClient(base_url, api_key=None, model=LAYA_MODEL, timeout_s=timeout_s),
        jev=(
            SystemOneClient(jev_base_url, api_key=jev_api_key, model=jev_model, timeout_s=timeout_s)
            if jev_available(jev_api_key)
            else None
        ),
        health=LayaHealth(base_url, timeout_s=timeout_s),
        max_concurrency=max_concurrency,
        models=build_models(jev_model),
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
    models: Mapping[str, str] | None = None,
    flagged_text_days: int = 30,
    max_notice_lifetime_h: int = 24,
    outcome_visible_s: int = 600,
    alerts_pace_s: float = 1.0,
    summary_after_h: int = 48,
    removed_chat_days: int = 30,
    notices: NoticeQueue | None = None,
    notices_per_second: float = 1,
    notices_per_minute: int = 18,
    max_queue_age_s: int = 300,
    shifts: Mapping[str, float] | None = None,
) -> Dispatcher:
    navigator = MenuNavigator(core=i18n.core)
    classifier_ = classifier if classifier is not None else build_classifier()
    linking = LinkingService(
        session_maker=session_maker,
        clock=clock,
        core=i18n.core,
        prompt_delete_after_s=prompt_delete_after_s,
        # §5: a chat linked before Laya ever answered /health starts on Jev.
        laya_deployed=lambda: classifier_.laya_deployed,
    )
    admin_cache = AdminCache(clock=clock, ttl_s=admin_cache_ttl_s)
    fanout = AlertFanout(core=i18n.core, clock=clock, pace_s=alerts_pace_s)
    lifecycle = ChatLifecycleService(
        clock=clock,
        fanout=fanout,
        admin_cache=admin_cache,
        removed_chat_days=removed_chat_days,
    )
    notice_queue = (
        notices
        if notices is not None
        else NoticeQueue(
            clock=clock,
            per_second=notices_per_second,
            per_minute=notices_per_minute,
            max_queue_age_s=max_queue_age_s,
        )
    )
    pipeline = ModerationPipeline(
        clock=clock,
        core=i18n.core,
        backend=classifier if classifier is not None else build_classifier(),
        thresholds=thresholds if thresholds is not None else default_thresholds(),
        models=models if models is not None else classifier_.models,
        flagged_text_days=flagged_text_days,
        max_notice_lifetime_h=max_notice_lifetime_h,
        fanout=fanout,
        notices=notice_queue,
        session_maker=session_maker,
        lifecycle=lifecycle,
        shifts=shifts,
    )
    dispatcher = Dispatcher(storage=PostgresStorage(session_maker))
    dispatcher.update.outer_middleware(DbSessionMiddleware(session_maker))
    dispatcher.update.outer_middleware(BotUserMiddleware(clock=clock))
    i18n.setup(dispatcher)  # locale resolution runs after the DB middlewares
    dispatcher.include_router(create_private_router(summary_after_h=summary_after_h))
    dispatcher.include_router(create_linking_router())
    dispatcher.include_router(create_alerts_router(max_notice_lifetime_h=max_notice_lifetime_h))
    dispatcher.include_router(create_appeals_router(outcome_visible_s=outcome_visible_s))
    dispatcher.include_router(create_group_router())
    dispatcher["navigator"] = navigator
    dispatcher["clock"] = clock
    dispatcher["linking"] = linking
    dispatcher["admin_cache"] = admin_cache
    dispatcher["fanout"] = fanout
    dispatcher["lifecycle"] = lifecycle
    dispatcher["notices"] = notice_queue
    dispatcher["session_maker"] = session_maker
    dispatcher["pipeline"] = pipeline
    dispatcher["classifier"] = classifier if classifier is not None else pipeline.backend
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
        jev_api_key=settings.jev.api_key,
        jev_base_url=settings.jev.base_url,
        jev_model=settings.jev.model,
    )
    dispatcher = build_dispatcher(
        session_maker=session_maker,
        i18n=i18n,
        clock=clock,
        prompt_delete_after_s=settings.linking.prompt_delete_after_s,
        admin_cache_ttl_s=settings.admin_cache.ttl_s,
        classifier=classifier,
        thresholds=settings.thresholds,
        flagged_text_days=settings.retention.flagged_text_days,
        max_notice_lifetime_h=settings.notices.max_lifetime_h,
        outcome_visible_s=settings.notices.outcome_visible_s,
        alerts_pace_s=settings.alerts.pace_s,
        summary_after_h=settings.observation.summary_after_h,
        removed_chat_days=settings.retention.removed_chat_days,
        notices_per_second=settings.notices.per_second,
        notices_per_minute=settings.notices.per_minute,
        max_queue_age_s=settings.notices.max_queue_age_s,
        shifts=dict(settings.signals.__dict__),
    )
    bot = build_bot(settings.bot_token)
    # §11: one loop picks up every due job; Telegram lifts expired
    # Restrictions itself, so no job is needed for that.
    scheduler = Scheduler(
        bot=bot,
        session_maker=session_maker,
        clock=clock,
        core=i18n.core,
        auto_close_h=settings.suspicions.auto_close_h,
        summary_after_h=settings.observation.summary_after_h,
        removed_chat_days=settings.retention.removed_chat_days,
    )
    scheduler_task = asyncio.create_task(scheduler.run_forever())
    # §5: the health prober runs beside the poller at health_interval_s; the
    # router's fallback and the Menu's disabled buttons read its two flags.
    health_task = asyncio.create_task(
        classifier.probe_forever(settings.classifier.health_interval_s)
    )
    logger.info("starting polling with allowed_updates=%s", ALLOWED_UPDATES)
    try:
        await dispatcher.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        health_task.cancel()
        scheduler_task.cancel()
        await classifier.aclose()
        await i18n.core.shutdown()


if __name__ == "__main__":
    asyncio.run(run())
