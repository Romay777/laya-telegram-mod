"""The Appeal flow (§8): filing from the Chat Notice, deciding from an alert.

Filing runs the three checks — the presser is the restricted Member, this
is the first Appeal for the Violation, the Violation is not already
revoked — then creates the `appeal` row as `pending`, replaces the notice
button with the pending text, and fans out the Appeal Admin Alerts.
Deciding is first-click-wins like every alert decision (§9): the first
Admin press settles the Appeal, the notice shows the outcome in the Chat
Language, and every alert copy is edited to show who decided.
"""

import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ChatPermissions, User
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.fanout import AlertFanout
from app.alerts.lift import decided_by, handle_of
from app.clock import Clock
from app.db.models import Appeal, Chat, ChatNotice, FlaggedMessage, MessageCheck, Violation
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.appeals import AppealRepository
from app.db.repositories.moderation import ModerationRepository
from app.domain.guards import belongs_to_chat
from app.i18n import GetText, translator_for
from app.linking.admin_cache import AdminCache
from app.moderation.actions import lift_restriction


@dataclass(frozen=True, slots=True)
class FiledAppeal:
    """What one press of the Appeal button did.

    `toast` names a `notices.ftl` key when a check refused the Appeal; a
    successful filing carries the new appeal id and no toast.
    """

    appeal_id: int | None = None
    toast: str | None = None


def notice_text_translator(core: BaseCore, chat: Chat) -> GetText:
    """The translator of the Chat Language: every group text speaks it (§15)."""
    return translator_for(core, chat.chat_language)


async def file_appeal(
    bot: Bot,
    session: AsyncSession,
    *,
    core: BaseCore,
    clock: Clock,
    fanout: AlertFanout,
    admin_cache: AdminCache,
    chat: Chat,
    violation: Violation,
    presser: User,
) -> FiledAppeal:
    """Run the §8 checks and file the Appeal; a refused check names its toast."""
    if not belongs_to_chat(violation, chat.chat_id):
        # The callback data is forged for another chat: nothing is filed (§13).
        return FiledAppeal()
    repo = AppealRepository(session)
    if await repo.by_violation(violation.id) is not None:
        return FiledAppeal(toast="notice-appeal-already-sent")
    if violation.revoked_at is not None:
        return FiledAppeal(toast="notice-appeal-too-late")
    if violation.notice_dropped:
        # §7: the notice never made it out of the queue, so no Appeal
        # is possible for this Violation.
        return FiledAppeal(toast="notice-appeal-dropped")
    appeal = await repo.create(violation.id, created_at=clock.now())
    if appeal is None:  # a concurrent filing won the UNIQUE constraint (§8)
        return FiledAppeal(toast="notice-appeal-already-sent")

    await _swap_notice_button_for_pending_text(bot, session, core, chat, violation.id)

    check = await session.get(MessageCheck, violation.check_id)
    flagged = await session.get(FlaggedMessage, violation.check_id)
    await fanout.appeal_alert(
        bot,
        session,
        admin_cache=admin_cache,
        chat=chat,
        member_name=presser.first_name or str(presser.id),
        category=violation.category,
        confidence=check.confidence if check is not None else 0.0,
        step_seconds=violation.restriction_seconds or 0,
        flagged_text=flagged.text if flagged is not None else None,
        flagged_entities=flagged.entities if flagged is not None else None,
        appeal_id=appeal.id,
    )
    return FiledAppeal(appeal_id=appeal.id)


async def _swap_notice_button_for_pending_text(
    bot: Bot,
    session: AsyncSession,
    core: BaseCore,
    chat: Chat,
    violation_id: int,
) -> None:
    """The notice button is replaced by the pending text (§8): markup, then text.

    A notice Telegram already dropped cannot be edited; the Appeal stands.
    """
    notice = await session.get(ChatNotice, violation_id)
    if notice is None:
        return
    t = notice_text_translator(core, chat)
    with contextlib.suppress(TelegramBadRequest):
        await bot.edit_message_reply_markup(
            chat_id=chat.chat_id, message_id=notice.message_id, reply_markup=None
        )
    with contextlib.suppress(TelegramBadRequest):
        await bot.edit_message_text(
            chat_id=chat.chat_id, message_id=notice.message_id, text=t("notice-appeal-sent")
        )


@dataclass(frozen=True, slots=True)
class AppealDecision:
    """What one press of a decision button did, and who had decided (§9)."""

    won: bool
    decided_by: str  # the @handle (or name) of the deciding Admin


async def decide_appeal(
    bot: Bot,
    session: AsyncSession,
    *,
    core: BaseCore,
    clock: Clock,
    chat: Chat,
    appeal_id: int,
    approve: bool,
    admin: User,
    locale: str,
    outcome_visible_s: int,
) -> AppealDecision:
    """Apply one decision press on an Appeal alert copy (§8).

    Approve lifts the Restriction (§6) and turns the Violation into a False
    Positive; Reject leaves it standing. Either way the notice shows the
    outcome and is scheduled for removal after `outcome_visible_s`, and
    every alert copy shows the outcome and who decided, buttons gone.
    """
    appeal = await session.get(Appeal, appeal_id)
    if appeal is None:
        return AppealDecision(won=False, decided_by="?")
    violation = await session.get(Violation, appeal.violation_id)
    if not belongs_to_chat(violation, chat.chat_id):
        # Nothing to decide — or callback data forged for another chat (§13).
        return AppealDecision(won=False, decided_by="?")

    status = "approved" if approve else "rejected"
    if not await AppealRepository(session).decide(
        appeal_id, status=status, by=admin.id, at=clock.now()
    ):
        return AppealDecision(
            won=False, decided_by=await decided_by(bot, chat.chat_id, appeal.decided_by)
        )

    t = notice_text_translator(core, chat)
    if approve:
        # §6: the Member returns to the chat's normal permissions, and the
        # Violation becomes a False Positive that counts no more.
        tg_chat = await bot.get_chat(chat.chat_id)
        await lift_restriction(
            bot,
            chat.chat_id,
            violation.user_id,
            permissions=tg_chat.permissions or ChatPermissions(),
        )
        await ModerationRepository(session).revoke_violation(
            violation.id, by=admin.id, at=clock.now()
        )
        notice_text = t("notice-appeal-approved")
    else:
        notice_text = t("notice-appeal-rejected")

    await _show_outcome_on_notice(
        bot, session, chat, violation.id, notice_text, clock.now(), outcome_visible_s
    )

    text = translator_for(core, locale)(
        "alert-lifted" if approve else "alert-appeal-rejected", admin=handle_of(admin)
    )
    for copy in await AlertRepository(session).alerts_for("appeal", appeal_id):
        # A copy Telegram already dropped cannot be edited; the rest still are.
        with contextlib.suppress(TelegramBadRequest):
            await bot.edit_message_text(
                chat_id=copy.admin_id,
                message_id=copy.message_id,
                text=text,
                reply_markup=None,
            )
    return AppealDecision(won=True, decided_by=handle_of(admin))


async def _show_outcome_on_notice(
    bot: Bot,
    session: AsyncSession,
    chat: Chat,
    violation_id: int,
    text: str,
    now: datetime,
    outcome_visible_s: int,
) -> None:
    """Put the outcome on the notice and bring its removal forward (§7, §8).

    The notice is deleted at the earliest of its timers, so resolution
    moves `delete_at` to `outcome_visible_s` from now unless that is later
    than the removal already scheduled.
    """
    notice = await session.get(ChatNotice, violation_id)
    if notice is None:
        return
    notice.delete_at = min(notice.delete_at, now + timedelta(seconds=outcome_visible_s))
    await session.flush()
    with contextlib.suppress(TelegramBadRequest):
        await bot.edit_message_text(chat_id=chat.chat_id, message_id=notice.message_id, text=text)
