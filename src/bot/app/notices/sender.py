"""Chat Notice rendering and sending (§7).

The text is the default one in the Chat Language — the Notice Template of
§14 arrives with its own ticket. `{duration}` comes from the Fluent plural
forms, so `1 час`, `3 часа`, `5 часов` and "forever" / "навсегда" are all
correct without code per case.

The Appeal button is not part of this ticket: Appeals are a later ticket,
and §7 leaves the button out while no Admin has Appeals enabled.
"""

from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import Message
from aiogram_i18n.cores.base import BaseCore

from app.i18n import GetText, translator_for

#: The default text lives longer than a forever Restriction (§7): the notice
#: is removed `max_lifetime_h` after posting when the Restriction never ends.
DEFAULT_MAX_LIFETIME_H = 24

_MINUTE = 60
_HOUR = 3600
_DAY = 86400


def render_notice(
    t: GetText,
    *,
    name: str,
    category: str,
    step_seconds: int,
) -> str:
    """The default Chat Notice (§7), as pure rendering.

    `t` is a translator bound to the Chat Language (§15) — the one
    `translator_for(core, chat.chat_language)` returns.
    """
    return t(
        "notice-violation",
        user=name,
        reason=t(f"notice-reason-{category}"),
        duration=_duration_text(t, step_seconds),
    )


def _duration_text(t: GetText, step_seconds: int) -> str:
    """A Step as text: the largest unit that fits, with plural forms (§14)."""
    if step_seconds <= 0:
        return t("notice-duration-forever")
    if step_seconds % _DAY == 0:
        return t("notice-duration-days", days=step_seconds // _DAY)
    if step_seconds % _HOUR == 0:
        return t("notice-duration-hours", hours=step_seconds // _HOUR)
    return t("notice-duration-minutes", minutes=max(step_seconds // _MINUTE, 1))


def removal_time(
    now: datetime, *, restricted_until: datetime | None, max_lifetime_h: int = DEFAULT_MAX_LIFETIME_H
) -> datetime:
    """When the notice deletes itself (§7): the Restriction's end, or the
    `max_lifetime_h` cap — the only timer a forever Restriction has."""
    if restricted_until is not None:
        return restricted_until
    return now + timedelta(hours=max_lifetime_h)


async def send_notice(
    bot: Bot,
    core: BaseCore,
    *,
    chat_id: int,
    chat_language: str,
    name: str,
    category: str,
    step_seconds: int,
) -> Message:
    """Post the Chat Notice in the chat's own language (§15)."""
    text = render_notice(
        translator_for(core, chat_language),
        name=name,
        category=category,
        step_seconds=step_seconds,
    )
    return await bot.send_message(chat_id=chat_id, text=text)
