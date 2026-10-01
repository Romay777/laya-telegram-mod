"""The moderation pipeline (§4): filters → check → Decision → actions.

One entry point per incoming group message or edit. Each step can stop the
pipeline; the actions on a Violation run in the §6 order — delete, record,
restrict, enqueue the notice — and the recording rules of §4 step 9 hold
throughout: every check writes a `message_check` row without text, and
only flagged messages keep theirs, in `flagged_message` until the
retention passes. The Chat Notice itself goes through the per-chat
rate-limited queue (§7); deleting and restricting are never delayed by it.

The §3 signals — the Member row plus the text — move both thresholds only;
the adjusted values are clamped to [0.05, 0.99], and the shift values come
from config. An edit is checked again from scratch (§4), but edits to
messages older than 48 hours are skipped: Telegram no longer allows
deleting them, so a Violation could no longer be carried out. An open
Suspicion on the same message is never alerted twice, and a new Violation
closes it as `superseded`.

A `sender_chat` that is neither the chat itself nor its linked channel is
a foreign channel. Its Violation deletes the message and bans the sender
chat outright — no ladder, no Restriction of a user, no Chat Notice, no
Appeal (§4) — and the Admins get an Admin Alert with a 🟢 Unban button.
"""

import time
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.fanout import AlertFanout
from app.classifiers.client import Probabilities
from app.classifiers.router import BackendFailure, CheckSkip, ClassifierBackend
from app.classifiers.spec import SPEC_VERSION
from app.clock import Clock
from app.db.models import Chat
from app.db.repositories.chats import ChatRepository
from app.db.repositories.incidents import IncidentRepository
from app.db.repositories.members import MemberRepository
from app.db.repositories.moderation import ModerationRepository
from app.db.repositories.notice_templates import NoticeTemplateRepository
from app.db.repositories.suspicions import SuspicionRepository
from app.domain.backends import BACKEND_NAMES, JEV
from app.domain.decision import OUTCOME_SUSPICION, OUTCOME_VIOLATION, decide
from app.domain.signals import adjusted_thresholds, is_established, is_new, threshold_shifts
from app.lifecycle.service import ChatLifecycleService
from app.linking.admin_cache import AdminCache
from app.moderation import signals
from app.moderation.actions import ban_sender_chat, delete_message, restrict_member
from app.notices.anchor import NoticeAnchor, notice_anchor
from app.notices.jobs import violation_notice
from app.notices.queue import NoticeQueue

#: The §3 Sensitivity presets, keyed `[backend][sensitivity]`; each entry
#: carries `.violation`. Any mapping shaped like `Settings.thresholds` fits.
Thresholds = Mapping[str, Mapping[str, Any]]

#: The model recorded per serving backend (§12): Laya pins its checkpoint.
Models = Mapping[str, str]

#: The §3 shift values, keyed as config.toml's `[signals]` spells them.
SignalShifts = Mapping[str, float]

#: The §3 default shifts, used when the caller brings none from config.
DEFAULT_SHIFTS: dict[str, float] = {
    "new_member_link": -0.10,
    "invite_link": -0.05,
    "established_member": 0.05,
}

#: Telegram stops allowing deletions after 48 hours (§4 step 2, §9).
DELETION_WINDOW = timedelta(hours=48)


class ModerationPipeline:
    def __init__(
        self,
        *,
        clock: Clock,
        core: BaseCore,
        backend: ClassifierBackend,
        thresholds: Thresholds,
        models: Models,
        flagged_text_days: int,
        max_notice_lifetime_h: int,
        fanout: AlertFanout,
        notices: NoticeQueue,
        session_maker: async_sessionmaker[AsyncSession],
        lifecycle: ChatLifecycleService,
        shifts: SignalShifts | None = None,
    ) -> None:
        self._clock = clock
        self._core = core
        self._backend = backend
        self._thresholds = thresholds
        self._models = models
        self._flagged_text_days = flagged_text_days
        self._max_notice_lifetime_h = max_notice_lifetime_h
        self._fanout = fanout
        self._notices = notices
        self._session_maker = session_maker
        self._lifecycle = lifecycle
        self._shifts: SignalShifts = shifts if shifts is not None else DEFAULT_SHIFTS

    @property
    def backend(self) -> ClassifierBackend:
        """The router behind this pipeline: its availability feeds the Menu (§5)."""
        return self._backend

    async def handle_message(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        chat: Chat,
        message: Message,
        admin_cache: AdminCache,
    ) -> None:
        """Check one group message — or edit — of a Linked Chat and act (§4).

        A Suspended Chat checks nothing (§10). Both modes check and record:
        Auto-moderation acts on Violations itself, while Observation Mode
        turns every flagged Verdict into a Suspicion for the Admins (§4).
        """
        if chat.status != "active":
            return

        # §4 step 1: the Exempt Sender filters. Automatic forwards from the
        # linked channel and anonymous admins (the chat speaking as itself)
        # are decided from the update alone; every other sender_chat is a
        # channel post and stays in the pipeline — a foreign one is checked
        # like any other message (§4), and only a linked channel of the chat
        # is exempt.
        if message.is_automatic_forward:
            return
        sender = message.from_user
        if message.sender_chat is not None:
            if message.sender_chat.id == chat.chat_id:
                return  # an anonymous admin speaks as the chat itself (§4 step 1)
            if message.sender_chat.id == await self._linked_channel_id(bot, chat):
                return  # a post from the chat's own linked channel (§4 step 1)
            # Any other sender_chat is a foreign channel: it is checked like
            # any other message (§4), attributed to the channel itself.
        elif sender is None or sender.is_bot:
            return  # no sender at all, or a bot (§4 step 1)
        if sender is not None and await admin_cache.is_admin(bot, chat.chat_id, sender.id):
            return

        # §4 step 2: edits to messages older than 48 hours are skipped —
        # Telegram no longer allows deleting them.
        is_edit = message.edit_date is not None
        if is_edit and self._clock.now() - message.date > DELETION_WINDOW:
            return

        # §4 step 3: the text is `text` or `caption`; a bare photo or a
        # sticker has neither and is skipped.
        text = message.text or message.caption
        if text is None:
            return
        entities = [
            entity.model_dump(mode="json", exclude_none=True)
            for entity in message.entities or message.caption_entities or []
        ]
        now = self._clock.now()
        repo = ModerationRepository(session)

        # §4 step 4: the short-message filter, at the chat's own minimum
        # length (§13). Short skips never reach the classifier, so they count
        # on no Member row (§3 reads the history of *checked* messages).
        if signals.is_short(text=text, entities=entities, min_chars=chat.min_chars):
            await self._record(
                repo,
                chat,
                message_sender_id(message),
                message.message_id,
                backend=chat.backend,
                outcome="skipped_short",
                created_at=now,
                is_edit=is_edit,
            )
            return

        # §4 step 5: the Member's history as it stood before this check (§3).
        # A channel sender has no Member row and takes no member signals.
        members = MemberRepository(session)
        facts = None if sender is None else await members.facts(chat.chat_id, sender.id)

        # §4 steps 6-8: classify with the chat's backend, then decide.
        started = time.monotonic()
        check = await self._backend.check(chat.backend, signals.extract_state(text, entities))
        latency_ms = round((time.monotonic() - started) * 1000)

        if check.failure is not None and check.failure.backend == JEV:
            # §5 scopes incidents to the Jev backend; a Laya failure leaves
            # the chat quiet — there is nothing to fall back to.
            await self._note_failure(
                bot,
                session,
                admin_cache,
                chat=chat,
                failure=check.failure,
                now=now,
                using_laya=isinstance(check.outcome, dict),
            )

        if isinstance(check.outcome, CheckSkip):
            await self._record(
                repo,
                chat,
                message_sender_id(message),
                message.message_id,
                backend=check.served_by,
                outcome=str(check.outcome),
                created_at=now,
                is_edit=is_edit,
            )
            await members.note_checked(
                chat.chat_id, message_sender_id(message), flagged=False, at=now
            )
            return

        # A served answer closes any open incident of the serving backend (§5).
        await self._note_success(
            bot, session, admin_cache, chat=chat, served_by=check.served_by, now=now
        )

        violation_threshold, suspicion_threshold = adjusted_thresholds(
            violation_threshold=self._violation_threshold(check.served_by, chat.sensitivity),
            suspicion_threshold=self._suspicion_threshold(check.served_by, chat.sensitivity),
            shifts=threshold_shifts(
                # No row at all is the newest kind of Member: first contact.
                is_new_member=facts is None
                or is_new(
                    first_seen_at=facts.first_seen_at,
                    checked_count=facts.checked_count,
                    now=now,
                ),
                has_link_invite_or_mention=signals.has_link_invite_or_mention(text, entities),
                has_invite_link=signals.has_invite_link(text, entities),
                is_established_member=facts is not None
                and is_established(
                    first_seen_at=facts.first_seen_at,
                    checked_count=facts.checked_count,
                    flagged_count=facts.flagged_count,
                    now=now,
                ),
                shifts=self._shifts,
            ),
        )

        decision = decide(
            probabilities=check.outcome,
            violation_threshold=violation_threshold,
            suspicion_threshold=suspicion_threshold,
            mode=chat.mode,
            enabled_categories=await ChatRepository(session).enabled_categories(chat.chat_id),
        )
        # §7: where a notice for this message would land — a forum topic or
        # the comment thread under a channel post — frozen now, from the
        # message itself, before either producer needs it.
        anchor = notice_anchor(message)
        # The check itself lands on the Member's row; a flagged Verdict
        # counts as flagged (§3: an Established Member is never flagged).
        await members.note_checked(
            chat.chat_id,
            message_sender_id(message),
            flagged=decision.outcome in (OUTCOME_VIOLATION, OUTCOME_SUSPICION),
            at=now,
        )
        recorded = await self._record(
            repo,
            chat,
            message_sender_id(message),
            message.message_id,
            backend=check.served_by,
            outcome=decision.outcome,
            created_at=now,
            category=decision.category,
            confidence=decision.confidence,
            probabilities=dict(check.outcome),
            latency_ms=latency_ms,
            is_edit=is_edit,
        )
        if decision.outcome == OUTCOME_SUSPICION:
            await self._raise_suspicion(
                bot,
                session,
                repo,
                chat=chat,
                sender_id=message_sender_id(message),
                member=sender,
                message_id=message.message_id,
                check_id=recorded.id,
                text=text,
                entities=entities,
                category=decision.category,
                confidence=decision.confidence,
                now=now,
                admin_cache=admin_cache,
                anchor=anchor,
            )
            return
        if decision.outcome != OUTCOME_VIOLATION:
            return

        # §4 step 9: the flagged message keeps its text until the retention passes.
        await repo.store_flagged(
            recorded.id,
            text=text,
            entities=entities,
            purge_at=now + timedelta(days=self._flagged_text_days),
        )

        if message.sender_chat is not None:
            # A foreign channel: delete and ban the sender chat — no ladder,
            # no Chat Notice, no Appeal (§4).
            await self._punish_channel(
                bot,
                session,
                repo,
                admin_cache=admin_cache,
                chat=chat,
                message=message,
                recorded=recorded,
                category=decision.category,
                confidence=decision.confidence,
                text=text,
                entities=entities,
                now=now,
            )
            return

        # §6 order of actions: delete → record → restrict → notice. A
        # Restriction that fails for lack of rights suspends the chat
        # (§6, §10): the Violation stands, but no Notice and no alert of
        # the Violation follows — the Suspension alert takes over.
        await delete_message(bot, message)
        violation = await repo.record_violation(
            chat=chat,
            user_id=sender.id,
            check_id=recorded.id,
            category=decision.category,
            now=now,
        )
        try:
            await restrict_member(
                bot, chat.chat_id, sender.id, restricted_until=violation.restricted_until
            )
        except TelegramForbiddenError:
            await self._suspend_chat(bot, session, chat)
            return
        except TelegramBadRequest as failure:
            if "not enough rights" not in str(failure):
                raise
            await self._suspend_chat(bot, session, chat)
            return
        # §4: a new Violation closes the message's open Suspicion as superseded.
        await SuspicionRepository(session).supersede_open(chat.chat_id, message.message_id, at=now)
        # The Violation becomes durable before anything background refers to
        # it: the queue's drain posts the notice from its own session, which
        # cannot see this request's uncommitted transaction.
        await session.commit()
        # §6 steps 4-5: the Chat Notice goes into the per-chat rate-limited
        # queue (§7) — deleting and restricting above already happened, and
        # the Admin Alerts below do not wait for it. The template (§14) and
        # the 🙋 button are decided here: the 🙋 button is on the notice only
        # while an Appeal would reach an Admin (§7); a channel sender gets
        # no button either way (§4).
        appeal_recipient = await self._fanout.has_appeal_recipient(
            bot, session, admin_cache=admin_cache, chat=chat
        )
        template = await NoticeTemplateRepository(session).get(chat.chat_id)
        self._notices.enqueue(
            violation_notice(
                session_maker=self._session_maker,
                bot=bot,
                core=self._core,
                clock=self._clock,
                fanout=self._fanout,
                chat=chat,
                violation=violation,
                member_id=sender.id,
                member_name=sender.first_name or str(sender.id),
                category=decision.category,
                confidence=decision.confidence,
                flagged_text=text,
                flagged_entities=entities,
                max_lifetime_h=self._max_notice_lifetime_h,
                appeal_violation_id=violation.id if appeal_recipient else None,
                template_text=template.text if template is not None else None,
                template_entities=template.entities if template is not None else None,
                anchor=anchor,
            )
        )
        await self._fanout.violation_alert(
            bot,
            session,
            admin_cache=admin_cache,
            chat=chat,
            member_name=sender.first_name or str(sender.id),
            category=decision.category,
            confidence=decision.confidence,
            step_seconds=violation.restriction_seconds or 0,
            flagged_text=text,
            flagged_entities=entities,
            violation_id=violation.id,
        )

    async def _suspend_chat(self, bot: Bot, session: AsyncSession, chat: Chat) -> None:
        """The chat just lost its rights mid-Violation: suspend (§10).

        The Violation is already durable — the Restriction is Telegram's
        business once the rights are back — and the Suspension alert is
        committed with the update's own transaction.
        """
        await session.commit()
        await self._lifecycle.suspend_on_failed_restriction(bot, session, chat)

    async def _punish_channel(
        self,
        bot: Bot,
        session: AsyncSession,
        repo: ModerationRepository,
        *,
        admin_cache: AdminCache,
        chat: Chat,
        message: Message,
        recorded: Any,
        category: str,
        confidence: float,
        text: str,
        entities: list[dict[str, Any]],
        now: datetime,
    ) -> None:
        """A foreign channel's Violation: delete, then ban the sender chat (§4).

        There is no Penalty Ladder, no Restriction of a user, no Chat Notice
        and no Appeal in this case. The Violation row is recorded without a
        Restriction — §12 reserves a NULL `restriction_seconds` for the
        sender-chat ban — and the Admins get an alert with a 🟢 Unban button.
        """
        sender_chat = message.sender_chat
        assert sender_chat is not None  # the caller checked
        await delete_message(bot, message)
        violation = await repo.record_violation(
            chat=chat,
            user_id=sender_chat.id,
            check_id=recorded.id,
            category=category,
            now=now,
            sender_chat_ban=True,
        )
        await ban_sender_chat(bot, chat.chat_id, sender_chat.id)
        # Durable before the alert fan-out reads the copies back (§9).
        await session.commit()
        await self._fanout.channel_violation_alert(
            bot,
            session,
            admin_cache=admin_cache,
            chat=chat,
            channel_id=sender_chat.id,
            channel_title=sender_chat.title or str(sender_chat.id),
            category=category,
            confidence=confidence,
            flagged_text=text,
            flagged_entities=entities,
            violation_id=violation.id,
        )

    async def _linked_channel_id(self, bot: Bot, chat: Chat) -> int | None:
        """The chat's linked channel, as Telegram reports it (§4 step 1)."""
        try:
            full = await bot.get_chat(chat.chat_id)
        except TelegramAPIError:
            return None  # no answer: nothing to match the sender_chat against
        return full.linked_chat_id

    async def _record(
        self,
        repo: ModerationRepository,
        chat: Chat,
        user_id: int,
        message_id: int,
        *,
        backend: str,
        outcome: str,
        created_at: datetime,
        category: str | None = None,
        confidence: float | None = None,
        probabilities: Probabilities | None = None,
        latency_ms: int | None = None,
        is_edit: bool = False,
    ) -> Any:
        """One `message_check` row: the Verdict and probabilities, no text (§12).

        The row names the backend that served the check — after a fallback
        that is Laya, with Laya's model — never the chat's choice (§5).
        """
        return await repo.record_check(
            chat_id=chat.chat_id,
            user_id=user_id,
            message_id=message_id,
            backend=backend,
            model=self._models[backend],
            spec_version=SPEC_VERSION,
            outcome=outcome,
            created_at=created_at,
            category=category,
            confidence=confidence,
            probabilities=probabilities,
            latency_ms=latency_ms,
            is_edit=is_edit,
        )

    async def _note_failure(
        self,
        bot: Bot,
        session: AsyncSession,
        admin_cache: AdminCache,
        *,
        chat: Chat,
        failure: BackendFailure,
        now: datetime,
        using_laya: bool,
    ) -> None:
        """The first failure opens the incident and alerts once (§5).

        `open` returns the already-open incident unchanged while one is open
        (a run of failures is one incident), so the alert goes out only when
        this call actually created the row — a restart does not re-alert for
        what is still open.
        """
        repo = IncidentRepository(session)
        already_open = await repo.open_id(failure.backend) is not None
        incident = await repo.open(failure.backend, reason=failure.reason, opened_at=now)
        if already_open:
            return
        await self._fanout.backend_incident_alert(
            bot,
            session,
            admin_cache=admin_cache,
            chat=chat,
            backend=BACKEND_NAMES[failure.backend],
            reason=failure.reason,
            incident_id=incident.id,
            using_laya=using_laya,
        )

    async def _note_success(
        self,
        bot: Bot,
        session: AsyncSession,
        admin_cache: AdminCache,
        *,
        chat: Chat,
        served_by: str,
        now: datetime,
    ) -> None:
        """The first success closes the serving backend's incident (§5).

        The "back" follow-up goes to every chat that was told about it —
        which may be more chats than this one.
        """
        repo = IncidentRepository(session)
        open_id = await repo.open_id(served_by)
        if open_id is None:
            return
        if not await repo.close(open_id, closed_at=now):
            return
        await self._fanout.backend_recovery_alerts(
            bot,
            session,
            admin_cache=admin_cache,
            incident_id=open_id,
            backend=BACKEND_NAMES[served_by],
        )

    async def _raise_suspicion(
        self,
        bot: Bot,
        session: AsyncSession,
        repo: ModerationRepository,
        *,
        chat: Chat,
        sender_id: int,
        member: Any,
        message_id: int,
        check_id: int,
        text: str,
        entities: list[dict[str, Any]],
        category: str,
        confidence: float,
        now: datetime,
        admin_cache: AdminCache,
        anchor: NoticeAnchor | None = None,
    ) -> None:
        """The middle band — or anything flagged in Observation Mode (§4, §9).

        The message stays in the chat; its text is kept for the alert and
        the later decision, and the Admins subscribed with `all` are asked
        to 🔴 Punish or Dismiss. A message that already has an open
        Suspicion — its original was flagged, then the edit was too — gets
        no second row and no second alert (§4). The notice anchor (§7) is
        stored on the row: a later Punish posts the notice there.
        """
        # §4 step 9: the flagged message keeps its text until the retention passes.
        await repo.store_flagged(
            check_id,
            text=text,
            entities=entities,
            purge_at=now + timedelta(days=self._flagged_text_days),
        )
        suspicions = SuspicionRepository(session)
        if await suspicions.open_id(chat.chat_id, message_id) is not None:
            return
        suspicion = await suspicions.create(
            check_id=check_id,
            chat_id=chat.chat_id,
            user_id=sender_id,
            message_id=message_id,
            created_at=now,
            anchor_kind=anchor.kind if anchor is not None else None,
            anchor_message_id=anchor.message_id if anchor is not None else None,
        )
        await self._fanout.suspicion_alert(
            bot,
            session,
            admin_cache=admin_cache,
            chat=chat,
            member_name=getattr(member, "first_name", None) or str(sender_id),
            category=category,
            confidence=confidence,
            suspicion_id=suspicion.id,
            message_id=message_id,
            flagged_text=text,
            flagged_entities=entities,
        )

    def _violation_threshold(self, backend: str, sensitivity: str) -> float:
        """The Sensitivity preset for the backend that served the check (§3, §5).

        After a fallback that is Laya, with Laya's thresholds — never the
        chat's choice. Per-Category overrides are reserved for later.
        """
        return self._thresholds[backend][sensitivity].violation

    def _suspicion_threshold(self, backend: str, sensitivity: str) -> float:
        """The lower zone's threshold, from the same Sensitivity preset (§3)."""
        return self._thresholds[backend][sensitivity].suspicion


def message_sender_id(message: Message) -> int:
    """Who a check is attributed to: the Member, or the channel (§12)."""
    if message.from_user is not None:
        return message.from_user.id
    assert message.sender_chat is not None
    return message.sender_chat.id
