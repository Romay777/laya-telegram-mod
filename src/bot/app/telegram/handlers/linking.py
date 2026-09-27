"""Linking handlers: `my_chat_member` promotions and the Check-again callback (§10)."""

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, ChatMemberUpdated
from aiogram_i18n import I18nContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BotUser
from app.linking.service import LinkingService
from app.menu.callbacks import LinkCheckCallback
from app.menu.navigator import MenuNavigator


def create_linking_router() -> Router:
    router = Router(name="linking")

    @router.my_chat_member(
        F.chat.type.in_({"group", "supergroup"}),  # channels are not Linked Chats
        F.new_chat_member.status == "administrator",
    )
    async def bot_promoted(
        event: ChatMemberUpdated,
        bot: Bot,
        session: AsyncSession,
        navigator: MenuNavigator,
        linking: LinkingService,
    ) -> None:
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
        await linking.check_against(
            bot=bot,
            session=session,
            navigator=navigator,
            user=bot_user,
            chat_id=callback_data.chat_id,
            locale=i18n.locale,
        )
        await callback.answer()

    return router
