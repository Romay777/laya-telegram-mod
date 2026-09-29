"""Admin Alert fan-out (§9): Violations and Appeals reach their subscribers.

Recipients are the chat's current Admins (Telegram is the source of Admin
status, checked through the AdminCache) whose alert mode includes the
subject — Violations go to `all`, Appeals to `all` and `appeals`. Each
alert goes out as a private message in that Admin's language, paced about
one message per second per Admin; a 403 marks the Admin unreachable and
they are skipped from then on. Every sent message is recorded in
`admin_alert`, so any decision can later edit every copy.

In a raid the Violation alerts burst (§9): the first five per chat per
minute go out one by one, and the rest of that minute is covered by one
summary alert per chat — "N violations in the last minute in {chat}",
with an Open journal button. Restrictions and deletions carry on
regardless.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.rendering import (
    message_link,
    render_appeal_alert,
    render_channel_alert,
    render_incident_alert,
    render_lifecycle_alert,
    render_suspicion_alert,
    render_violation_alert,
)
from app.clock import Clock
from app.db.models import BotUser, Chat
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.chats import ChatRepository
from app.db.repositories.subscriptions import AdminSubscriptionRepository
from app.db.repositories.users import BotUserRepository
from app.i18n import GetText, translator_for
from app.linking.admin_cache import AdminCache
from app.menu.callbacks import (
    AppealDecideCallback,
    JournalCallback,
    LiftRestrictionCallback,
    LinkCheckCallback,
    SuspicionDecideCallback,
    UnbanChannelCallback,
)
from app.menu.screens.buttons import DANGER, PRIMARY, SUCCESS

#: The private-chat limit: about one message per second per Admin (§9).
PACE_S = 1.0

#: The language of an Admin who never picked one (§15 fallback).
FALLBACK_LANGUAGE = "en"

#: The alert modes that receive Appeals (§9): `all` and `appeals`.
APPEAL_MODES = ("all", "appeals")

#: The alert modes that receive backend incidents (§9): the same pair.
INCIDENT_MODES = ("all", "appeals")

#: The alert modes that receive Suspension and removal (§9): the same pair.
LIFECYCLE_MODES = ("all", "appeals")

#: §9 burst handling: the first five Violation alerts per chat per minute
#: go out one by one; the rest of that minute shares one summary alert.
BURST_AFTER = 5

#: The length of a burst window: one summary alert per chat per minute (§9).
BURST_WINDOW = timedelta(minutes=1)


@dataclass(slots=True)
class _Burst:
    """One chat's burst window: the minute since its first Violation alert."""

    started_at: datetime
    individuals: int = 0
    overflow: int = 0


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


def appeal_keyboard(t: GetText, chat_id: int, appeal_id: int) -> InlineKeyboardMarkup:
    """The two decision buttons of an Appeal alert (§8): 🟢 Lift, 🔴 Reject."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("alert-lift-button"),
                    callback_data=AppealDecideCallback(
                        chat_id=chat_id, appeal_id=appeal_id, approve=True
                    ).pack(),
                    style=SUCCESS,
                ),
                InlineKeyboardButton(
                    text=t("alert-reject-button"),
                    callback_data=AppealDecideCallback(
                        chat_id=chat_id, appeal_id=appeal_id, approve=False
                    ).pack(),
                    style=DANGER,
                ),
            ]
        ]
    )


def journal_keyboard(t: GetText, chat_id: int) -> InlineKeyboardMarkup:
    """The Open journal button of a burst summary (§9)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("alert-open-journal"),
                    callback_data=JournalCallback(chat_id=chat_id).pack(),
                )
            ]
        ]
    )


def suspicion_keyboard(t: GetText, chat_id: int, suspicion_id: int) -> InlineKeyboardMarkup:
    """The two decision buttons of a Suspicion alert (§9): 🔴 Punish · Dismiss."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("alert-punish-button"),
                    callback_data=SuspicionDecideCallback(
                        chat_id=chat_id, suspicion_id=suspicion_id, punish=True
                    ).pack(),
                    style=DANGER,
                ),
                InlineKeyboardButton(
                    text=t("alert-dismiss-button"),
                    callback_data=SuspicionDecideCallback(
                        chat_id=chat_id, suspicion_id=suspicion_id, punish=False
                    ).pack(),
                ),
            ]
        ]
    )


def unban_keyboard(t: GetText, chat_id: int, violation_id: int) -> InlineKeyboardMarkup:
    """The 🟢 Unban button of a foreign channel's Violation alert (§4)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("alert-unban-button"),
                    callback_data=UnbanChannelCallback(
                        chat_id=chat_id, violation_id=violation_id
                    ).pack(),
                    style=SUCCESS,
                )
            ]
        ]
    )


def check_again_keyboard(t: GetText, chat_id: int) -> InlineKeyboardMarkup:
    """The 🔵 Check again button of a Suspension alert (§9, §10).

    The Linking Check-again callback doubles here: its re-check reads the
    chat's status and reactivates a Suspended Chat whose rights are back.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("alert-check-again-button"),
                    callback_data=LinkCheckCallback(chat_id=chat_id).pack(),
                    style=PRIMARY,
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
        self._bursts: dict[int, _Burst] = {}

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
        """Send the Violation alert to every Admin whose mode is `all` (§9).

        In a burst — more than five Violations in one chat in a minute —
        the rest of the minute is covered by one summary alert instead (§9).
        """
        burst = self._burst_of(chat.chat_id, self._clock.now())
        if burst.individuals < BURST_AFTER:
            burst.individuals += 1
            await self._violation_alert_copy(
                bot,
                session,
                admin_cache=admin_cache,
                chat=chat,
                member_name=member_name,
                category=category,
                confidence=confidence,
                step_seconds=step_seconds,
                flagged_text=flagged_text,
                flagged_entities=flagged_entities,
                violation_id=violation_id,
            )
            return
        burst.overflow += 1
        await self._burst_summary(bot, session, admin_cache=admin_cache, chat=chat)

    async def _violation_alert_copy(
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
        """One Violation alert, one copy per `all`-mode Admin (§9)."""
        recipients = await AdminSubscriptionRepository(session).user_ids_with_mode(
            chat.chat_id, alert_mode="all"
        )
        async for admin_id, _user, t in self._recipients(
            bot, session, admin_cache, chat, recipients
        ):
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
            await self._send_alert(
                bot,
                session,
                chat=chat,
                admin_id=admin_id,
                t=t,
                text=text,
                entities=entities,
                markup=lift_keyboard(t, chat.chat_id, violation_id),
                subject_type="violation",
                subject_id=violation_id,
            )

    def _burst_of(self, chat_id: int, now: datetime) -> _Burst:
        """The chat's burst window: a fresh one each minute (§9)."""
        burst = self._bursts.get(chat_id)
        if burst is None or now - burst.started_at >= BURST_WINDOW:
            burst = _Burst(started_at=now)
            self._bursts[chat_id] = burst
        return burst

    async def _burst_summary(
        self,
        bot: Bot,
        session: AsyncSession,
        *,
        admin_cache: AdminCache,
        chat: Chat,
    ) -> None:
        """The one summary alert per chat per minute, kept up to date (§9).

        The first overflow Violation of a minute sends it; the rest edit
        every copy in place, so one alert still covers the whole minute.
        Copies are read back from `admin_alert`, so the summary survives a
        restart within the minute.
        """
        burst = self._bursts[chat.chat_id]
        subject_id = int(burst.started_at.timestamp())
        copies = [
            copy
            for copy in await AlertRepository(session).alerts_for("burst", subject_id)
            if copy.chat_id == chat.chat_id
        ]
        if not copies:
            recipients = await AdminSubscriptionRepository(session).user_ids_with_mode(
                chat.chat_id, alert_mode="all"
            )
            async for admin_id, _user, t in self._recipients(
                bot, session, admin_cache, chat, recipients
            ):
                await self._send_alert(
                    bot,
                    session,
                    chat=chat,
                    admin_id=admin_id,
                    t=t,
                    text=t("alert-burst-summary", count=burst.overflow, chat=chat.title),
                    entities=[],
                    markup=journal_keyboard(t, chat.chat_id),
                    subject_type="burst",
                    subject_id=subject_id,
                )
            return
        for copy in copies:
            t = await self._translator_of(session, copy.admin_id)
            with contextlib.suppress(TelegramBadRequest):
                await bot.edit_message_text(
                    chat_id=copy.admin_id,
                    message_id=copy.message_id,
                    text=t("alert-burst-summary", count=burst.overflow, chat=chat.title),
                    reply_markup=journal_keyboard(t, chat.chat_id),
                )

    async def notice_not_sent(
        self,
        bot: Bot,
        session: AsyncSession,
        *,
        chat: Chat,
        violation_id: int,
        member_name: str,
        category: str,
        confidence: float,
        step_seconds: int,
        flagged_text: str | None,
        flagged_entities: list[dict[str, Any]] | None,
    ) -> None:
        """Append "notice not sent (rate limit)" to every copy of the Violation
        alert (§7): the notice aged out of the queue, so the drop shows where
        the Admins read about the Violation. Violations covered by a burst
        summary have no individual copy — the summary already stands in."""
        for copy in await AlertRepository(session).alerts_for("violation", violation_id):
            t = await self._translator_of(session, copy.admin_id)
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
            with contextlib.suppress(TelegramBadRequest):
                await bot.edit_message_text(
                    chat_id=copy.admin_id,
                    message_id=copy.message_id,
                    text=f"{text}\n{t('alert-notice-not-sent')}",
                    entities=[MessageEntity.model_validate(entity) for entity in entities],
                )

    async def backend_incident_alert(
        self,
        bot: Bot,
        session: AsyncSession,
        *,
        admin_cache: AdminCache,
        chat: Chat,
        backend: str,
        reason: str,
        incident_id: int,
        using_laya: bool,
    ) -> None:
        """Send the incident alert to every Admin whose mode includes
        incidents (§5, §9): "⚠️ Jev is unavailable: …", with the fallback
        named only while Laya actually serves the check.

        No buttons; every copy is recorded under the subject `incident`, so
        the recovery follow-up reaches exactly the chats that were told.
        """
        subscribers = await AdminSubscriptionRepository(session).user_ids_with_modes(
            chat.chat_id, modes=INCIDENT_MODES
        )
        async for admin_id, _user, t in self._recipients(
            bot, session, admin_cache, chat, subscribers
        ):
            await self._send_alert(
                bot,
                session,
                chat=chat,
                admin_id=admin_id,
                t=t,
                text=render_incident_alert(
                    t, backend=backend, reason=reason, recovering=False, using_laya=using_laya
                ),
                entities=[],
                markup=None,
                subject_type="incident",
                subject_id=incident_id,
            )

    async def backend_recovery_alerts(
        self,
        bot: Bot,
        session: AsyncSession,
        *,
        admin_cache: AdminCache,
        incident_id: int,
        backend: str,
    ) -> None:
        """The "✅ Jev is back" follow-up (§5): one per chat that was told.

        The chats come from the incident's recorded alert copies, so an
        incident nobody was alerted about closes silently.
        """
        copies = await AlertRepository(session).alerts_for("incident", incident_id)
        told_chats = {copy.chat_id for copy in copies}
        for chat_id in told_chats:
            chat = await ChatRepository(session).get(chat_id)
            if chat is None:
                continue
            subscribers = await AdminSubscriptionRepository(session).user_ids_with_modes(
                chat.chat_id, modes=INCIDENT_MODES
            )
            async for admin_id, _user, t in self._recipients(
                bot, session, admin_cache, chat, subscribers
            ):
                await self._send_alert(
                    bot,
                    session,
                    chat=chat,
                    admin_id=admin_id,
                    t=t,
                    text=render_incident_alert(t, backend=backend, reason="", recovering=True),
                    entities=[],
                    markup=None,
                    subject_type="incident",
                    subject_id=incident_id,
                )

    async def channel_violation_alert(
        self,
        bot: Bot,
        session: AsyncSession,
        *,
        admin_cache: AdminCache,
        chat: Chat,
        channel_id: int,
        channel_title: str,
        category: str,
        confidence: float,
        flagged_text: str | None,
        flagged_entities: list[dict[str, Any]] | None,
        violation_id: int,
    ) -> None:
        """A foreign channel was banned over one message (§4).

        No ladder, no Restriction, no Chat Notice and no Appeal exists here;
        the Admins subscribed with `all` get the facts and a 🟢 Unban button,
        recorded under the subject `violation` so an unban edits every copy.
        """
        recipients = await AdminSubscriptionRepository(session).user_ids_with_mode(
            chat.chat_id, alert_mode="all"
        )
        async for admin_id, _user, t in self._recipients(
            bot, session, admin_cache, chat, recipients
        ):
            text, entities = render_channel_alert(
                t,
                chat_title=chat.title or str(chat.chat_id),
                channel_title=channel_title,
                category=category,
                confidence=confidence,
                flagged_text=flagged_text,
                flagged_entities=flagged_entities,
            )
            await self._send_alert(
                bot,
                session,
                chat=chat,
                admin_id=admin_id,
                t=t,
                text=text,
                entities=entities,
                markup=unban_keyboard(t, chat.chat_id, violation_id),
                subject_type="violation",
                subject_id=violation_id,
            )

    async def suspension_alert(
        self,
        bot: Bot,
        session: AsyncSession,
        *,
        admin_cache: AdminCache,
        chat: Chat,
        missing_rights: tuple[str, ...],
        removed_chat_days: int,
    ) -> None:
        """The chat was Suspended: what is missing, with 🔵 Check again (§9, §10).

        Every Admin whose mode includes Suspension gets a copy, recorded
        under the subject `lifecycle`; a re-activation edits every copy.
        """
        subscribers = await AdminSubscriptionRepository(session).user_ids_with_modes(
            chat.chat_id, modes=LIFECYCLE_MODES
        )
        async for admin_id, _user, t in self._recipients(
            bot, session, admin_cache, chat, subscribers
        ):
            await self._send_alert(
                bot,
                session,
                chat=chat,
                admin_id=admin_id,
                t=t,
                text=render_lifecycle_alert(
                    t,
                    chat_title=chat.title or str(chat.chat_id),
                    kind="suspended",
                    missing_rights=missing_rights,
                    removed_chat_days=removed_chat_days,
                ),
                entities=[],
                markup=check_again_keyboard(t, chat.chat_id),
                subject_type="lifecycle",
                subject_id=chat.chat_id,
            )

    async def removal_alert(
        self,
        bot: Bot,
        session: AsyncSession,
        *,
        admin_cache: AdminCache,
        chat: Chat,
        removed_chat_days: int,
    ) -> None:
        """The bot was removed: the settings are kept for the window (§9, §10)."""
        subscribers = await AdminSubscriptionRepository(session).user_ids_with_modes(
            chat.chat_id, modes=LIFECYCLE_MODES
        )
        async for admin_id, _user, t in self._recipients(
            bot, session, admin_cache, chat, subscribers
        ):
            await self._send_alert(
                bot,
                session,
                chat=chat,
                admin_id=admin_id,
                t=t,
                text=render_lifecycle_alert(
                    t,
                    chat_title=chat.title or str(chat.chat_id),
                    kind="removed",
                    missing_rights=(),
                    removed_chat_days=removed_chat_days,
                ),
                entities=[],
                markup=None,
                subject_type="lifecycle",
                subject_id=chat.chat_id,
            )

    async def reactivation_alerts(
        self,
        bot: Bot,
        session: AsyncSession,
        *,
        admin_cache: AdminCache,
        chat: Chat,
        removed_chat_days: int,
    ) -> None:
        """The chat is active again: every Suspension copy shows the all-clear (§10).

        The copies were recorded under the subject `lifecycle`; the news
        goes to whoever holds one, and to any subscribed Admin who was
        never told (§10: the Linker is notified).
        """
        copies = await AlertRepository(session).alerts_for("lifecycle", chat.chat_id)
        for copy in copies:
            t = await self._translator_of(session, copy.admin_id)
            with contextlib.suppress(TelegramBadRequest):
                await bot.edit_message_text(
                    chat_id=copy.admin_id,
                    message_id=copy.message_id,
                    text=render_lifecycle_alert(
                        t,
                        chat_title=chat.title or str(chat.chat_id),
                        kind="reactivated",
                        missing_rights=(),
                        removed_chat_days=removed_chat_days,
                    ),
                    reply_markup=None,
                )
        told = {copy.admin_id for copy in copies}
        subscribers = await AdminSubscriptionRepository(session).user_ids_with_modes(
            chat.chat_id, modes=LIFECYCLE_MODES
        )
        async for admin_id, _user, t in self._recipients(
            bot, session, admin_cache, chat, subscribers
        ):
            if admin_id in told:
                continue  # their copy was just edited; no second message
            await self._send_alert(
                bot,
                session,
                chat=chat,
                admin_id=admin_id,
                t=t,
                text=render_lifecycle_alert(
                    t,
                    chat_title=chat.title or str(chat.chat_id),
                    kind="reactivated",
                    missing_rights=(),
                    removed_chat_days=removed_chat_days,
                ),
                entities=[],
                markup=None,
                subject_type="lifecycle",
                subject_id=chat.chat_id,
            )

    async def _translator_of(self, session: AsyncSession, admin_id: int) -> GetText:
        """The translator of an Admin's interface language (§15)."""
        user = await BotUserRepository(session).get(admin_id)
        locale = user.language if user is not None and user.language else FALLBACK_LANGUAGE
        return translator_for(self._core, locale)

    async def appeal_alert(
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
        appeal_id: int,
    ) -> None:
        """Send the Appeal alert to every Admin whose mode includes Appeals (§8, §9)."""
        subscribers = await AdminSubscriptionRepository(session).user_ids_with_modes(
            chat.chat_id, modes=APPEAL_MODES
        )
        async for admin_id, _user, t in self._recipients(
            bot, session, admin_cache, chat, subscribers
        ):
            text, entities = render_appeal_alert(
                t,
                chat_title=chat.title or str(chat.chat_id),
                member_name=member_name,
                category=category,
                confidence=confidence,
                step_seconds=step_seconds,
                flagged_text=flagged_text,
                flagged_entities=flagged_entities,
            )
            await self._send_alert(
                bot,
                session,
                chat=chat,
                admin_id=admin_id,
                t=t,
                text=text,
                entities=entities,
                markup=appeal_keyboard(t, chat.chat_id, appeal_id),
                subject_type="appeal",
                subject_id=appeal_id,
            )

    async def suspicion_alert(
        self,
        bot: Bot,
        session: AsyncSession,
        *,
        admin_cache: AdminCache,
        chat: Chat,
        member_name: str,
        category: str,
        confidence: float,
        suspicion_id: int,
        message_id: int,
        flagged_text: str | None,
        flagged_entities: list[dict[str, Any]] | None,
    ) -> None:
        """Send the Suspicion alert to every Admin whose mode is `all` (§9).

        The message stays in the chat, so the alert links to it; the 🔴
        Punish and Dismiss buttons let the first Admin decide.
        """
        recipients = await AdminSubscriptionRepository(session).user_ids_with_mode(
            chat.chat_id, alert_mode="all"
        )
        url = message_link(chat.chat_id, message_id)
        async for admin_id, _user, t in self._recipients(
            bot, session, admin_cache, chat, recipients
        ):
            text, entities = render_suspicion_alert(
                t,
                chat_title=chat.title or str(chat.chat_id),
                member_name=member_name,
                category=category,
                confidence=confidence,
                url=url,
                flagged_text=flagged_text,
                flagged_entities=flagged_entities,
            )
            await self._send_alert(
                bot,
                session,
                chat=chat,
                admin_id=admin_id,
                t=t,
                text=text,
                entities=entities,
                markup=suspicion_keyboard(t, chat.chat_id, suspicion_id),
                subject_type="suspicion",
                subject_id=suspicion_id,
            )

    async def _send_alert(
        self,
        bot: Bot,
        session: AsyncSession,
        *,
        chat: Chat,
        admin_id: int,
        t: GetText,
        text: str,
        entities: list[dict[str, Any]],
        markup: InlineKeyboardMarkup | None,
        subject_type: str,
        subject_id: int,
    ) -> None:
        """One copy of an alert, paced: send it, mark 403s, record it (§9)."""
        await self._pace_for(admin_id)
        try:
            message = await bot.send_message(
                chat_id=admin_id,
                text=text,
                entities=[MessageEntity.model_validate(entity) for entity in entities],
                reply_markup=markup,
            )
        except TelegramForbiddenError:
            await BotUserRepository(session).mark_unreachable(
                admin_id, started_at=self._clock.now()
            )
            return
        await AlertRepository(session).record_alert(
            chat.chat_id,
            admin_id=admin_id,
            message_id=message.message_id,
            subject_type=subject_type,
            subject_id=subject_id,
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
