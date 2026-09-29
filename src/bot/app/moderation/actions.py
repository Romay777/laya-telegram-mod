"""Moderation actions over the Bot API (§2, §6): delete, restrict, lift, ban.

Deleting tolerates a message that is already gone (§6); a failed Restriction
means the chat is Suspended (§10) and is that ticket's business. Banning a
sender chat is the foreign-channel action of §4.
"""

import contextlib
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ChatPermissions, Message


async def delete_message(bot: Bot, message: Message) -> None:
    """Delete the message; if it is already gone, the pipeline continues (§6)."""
    await delete_chat_message(bot, message.chat.id, message.message_id)


async def delete_chat_message(bot: Bot, chat_id: int, message_id: int) -> None:
    """Delete by ids; if it is already gone, the flow continues (§6)."""
    with contextlib.suppress(TelegramBadRequest):
        await bot.delete_message(chat_id=chat_id, message_id=message_id)


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


async def ban_sender_chat(bot: Bot, chat_id: int, sender_chat_id: int) -> None:
    """Ban a channel from posting in the chat (§4): `banChatSenderChat`."""
    await bot.ban_chat_sender_chat(chat_id=chat_id, sender_chat_id=sender_chat_id)


async def unban_sender_chat(bot: Bot, chat_id: int, sender_chat_id: int) -> None:
    """Lift a sender-chat ban (§4): the 🟢 Unban button on the Admin Alert."""
    await bot.unban_chat_sender_chat(chat_id=chat_id, sender_chat_id=sender_chat_id)


async def lift_restriction(
    bot: Bot, chat_id: int, user_id: int, *, permissions: ChatPermissions
) -> None:
    """Give the Member back the chat's normal permissions (§6).

    `permissions` are the chat's own defaults, read with `getChat`; a chat
    that reports none is treated as allowing nothing beyond Telegram's
    baseline. `until_date = 0`: no timed Restriction on the way back.
    """
    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=permissions,
        until_date=0,
    )
