"""The Chat Notice job of a Violation (§6 step 4, §7): what the queue sends.

Both Violation producers — the moderation pipeline (§4) and a punished
Suspicion (§9) — hand the queue the same shaped job. `send` posts the
notice in the Chat Language and records it with its removal time;
`on_dropped` marks the Violation `notice_dropped` — no Appeal is possible
(§7) — and appends "notice not sent (rate limit)" to every copy of its
Violation Admin Alert.
"""

from typing import Any

from aiogram import Bot
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.fanout import AlertFanout
from app.clock import Clock
from app.db.models import Chat, Violation
from app.db.repositories.moderation import ModerationRepository
from app.notices.queue import PendingNotice
from app.notices.sender import removal_time, send_notice


def violation_notice(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    bot: Bot,
    core: BaseCore,
    clock: Clock,
    fanout: AlertFanout,
    chat: Chat,
    violation: Violation,
    member_name: str,
    category: str,
    confidence: float,
    flagged_text: str | None,
    flagged_entities: list[dict[str, Any]] | None,
    max_lifetime_h: int,
    appeal_violation_id: int | None,
) -> PendingNotice:
    """The queue's job for one Violation's Chat Notice, enqueued now (§7).

    The closures run later, from the queue's drain — long after the request
    session is gone — so each opens its own session. The appeal button is
    part of the job: the producers decide, before enqueueing, whether an
    Appeal would reach an Admin (§7).
    """

    async def send() -> None:
        async with session_maker() as session:
            notice = await send_notice(
                bot,
                core,
                chat_id=chat.chat_id,
                chat_language=chat.chat_language,
                name=member_name,
                category=category,
                step_seconds=violation.restriction_seconds or 0,
                appeal_violation_id=appeal_violation_id,
            )
            await ModerationRepository(session).save_notice(
                violation.id,
                message_id=notice.message_id,
                # The removal timers start at posting (§7): a forever
                # Restriction's notice lives `max_lifetime_h` from here.
                delete_at=removal_time(
                    clock.now(),
                    restricted_until=violation.restricted_until,
                    max_lifetime_h=max_lifetime_h,
                ),
            )
            await session.commit()

    async def on_dropped() -> None:
        async with session_maker() as session:
            await ModerationRepository(session).mark_notice_dropped(violation.id)
            await fanout.notice_not_sent(
                bot,
                session,
                chat=chat,
                violation_id=violation.id,
                member_name=member_name,
                category=category,
                confidence=confidence,
                step_seconds=violation.restriction_seconds or 0,
                flagged_text=flagged_text,
                flagged_entities=flagged_entities,
            )
            await session.commit()

    return PendingNotice(
        chat_id=chat.chat_id,
        enqueued_at=clock.now(),
        send=send,
        on_dropped=on_dropped,
    )
