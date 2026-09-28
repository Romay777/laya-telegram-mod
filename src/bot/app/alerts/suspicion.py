"""Deciding a Suspicion from an Admin Alert (§9): 🔴 Punish or Dismiss.

The first Admin to press settles it (§9): the conditional update decides,
and every copy of the alert is edited to show who decided, buttons gone.

- **Punish** applies a full Violation in the §6 order — delete, record,
  restrict, Chat Notice — with `source = "admin"`. If the message is older
  than 48 hours, Telegram would refuse the deletion, so it is skipped and
  the alert says so.
- **Dismiss** just closes the Suspicion; nothing happens in the chat.
"""

import contextlib
from dataclasses import dataclass
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import User
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.fanout import AlertFanout
from app.alerts.lift import decided_by, handle_of
from app.clock import Clock
from app.db.models import Chat, MessageCheck, Suspicion
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.moderation import ModerationRepository
from app.db.repositories.suspicions import SuspicionRepository
from app.i18n import translator_for
from app.linking.admin_cache import AdminCache
from app.moderation.actions import restrict_member
from app.notices.sender import removal_time, send_notice

#: Telegram stops allowing deletions after 48 hours (§4, §9).
DELETION_WINDOW = timedelta(hours=48)


@dataclass(frozen=True, slots=True)
class SuspicionDecision:
    """What one press on a Suspicion alert did, and who had decided (§9)."""

    won: bool
    decided_by: str  # the @handle (or name) of the deciding Admin
    expired: bool = False  # the scheduler had already auto-closed it


async def decide_suspicion(
    bot: Bot,
    session: AsyncSession,
    *,
    core: BaseCore,
    clock: Clock,
    fanout: AlertFanout,
    admin_cache: AdminCache,
    chat: Chat,
    suspicion_id: int,
    punish: bool,
    admin: User,
    locale: str,
    max_notice_lifetime_h: int,
) -> SuspicionDecision:
    """Apply one decision press on a Suspicion alert copy (§9)."""
    repo = SuspicionRepository(session)
    suspicion = await repo.get(suspicion_id)
    if suspicion is None:
        return SuspicionDecision(won=False, decided_by="?")

    status = "punished" if punish else "dismissed"
    if not await repo.decide(suspicion_id, status=status, by=admin.id, at=clock.now()):
        return await _already_decided(bot, repo, suspicion_id)

    deleted = False
    if punish:
        deleted = await _apply_punishment(
            bot,
            session,
            core=core,
            clock=clock,
            fanout=fanout,
            admin_cache=admin_cache,
            chat=chat,
            suspicion=suspicion,
            max_notice_lifetime_h=max_notice_lifetime_h,
        )

    t = translator_for(core, locale)
    text = t(
        "alert-suspicion-punished" if punish else "alert-suspicion-dismissed",
        admin=handle_of(admin),
    )
    if punish and not deleted:
        text += "\n" + t("alert-suspicion-not-deleted")
    await _edit_copies(bot, session, suspicion_id, text)
    return SuspicionDecision(won=True, decided_by=handle_of(admin))


async def _already_decided(
    bot: Bot, repo: SuspicionRepository, suspicion_id: int
) -> SuspicionDecision:
    """A late click decides nothing: it learns the outcome instead (§9)."""
    current = await repo.get(suspicion_id)
    if current is None:
        return SuspicionDecision(won=False, decided_by="?")
    if current.status == "expired":
        # Auto-closed while the alert sat unread (§11); nothing to decide.
        return SuspicionDecision(won=False, decided_by="?", expired=True)
    return SuspicionDecision(
        won=False, decided_by=await decided_by(bot, current.chat_id, current.decided_by)
    )


async def _apply_punishment(
    bot: Bot,
    session: AsyncSession,
    *,
    core: BaseCore,
    clock: Clock,
    fanout: AlertFanout,
    admin_cache: AdminCache,
    chat: Chat,
    suspicion: Suspicion,
    max_notice_lifetime_h: int,
) -> bool:
    """The full Violation (§6, §9); returns whether the message was deleted."""
    now = clock.now()
    too_old = now - suspicion.created_at > DELETION_WINDOW

    if not too_old:
        # §6 step 1: delete; if it is already gone, the flow continues.
        with contextlib.suppress(TelegramBadRequest):
            await bot.delete_message(chat_id=chat.chat_id, message_id=suspicion.message_id)

    # §6 steps 2-3: record the Violation, then restrict the Member. A Member
    # already restricted by another Violation still counts this one (§9).
    check = await session.get(MessageCheck, suspicion.check_id)
    violation = await ModerationRepository(session).record_violation(
        chat=chat,
        user_id=suspicion.user_id,
        check_id=suspicion.check_id,
        category=check.category if check is not None else "spam",
        now=now,
        source="admin",
    )
    await restrict_member(
        bot, chat.chat_id, suspicion.user_id, restricted_until=violation.restricted_until
    )
    # §6 step 4: the Chat Notice, with its 🙋 button only while an Appeal
    # would reach an Admin (§7) — the same rule the pipeline applies.
    appeal_recipient = await fanout.has_appeal_recipient(
        bot, session, admin_cache=admin_cache, chat=chat
    )
    notice = await send_notice(
        bot,
        core,
        chat_id=chat.chat_id,
        chat_language=chat.chat_language,
        name=await _member_name(bot, chat.chat_id, suspicion.user_id),
        category=violation.category,
        step_seconds=violation.restriction_seconds or 0,
        appeal_violation_id=violation.id if appeal_recipient else None,
    )
    await ModerationRepository(session).save_notice(
        violation.id,
        message_id=notice.message_id,
        delete_at=removal_time(
            now,
            restricted_until=violation.restricted_until,
            max_lifetime_h=max_notice_lifetime_h,
        ),
    )
    return not too_old


async def _member_name(bot: Bot, chat_id: int, user_id: int) -> str:
    """The Member's display name for the Chat Notice (§7)."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return str(user_id)
    return member.user.first_name or str(user_id)


async def _edit_copies(bot: Bot, session: AsyncSession, suspicion_id: int, text: str) -> None:
    """Every copy of the alert shows the outcome, buttons gone (§9)."""
    copies = await AlertRepository(session).alerts_for("suspicion", suspicion_id)
    for copy in copies:
        # A copy Telegram already dropped cannot be edited; the rest still are.
        with contextlib.suppress(TelegramBadRequest):
            await bot.edit_message_text(
                chat_id=copy.admin_id, message_id=copy.message_id, text=text, reply_markup=None
            )
