"""Admin Alert fan-out (§9): a Violation reaches every subscribed Admin.

Recipients are the chat's current Admins (Telegram is the source of Admin
status, checked through the AdminCache) whose alert mode for the chat is
`all`. Each alert goes out as a private message in that Admin's language,
paced about one message per second per Admin; a 403 marks the Admin
unreachable and they are skipped from then on. Every sent message is
recorded in `admin_alert`, so any decision can later edit every copy.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.rendering import render_violation_alert
from app.clock import Clock
from app.db.models import BotUser, Chat
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.subscriptions import AdminSubscriptionRepository
from app.db.repositories.users import BotUserRepository
from app.i18n import GetText, translator_for
from app.linking.admin_cache import AdminCache
from app.menu.callbacks import LiftRestrictionCallback
from app.menu.screens.buttons import SUCCESS

#: The private-chat limit: about one message per second per Admin (§9).
PACE_S = 1.0

#: The language of an Admin who never picked one (§15 fallback).
FALLBACK_LANGUAGE = "en"

#: The alert modes that receive Appeals (§9): `all` and `appeals`.
APPEAL_MODES = ("all", "appeals")


def lift_keyboard(t: GetText, chat_id: int, violation_id: int) -> InlineKeyboardMarkup:
    """The 🟢 Lift restriction button of a Violation alert (§9)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("alert-lift-button"),
                    callback_data=LiftRestrictionCallback(
                        chat_id=chat_id, violation_id=violation_id
                    ).pack(),
                    style=SUCCESS,
                )
            ]
        ]
    )


class AlertFanout:
    def __init__(
        self,
        *,
        core: BaseCore,
        clock: Clock,
        pace_s: float = PACE_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._core = core
        self._clock = clock
        self._pace_s = pace_s
        self._sleep = sleep
        self._last_sent: dict[int, datetime] = {}

    async def violation_alert(
        self,
        bot: Bot,
        session: AsyncSession,
        *,
        admin_cache: AdminCache,
        chat: Chat,
        member_name: str,
        category: str,
        confidence: float,
        step_seconds: int,
        flagged_text: str | None,
        flagged_entities: list[dict[str, Any]] | None,
        violation_id: int,
    ) -> None:
        """Send the Violation alert to every Admin whose mode is `all` (§9)."""
        recipients = await AdminSubscriptionRepository(session).user_ids_with_mode(
            chat.chat_id, alert_mode="all"
        )
        async for admin_id, user, t in self._recipients(
            bot, session, admin_cache, chat, recipients
        ):
            await self._pace_for(admin_id)
            text, entities = render_violation_alert(
                t,
                chat_title=chat.title or str(chat.chat_id),
                member_name=member_name,
                category=category,
                confidence=confidence,
                step_seconds=step_seconds,
                flagged_text=flagged_text,
                flagged_entities=flagged_entities,
            )
            try:
                message = await bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    entities=[MessageEntity.model_validate(entity) for entity in entities],
                    reply_markup=lift_keyboard(t, chat.chat_id, violation_id),
                )
            except TelegramForbiddenError:
                await BotUserRepository(session).mark_unreachable(
                    admin_id, started_at=self._clock.now()
                )
                continue
            await AlertRepository(session).record_alert(
                chat.chat_id,
                admin_id=admin_id,
                message_id=message.message_id,
                subject_type="violation",
                subject_id=violation_id,
            )

    async def has_appeal_recipient(
        self,
        bot: Bot,
        session: AsyncSession,
        *,
        admin_cache: AdminCache,
        chat: Chat,
    ) -> bool:
        """Whether an Appeal would reach at least one Admin of the chat (§7, §9).

        The Chat Notice carries its 🙋 button only while this holds: the
        recipients are the current Admins (Telegram is the source of Admin
        status) who have started the bot and subscribe at `all` or `appeals`.
        """
        subscribers = await AdminSubscriptionRepository(session).user_ids_with_modes(
            chat.chat_id, modes=APPEAL_MODES
        )
        async for _admin_id, _user, _t in self._recipients(
            bot, session, admin_cache, chat, subscribers
        ):
            return True
        return False

    async def _recipients(
        self,
        bot: Bot,
        session: AsyncSession,
        admin_cache: AdminCache,
        chat: Chat,
        admin_ids: list[int],
    ) -> AsyncIterator[tuple[int, BotUser | None, GetText]]:
        """Yield the given subscribers an alert can actually reach (§9, §10)."""
        for admin_id in admin_ids:
            user = await BotUserRepository(session).get(admin_id)
            if user is not None and not user.reachable:
                continue  # a 403 marked them unreachable: skipped from then on (§9)
            if not await admin_cache.is_admin(bot, chat.chat_id, admin_id):
                continue  # no longer an Admin: alerts stop, the row is kept (§10)
            t = translator_for(
                self._core,
                user.language if user is not None and user.language else FALLBACK_LANGUAGE,
            )
            yield admin_id, user, t

    async def _pace_for(self, admin_id: int) -> None:
        """Wait out the private-chat pace this Admin's alerts must keep."""
        last = self._last_sent.get(admin_id)
        now = self._clock.now()
        if last is not None:
            delay = self._pace_s - (now - last).total_seconds()
            if delay > 0:
                await self._sleep(delay)
        self._last_sent[admin_id] = now
