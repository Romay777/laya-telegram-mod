"""Group handlers (§4): every text message a Linked Chat receives."""

from aiogram import Bot, F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.chats import ChatRepository
from app.linking.admin_cache import AdminCache
from app.moderation.pipeline import ModerationPipeline


def create_group_router() -> Router:
    router = Router(name="group")

    @router.message(F.chat.type.in_({"group", "supergroup"}), F.text)
    async def group_message(
        message: Message,
        bot: Bot,
        session: AsyncSession,
        admin_cache: AdminCache,
        pipeline: ModerationPipeline,
    ) -> None:
        # A chat that never linked has no row and no pipeline (§10).
        chat = await ChatRepository(session).get(message.chat.id)
        if chat is None:
            return
        await pipeline.handle_message(
            bot=bot, session=session, chat=chat, message=message, admin_cache=admin_cache
        )

    return router
