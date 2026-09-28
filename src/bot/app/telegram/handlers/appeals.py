"""Appeal handlers (§8): the Chat Notice button and the alert decision buttons.

The Appeal button is a Member's — the notice lives in the Linked Chat, so
its toasts speak the Chat Language (§15). The decision buttons live on
private alert copies and are Admin-scoped: every chat-scoped callback
re-checks Admin access (§13).
"""

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from aiogram_i18n import I18nContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.appeal import decide_appeal, file_appeal
from app.alerts.fanout import AlertFanout
from app.clock import Clock
from app.db.models import Violation
from app.db.repositories.chats import ChatRepository
from app.i18n import translator_for
from app.linking.admin_cache import AdminCache
from app.menu.callbacks import AppealCallback, AppealDecideCallback

#: How long a resolved notice stays visible before the scheduler removes it.
DEFAULT_OUTCOME_VISIBLE_S = 600


def create_appeals_router(outcome_visible_s: int = DEFAULT_OUTCOME_VISIBLE_S) -> Router:
    router = Router(name="appeals")

    @router.callback_query(
        AppealCallback.filter(), F.message.chat.type.in_({"group", "supergroup"})
    )
    async def appeal_button(
        callback: CallbackQuery,
        callback_data: AppealCallback,
        bot: Bot,
        session: AsyncSession,
        clock: Clock,
        fanout: AlertFanout,
        admin_cache: AdminCache,
        i18n: I18nContext,
    ) -> None:
        violation = await session.get(Violation, callback_data.violation_id)
        chat = await ChatRepository(session).get(callback_data.chat_id)
        if violation is None or chat is None or callback.from_user is None:
            await callback.answer()
            return

        # Check 1 of §8: the presser must be the restricted Member. Group
        # text — toasts included — speaks the Chat Language (§15).
        t = translator_for(i18n.core, chat.chat_language)
        if callback.from_user.id != violation.user_id:
            await callback.answer(text=t("notice-appeal-not-for-you"))
            return

        outcome = await file_appeal(
            bot,
            session,
            core=i18n.core,
            clock=clock,
            fanout=fanout,
            admin_cache=admin_cache,
            chat=chat,
            violation=violation,
            presser=callback.from_user,
        )
        await callback.answer(text=t(outcome.toast) if outcome.toast else None)

    @router.callback_query(AppealDecideCallback.filter(), F.message.chat.type == "private")
    async def decide(
        callback: CallbackQuery,
        callback_data: AppealDecideCallback,
        bot: Bot,
        session: AsyncSession,
        clock: Clock,
        admin_cache: AdminCache,
        i18n: I18nContext,
    ) -> None:
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

        decision = await decide_appeal(
            bot,
            session,
            core=i18n.core,
            clock=clock,
            chat=chat,
            appeal_id=callback_data.appeal_id,
            approve=callback_data.approve,
            admin=callback.from_user,
            locale=i18n.locale,
            outcome_visible_s=outcome_visible_s,
        )
        if decision.won:
            await callback.answer()
            return
        # First click wins (§9): a late click learns who was first.
        await callback.answer(
            text=translator_for(i18n.core, i18n.locale)(
                "alert-already-decided", admin=decision.decided_by
            ),
            show_alert=False,
        )

    return router
