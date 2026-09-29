"""Linking and lifecycle handlers: `my_chat_member` updates (§10) and the
Check-again callbacks.

One handler receives every group `my_chat_member` update. A chat that has
no row yet — or a removed one being re-added — belongs to the Linking
flow; an active or Suspended Linked Chat belongs to the lifecycle service.
"""

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, ChatMemberUpdated
from aiogram_i18n import I18nContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BotUser
from app.db.repositories.chats import ChatRepository
from app.lifecycle.service import ChatLifecycleService
from app.linking.service import LinkingService
from app.menu.callbacks import FallbackCheckCallback, LinkCheckCallback
from app.menu.navigator import MenuNavigator


def create_linking_router() -> Router:
    router = Router(name="linking")

    @router.my_chat_member(F.chat.type.in_({"group", "supergroup"}))
    async def bot_membership_changed(
        event: ChatMemberUpdated,
        bot: Bot,
        session: AsyncSession,
        navigator: MenuNavigator,
        linking: LinkingService,
        lifecycle: ChatLifecycleService,
    ) -> None:
        """The bot's standing in a group changed: Linking or lifecycle (§10)."""
        chat = await ChatRepository(session).get(event.chat.id)
        if chat is not None and chat.status != "removed":
            await lifecycle.handle_my_chat_member(bot=bot, session=session, event=event)
            return
        if event.new_chat_member.status != "administrator":
            return  # no Linked Chat and no promotion: nothing to do
        await linking.handle_promotion(
            bot=bot,
            session=session,
            navigator=navigator,
            event=event,
            telegram_language_code=event.from_user.language_code,
        )

    @router.callback_query(LinkCheckCallback.filter(), F.message.chat.type == "private")
    async def check_again(
        callback: CallbackQuery,
        callback_data: LinkCheckCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        linking: LinkingService,
        i18n: I18nContext,
    ) -> None:
        await linking.check_again(
            bot=bot,
            session=session,
            navigator=navigator,
            user=bot_user,
            chat_id=callback_data.chat_id,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(FallbackCheckCallback.filter(), F.message.chat.type == "private")
    async def fallback_check_again(
        callback: CallbackQuery,
        callback_data: FallbackCheckCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        linking: LinkingService,
        i18n: I18nContext,
    ) -> None:
        # The fallback path has no one-hour token: the checks alone decide (§10).
        await linking.fallback_check_again(
            bot=bot,
            session=session,
            navigator=navigator,
            user=bot_user,
            chat_id=callback_data.chat_id,
            locale=i18n.locale,
        )
        await callback.answer()

    return router
