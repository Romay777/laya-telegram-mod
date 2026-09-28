"""The moderation pipeline (§4): filters → check → Decision → actions.

One entry point per incoming group message. Each step can stop the
pipeline; the actions on a Violation run in the §6 order — delete, record,
restrict, notice — and the recording rules of §4 step 9 hold throughout:
every check writes a `message_check` row without text, and only flagged
messages keep theirs, in `flagged_message` until the retention passes.
"""

import time
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.types import Message
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.fanout import AlertFanout
from app.classifiers.client import Probabilities
from app.classifiers.router import CheckSkip, ClassifierBackend
from app.classifiers.spec import LAYA_MODEL, SPEC_VERSION
from app.clock import Clock
from app.db.models import Chat
from app.db.repositories.moderation import ModerationRepository
from app.domain.decision import decide
from app.linking.admin_cache import AdminCache
from app.moderation import signals
from app.moderation.actions import delete_message, restrict_member
from app.notices.sender import removal_time, send_notice

#: The backend this pipeline checks with: the local Laya (§5). Jev, the
#: fallback router and its incidents are ticket #14.
BACKEND = "laya"

#: The §3 Sensitivity presets, keyed `[backend][sensitivity]`; each entry
#: carries `.violation`. Any mapping shaped like `Settings.thresholds` fits.
Thresholds = Mapping[str, Mapping[str, Any]]


class ModerationPipeline:
    def __init__(
        self,
        *,
        clock: Clock,
        core: BaseCore,
        backend: ClassifierBackend,
        thresholds: Thresholds,
        min_words: int,
        flagged_text_days: int,
        max_notice_lifetime_h: int,
        fanout: AlertFanout,
    ) -> None:
        self._clock = clock
        self._core = core
        self._backend = backend
        self._thresholds = thresholds
        self._min_words = min_words
        self._flagged_text_days = flagged_text_days
        self._max_notice_lifetime_h = max_notice_lifetime_h
        self._fanout = fanout

    async def handle_message(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        chat: Chat,
        message: Message,
        admin_cache: AdminCache,
    ) -> None:
        """Check one group message of a Linked Chat and act on a Violation (§4)."""
        # Only chats in active status and Auto-moderation are acted on.
        # A Suspended Chat checks nothing (§10); Observation Mode builds out
        # in ticket #9, so until then nothing is recorded there either.
        if chat.status != "active" or chat.mode != "auto":
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
                outcome="skipped_short",
                created_at=now,
            )
            return

        # §4 steps 6-8: classify, then decide.
        started = time.monotonic()
        outcome = await self._backend.check(signals.extract_state(text, entities))
        latency_ms = round((time.monotonic() - started) * 1000)

        if isinstance(outcome, CheckSkip):
            await self._record(
                repo, chat, sender.id, message.message_id, outcome=str(outcome), created_at=now
            )
            return

        decision = decide(
            probabilities=outcome,
            violation_threshold=self._violation_threshold(chat),
        )
        check = await self._record(
            repo,
            chat,
            sender.id,
            message.message_id,
            outcome=decision.outcome,
            created_at=now,
            category=decision.category,
            confidence=decision.confidence,
            probabilities=dict(outcome),
            latency_ms=latency_ms,
        )
        if decision.outcome != "violation":
            return

        # §4 step 9: the flagged message keeps its text until the retention passes.
        await repo.store_flagged(
            check.id,
            text=text,
            entities=entities,
            purge_at=now + timedelta(days=self._flagged_text_days),
        )

        # §6 order of actions: delete → record → restrict → notice.
        await delete_message(bot, message)
        violation = await repo.record_violation(
            chat=chat,
            user_id=sender.id,
            check_id=check.id,
            category=decision.category,
            now=now,
        )
        await restrict_member(
            bot, chat.chat_id, sender.id, restricted_until=violation.restricted_until
        )
        # §7: the 🙋 button is on the notice only while an Appeal would reach
        # an Admin — a channel sender gets no button either way (§4).
        appeal_recipient = await self._fanout.has_appeal_recipient(
            bot, session, admin_cache=admin_cache, chat=chat
        )
        notice = await send_notice(
            bot,
            self._core,
            chat_id=chat.chat_id,
            chat_language=chat.chat_language,
            name=sender.first_name or str(sender.id),
            category=decision.category,
            step_seconds=violation.restriction_seconds or 0,
            appeal_violation_id=violation.id if appeal_recipient else None,
        )
        await repo.save_notice(
            violation.id,
            message_id=notice.message_id,
            delete_at=removal_time(
                now,
                restricted_until=violation.restricted_until,
                max_lifetime_h=self._max_notice_lifetime_h,
            ),
        )
        # §6, step 5: the Admin Alert fan-out, after the Chat Notice.
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
        outcome: str,
        created_at: datetime,
        category: str | None = None,
        confidence: float | None = None,
        probabilities: Probabilities | None = None,
        latency_ms: int | None = None,
    ) -> Any:
        """One `message_check` row: the Verdict and probabilities, no text (§12)."""
        return await repo.record_check(
            chat_id=chat.chat_id,
            user_id=user_id,
            message_id=message_id,
            backend=BACKEND,
            model=LAYA_MODEL,
            spec_version=SPEC_VERSION,
            outcome=outcome,
            created_at=created_at,
            category=category,
            confidence=confidence,
            probabilities=probabilities,
            latency_ms=latency_ms,
        )

    def _violation_threshold(self, chat: Chat) -> float:
        """The chat's Sensitivity preset for its backend (§3; per-Category
        overrides are reserved for later)."""
        return self._thresholds[chat.backend][chat.sensitivity].violation
