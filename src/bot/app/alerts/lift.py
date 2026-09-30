"""Lifting a Restriction from an Admin Alert (§6, §9).

Any Admin may press 🟢 Lift restriction on any copy of the alert. The
conditional update decides first-click-wins: the winner's click gives the
Member back the chat's default permissions (read with `getChat`, applied
with `restrictChatMember`), and every recorded copy of the alert is edited
to show who decided, buttons gone. A late click decides nothing and gets
the winner's name for its toast.
"""

import contextlib
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import ChatPermissions, User
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock
from app.db.models import Violation
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.moderation import ModerationRepository
from app.domain.guards import belongs_to_chat
from app.i18n import translator_for
from app.moderation.actions import lift_restriction


@dataclass(frozen=True, slots=True)
class LiftOutcome:
    """What one press of the button did, and who had decided."""

    won: bool
    decided_by: str  # the @handle (or name) of the deciding Admin


def handle_of(user: User | None) -> str:
    """The name an Admin is shown by: @username when there is one."""
    if user is None:
        return "?"
    if user.username:
        return f"@{user.username}"
    return user.first_name or str(user.id)


async def decided_by(bot: Bot, chat_id: int, admin_id: int | None) -> str:
    """The name of the Admin who already decided, read from the chat (§9)."""
    if admin_id is None:
        return "?"
    try:
        member = await bot.get_chat_member(chat_id, admin_id)
    except TelegramAPIError:
        return str(admin_id)
    return handle_of(member.user)


async def lift_violation(
    bot: Bot,
    session: AsyncSession,
    *,
    core: BaseCore,
    clock: Clock,
    chat_id: int,
    violation_id: int,
    admin: User,
    locale: str,
) -> LiftOutcome:
    """Apply one 🟢 Lift restriction press (§9).

    The outcome names the deciding Admin — the clicker when the click won,
    the earlier winner otherwise — for the handler's toasts and edits.
    """
    violation = await session.get(Violation, violation_id)
    if not belongs_to_chat(violation, chat_id):
        # Nothing to lift — or callback data forged for another chat (§13).
        return LiftOutcome(won=False, decided_by="?")

    if not await ModerationRepository(session).revoke_violation(
        violation_id, by=admin.id, at=clock.now()
    ):
        return LiftOutcome(
            won=False, decided_by=await decided_by(bot, chat_id, violation.revoked_by)
        )

    # The Member returns to the chat's normal permissions, not to every
    # permission there is (§6).
    chat = await bot.get_chat(chat_id)
    await lift_restriction(
        bot, chat_id, violation.user_id, permissions=chat.permissions or ChatPermissions()
    )

    text = translator_for(core, locale)("alert-lifted", admin=handle_of(admin))
    for copy in await AlertRepository(session).alerts_for("violation", violation_id):
        # A copy Telegram already dropped cannot be edited; the rest still are.
        with contextlib.suppress(TelegramBadRequest):
            await bot.edit_message_text(
                chat_id=copy.admin_id,
                message_id=copy.message_id,
                text=text,
                reply_markup=None,
            )
    return LiftOutcome(won=True, decided_by=handle_of(admin))
