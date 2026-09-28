"""Moderation actions over the Bot API (§2, §6): delete, restrict.

Lifting a Restriction and banning a sender chat arrive with their tickets.
Deleting tolerates a message that is already gone (§6); a failed
Restriction means the chat is Suspended (§10) and is that ticket's business.
"""

import contextlib
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ChatPermissions, Message


async def delete_message(bot: Bot, message: Message) -> None:
    """Delete the message; if it is already gone, the pipeline continues (§6)."""
    with contextlib.suppress(TelegramBadRequest):
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)


async def restrict_member(
    bot: Bot, chat_id: int, user_id: int, *, restricted_until: datetime | None
) -> None:
    """Restrict with every `can_*` permission off; forever is `until_date = 0` (§6)."""
    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=ChatPermissions(
            can_send_messages=False,
            can_send_audios=False,
            can_send_documents=False,
            can_send_photos=False,
            can_send_videos=False,
            can_send_video_notes=False,
            can_send_voice_notes=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
            can_react_to_messages=False,
            can_edit_tag=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_topics=False,
        ),
        until_date=int(restricted_until.timestamp()) if restricted_until else 0,
        use_independent_chat_permissions=True,
    )
