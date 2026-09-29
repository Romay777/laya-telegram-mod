"""The moderation pipeline (§4): filters → check → Decision → actions.

One entry point per incoming group message. Each step can stop the
pipeline; the actions on a Violation run in the §6 order — delete, record,
restrict, enqueue the notice — and the recording rules of §4 step 9 hold
throughout: every check writes a `message_check` row without text, and
only flagged messages keep theirs, in `flagged_message` until the
retention passes. The Chat Notice itself goes through the per-chat
rate-limited queue (§7); deleting and restricting are never delayed by it.
"""

import time
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot
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
from app.db.repositories.moderation import ModerationRepository
from app.db.repositories.notice_templates import NoticeTemplateRepository
from app.db.repositories.suspicions import SuspicionRepository
from app.domain.backends import BACKEND_NAMES
from app.domain.decision import decide
from app.linking.admin_cache import AdminCache
from app.moderation import signals
from app.moderation.actions import delete_message, restrict_member
from app.notices.jobs import violation_notice
from app.notices.queue import NoticeQueue

#: The §3 Sensitivity presets, keyed `[backend][sensitivity]`; each entry
#: carries `.violation`. Any mapping shaped like `Settings.thresholds` fits.
Thresholds = Mapping[str, Mapping[str, Any]]

#: The model recorded per serving backend (§12): Laya pins its checkpoint.
Models = Mapping[str, str]


class ModerationPipeline:
    def __init__(
        self,
        *,
        clock: Clock,
        core: BaseCore,
        backend: ClassifierBackend,
        thresholds: Thresholds,
        models: Models,
        min_words: int,
        flagged_text_days: int,
        max_notice_lifetime_h: int,
        fanout: AlertFanout,
        notices: NoticeQueue,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._clock = clock
        self._core = core
        self._backend = backend
        self._thresholds = thresholds
        self._models = models
        self._min_words = min_words
        self._flagged_text_days = flagged_text_days
        self._max_notice_lifetime_h = max_notice_lifetime_h
        self._fanout = fanout
        self._notices = notices
        self._session_maker = session_maker

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
        """Check one group message of a Linked Chat and act on the Decision (§4).

        A Suspended Chat checks nothing (§10). Both modes check and record:
        Auto-moderation acts on Violations itself, while Observation Mode
        turns every flagged Verdict into a Suspicion for the Admins (§4).
        """
        if chat.status != "active":
            return

        # §4 step 1: Exempt Senders are never checked, not even recorded.
        sender = message.from_user
        if sender is None or sender.is_bot or message.is_automatic_forward:
            return
        if await admin_cache.is_admin(bot, chat.chat_id, sender.id):
            return

        # §4 step 3: text messages only this ticket.
        text = message.text
        if text is None:
            return
        entities = [
            entity.model_dump(mode="json", exclude_none=True) for entity in message.entities or []
        ]
        now = self._clock.now()
        repo = ModerationRepository(session)

        # §4 step 4: the short-message filter.
        if signals.is_short(text=text, entities=entities, min_words=self._min_words):
            await self._record(
                repo,
                chat,
                sender.id,
                message.message_id,
                backend=chat.backend,
                outcome="skipped_short",
                created_at=now,
            )
            return

        # §4 steps 6-8: classify with the chat's backend, then decide.
        started = time.monotonic()
        check = await self._backend.check(chat.backend, signals.extract_state(text, entities))
        latency_ms = round((time.monotonic() - started) * 1000)

        if check.failure is not None:
            await self._note_failure(
                bot, session, admin_cache, chat=chat, failure=check.failure, now=now
            )

        if isinstance(check.outcome, CheckSkip):
            await self._record(
                repo,
                chat,
                sender.id,
                message.message_id,
                backend=check.served_by,
                outcome=str(check.outcome),
                created_at=now,
            )
            return

        # A served answer closes any open incident of the serving backend (§5).
        await self._note_success(
            bot, session, admin_cache, chat=chat, served_by=check.served_by, now=now
        )

        decision = decide(
            probabilities=check.outcome,
            violation_threshold=self._violation_threshold(check.served_by, chat.sensitivity),
            suspicion_threshold=self._suspicion_threshold(check.served_by, chat.sensitivity),
            mode=chat.mode,
            enabled_categories=await ChatRepository(session).enabled_categories(chat.chat_id),
        )
        recorded = await self._record(
            repo,
            chat,
            sender.id,
            message.message_id,
            backend=check.served_by,
            outcome=decision.outcome,
            created_at=now,
            category=decision.category,
            confidence=decision.confidence,
            probabilities=dict(check.outcome),
            latency_ms=latency_ms,
        )
        if decision.outcome == "suspicion":
            await self._raise_suspicion(
                bot,
                session,
                repo,
                chat=chat,
                sender=sender,
                message_id=message.message_id,
                check_id=recorded.id,
                text=text,
                entities=entities,
                category=decision.category,
                confidence=decision.confidence,
                now=now,
                admin_cache=admin_cache,
            )
            return
        if decision.outcome != "violation":
            return

        # §4 step 9: the flagged message keeps its text until the retention passes.
        await repo.store_flagged(
            recorded.id,
            text=text,
            entities=entities,
            purge_at=now + timedelta(days=self._flagged_text_days),
        )

        # §6 order of actions: delete → record → restrict → notice.
        await delete_message(bot, message)
        violation = await repo.record_violation(
            chat=chat,
            user_id=sender.id,
            check_id=recorded.id,
            category=decision.category,
            now=now,
        )
        await restrict_member(
            bot, chat.chat_id, sender.id, restricted_until=violation.restricted_until
        )
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
        sender: Any,
        message_id: int,
        check_id: int,
        text: str,
        entities: list[dict[str, Any]],
        category: str,
        confidence: float,
        now: datetime,
        admin_cache: AdminCache,
    ) -> None:
        """The middle band — or anything flagged in Observation Mode (§4, §9).

        The message stays in the chat; its text is kept for the alert and
        the later decision, and the Admins subscribed with `all` are asked
        to 🔴 Punish or Dismiss.
        """
        # §4 step 9: the flagged message keeps its text until the retention passes.
        await repo.store_flagged(
            check_id,
            text=text,
            entities=entities,
            purge_at=now + timedelta(days=self._flagged_text_days),
        )
        suspicion = await SuspicionRepository(session).create(
            check_id=check_id,
            chat_id=chat.chat_id,
            user_id=sender.id,
            message_id=message_id,
            created_at=now,
        )
        await self._fanout.suspicion_alert(
            bot,
            session,
            admin_cache=admin_cache,
            chat=chat,
            member_name=sender.first_name or str(sender.id),
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
