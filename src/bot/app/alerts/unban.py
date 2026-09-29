"""🟢 Unban on a foreign channel's Violation alert (§4).

An Admin press lifts the sender-chat ban (`unbanChatSenderChat`) and every
recorded copy of the alert is edited to show who unbanned, buttons gone.
The press is not a decision to race for — unbanning twice is harmless — so
every copy is simply edited by the winner's click.
"""

import contextlib

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import User
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.lift import handle_of
from app.db.models import Violation
from app.db.repositories.alerts import AlertRepository
from app.i18n import translator_for
from app.moderation.actions import unban_sender_chat


async def unban_channel(
    bot: Bot,
    session: AsyncSession,
    *,
    core: BaseCore,
    chat_id: int,
    violation_id: int,
    admin: User,
    locale: str,
) -> None:
    """Apply one 🟢 Unban press (§4): lift the ban, tell every copy."""
    violation = await session.get(Violation, violation_id)
    if violation is None:
        return
    await unban_sender_chat(bot, chat_id, violation.user_id)

    text = translator_for(core, locale)("alert-channel-unbanned", admin=handle_of(admin))
    for copy in await AlertRepository(session).alerts_for("violation", violation_id):
        # A copy Telegram already dropped cannot be edited; the rest still are.
        with contextlib.suppress(TelegramBadRequest):
            await bot.edit_message_text(
                chat_id=copy.admin_id,
                message_id=copy.message_id,
                text=text,
                reply_markup=None,
            )
