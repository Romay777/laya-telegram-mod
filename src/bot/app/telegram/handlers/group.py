"""Group handlers (§4): every message and edit a Linked Chat receives.

Text messages and captions alike feed the pipeline; the `edited_message`
re-check treats the edit as a fresh message (§4). `chat_member` updates
invalidate the Admin-status cache, so a promotion or demotion applies at
once (§4 step 1).
"""

from aiogram import Bot, F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.chats import ChatRepository
from app.linking.admin_cache import AdminCache
from app.moderation.pipeline import ModerationPipeline


def create_group_router() -> Router:
    router = Router(name="group")

    async def handle(
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

    @router.message(
        F.chat.type.in_({"group", "supergroup"}),
        F.text | F.caption,  # captions are checked too (§4 step 3)
    )
    async def group_message(
        message: Message,
        bot: Bot,
        session: AsyncSession,
        admin_cache: AdminCache,
        pipeline: ModerationPipeline,
    ) -> None:
        await handle(message, bot, session, admin_cache, pipeline)

    @router.edited_message(
        F.chat.type.in_({"group", "supergroup"}),
        F.text | F.caption,
    )
    async def group_edited_message(
        message: Message,
        bot: Bot,
        session: AsyncSession,
        admin_cache: AdminCache,
        pipeline: ModerationPipeline,
    ) -> None:
        await handle(message, bot, session, admin_cache, pipeline)

    @router.chat_member()
    async def chat_member_changed(
        event: object,
        bot: Bot,
        admin_cache: AdminCache,
        session: AsyncSession,
    ) -> None:
        """A membership changed: the cached admin list is invalidated (§4 step 1)."""
        chat_id = getattr(getattr(event, "chat", None), "id", None)
        new_member = getattr(event, "new_chat_member", None)
        user_id = getattr(getattr(new_member, "user", None), "id", None)
        if chat_id is not None and user_id is not None:
            admin_cache.invalidate(chat_id, user_id)

    return router
