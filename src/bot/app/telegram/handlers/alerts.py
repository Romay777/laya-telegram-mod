"""Admin Alert callbacks (§9): the buttons on a private alert copy.

An alert is not the Menu: its buttons decide about a Violation or a
Suspicion directly, in any Admin's private chat, and the chat-scoped access
re-check still applies (§13).
"""

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from aiogram_i18n import I18nContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.fanout import AlertFanout
from app.alerts.lift import lift_violation
from app.alerts.suspicion import decide_suspicion
from app.alerts.unban import unban_channel
from app.clock import Clock
from app.db.repositories.chats import ChatRepository
from app.i18n import translator_for
from app.lifecycle.service import ChatLifecycleService
from app.linking.admin_cache import AdminCache
from app.menu.callbacks import (
    LiftRestrictionCallback,
    SuspicionDecideCallback,
    UnbanChannelCallback,
)
from app.notices.queue import NoticeQueue
from app.notices.sender import DEFAULT_MAX_LIFETIME_H


def create_alerts_router(max_notice_lifetime_h: int = DEFAULT_MAX_LIFETIME_H) -> Router:
    router = Router(name="alerts")

    @router.callback_query(LiftRestrictionCallback.filter(), F.message.chat.type == "private")
    async def lift_restriction(
        callback: CallbackQuery,
        callback_data: LiftRestrictionCallback,
        bot: Bot,
        session: AsyncSession,
        admin_cache: AdminCache,
        clock: Clock,
        i18n: I18nContext,
    ) -> None:
        # Every chat-scoped callback re-checks Admin access (§13).
        if not await admin_cache.is_admin(bot, callback_data.chat_id, callback.from_user.id):
            await callback.answer(
                text=translator_for(i18n.core, i18n.locale)("menu-chat-access-lost"),
                show_alert=False,
            )
            return

        outcome = await lift_violation(
            bot,
            session,
            core=i18n.core,
            clock=clock,
            chat_id=callback_data.chat_id,
            violation_id=callback_data.violation_id,
            admin=callback.from_user,
            locale=i18n.locale,
        )
        if outcome.won:
            await callback.answer()
            return
        # First click wins (§9): a late click learns who was first.
        await callback.answer(
            text=translator_for(i18n.core, i18n.locale)(
                "alert-already-decided", admin=outcome.decided_by
            ),
            show_alert=False,
        )

    @router.callback_query(UnbanChannelCallback.filter(), F.message.chat.type == "private")
    async def unban_channel_press(
        callback: CallbackQuery,
        callback_data: UnbanChannelCallback,
        bot: Bot,
        session: AsyncSession,
        admin_cache: AdminCache,
        i18n: I18nContext,
    ) -> None:
        """🟢 Unban on a foreign channel's Violation alert (§4)."""
        # Every chat-scoped callback re-checks Admin access (§13).
        if not await admin_cache.is_admin(bot, callback_data.chat_id, callback.from_user.id):
            await callback.answer(
                text=translator_for(i18n.core, i18n.locale)("menu-chat-access-lost"),
                show_alert=False,
            )
            return
        await unban_channel(
            bot,
            session,
            core=i18n.core,
            chat_id=callback_data.chat_id,
            violation_id=callback_data.violation_id,
            admin=callback.from_user,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(SuspicionDecideCallback.filter(), F.message.chat.type == "private")
    async def decide_suspicion_press(
        callback: CallbackQuery,
        callback_data: SuspicionDecideCallback,
        bot: Bot,
        session: AsyncSession,
        session_maker: async_sessionmaker[AsyncSession],
        admin_cache: AdminCache,
        clock: Clock,
        fanout: AlertFanout,
        notices: NoticeQueue,
        i18n: I18nContext,
        lifecycle: ChatLifecycleService,
    ) -> None:
        """🔴 Punish / Dismiss on a Suspicion alert copy; first click wins (§9)."""
        # Every chat-scoped callback re-checks Admin access (§13).
        if not await admin_cache.is_admin(bot, callback_data.chat_id, callback.from_user.id):
            await callback.answer(
                text=translator_for(i18n.core, i18n.locale)("menu-chat-access-lost"),
                show_alert=False,
            )
            return
        chat = await ChatRepository(session).get(callback_data.chat_id)
        if chat is None:
            await callback.answer()
            return

        decision = await decide_suspicion(
            bot,
            session,
            core=i18n.core,
            clock=clock,
            fanout=fanout,
            admin_cache=admin_cache,
            chat=chat,
            suspicion_id=callback_data.suspicion_id,
            punish=callback_data.punish,
            admin=callback.from_user,
            locale=i18n.locale,
            max_notice_lifetime_h=max_notice_lifetime_h,
            notices=notices,
            session_maker=session_maker,
            lifecycle=lifecycle,
        )
        if decision.won:
            await callback.answer()
            return
        t = translator_for(i18n.core, i18n.locale)
        if decision.expired:
            # Auto-closed while the alert sat unread (§11).
            await callback.answer(text=t("alert-suspicion-expired"), show_alert=False)
            return
        # First click wins (§9): a late click learns who was first.
        await callback.answer(
            text=t("alert-already-decided", admin=decision.decided_by), show_alert=False
        )

    return router
