"""Admin Alert callbacks (§9): the buttons on a private alert copy.

An alert is not the Menu: its buttons decide about a Violation directly,
in any Admin's private chat, and the chat-scoped access re-check still
applies (§13).
"""

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from aiogram_i18n import I18nContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.lift import lift_violation
from app.clock import Clock
from app.i18n import translator_for
from app.linking.admin_cache import AdminCache
from app.menu.callbacks import LiftRestrictionCallback


def create_alerts_router() -> Router:
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

    return router
