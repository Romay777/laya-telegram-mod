"""Linking handlers: `my_chat_member` promotions and the Check-again callback (§10)."""

from aiogram import Bot, F, Router
from aiogram.types import ChatMemberUpdated
from sqlalchemy.ext.asyncio import AsyncSession

from app.linking.service import LinkingService
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

    return router
