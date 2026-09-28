"""The Observation summary (§9, §13): the one 48-hour report to the Linker.

At `observation_summary_at` the Linker gets a single private message with
the counts of the last 48 hours — how many Suspicions were raised and how
many of them they punished — and the 🟢 Enable auto-moderation button. The
scheduler marks the chat `summary_sent`, so the offer is made exactly once;
after that the chat stays in Observation Mode until an Admin changes it.
"""

from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock
from app.db.models import Chat
from app.db.repositories.suspicions import SuspicionRepository
from app.db.repositories.users import BotUserRepository
from app.i18n import GetText, translator_for
from app.menu.callbacks import EnableAutoCallback
from app.menu.screens.buttons import SUCCESS

#: The language of a Linker who never picked one (§15 fallback).
FALLBACK_LANGUAGE = "en"


def enable_auto_keyboard(t: GetText, chat_id: int) -> InlineKeyboardMarkup:
    """The 🟢 Enable auto-moderation button of the summary (§9, §13)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("menu-settings-enable-auto"),
                    callback_data=EnableAutoCallback(chat_id=chat_id).pack(),
                    style=SUCCESS,
                )
            ]
        ]
    )


def render_summary(t: GetText, *, chat_title: str, window_h: int, total: int, punished: int) -> str:
    """The summary text (§9): counts from the window, and the Linker's share."""
    return "\n".join(
        [
            t("summary-header", chat=chat_title, hours=window_h),
            t("summary-suspicions", count=total),
            t("summary-punished", count=punished),
        ]
    )


async def send_observation_summary(
    bot: Bot,
    session: AsyncSession,
    *,
    core: BaseCore,
    clock: Clock,
    chat: Chat,
    window_h: int,
) -> None:
    """Send the Linker their summary and mark it sent — never repeated (§13).

    A Linker who blocked the bot (403) is marked unreachable and the summary
    still counts as made; any other Telegram failure propagates, leaving the
    summary due so the next tick retries (§11).
    """
    total, punished = await SuspicionRepository(session).summary_counts(
        chat.chat_id,
        since=clock.now() - timedelta(hours=window_h),
        punisher_id=chat.linker_id,
    )
    user = await BotUserRepository(session).get(chat.linker_id)
    locale = user.language if user is not None and user.language else FALLBACK_LANGUAGE
    t = translator_for(core, locale)
    text = render_summary(
        t,
        chat_title=chat.title or str(chat.chat_id),
        window_h=window_h,
        total=total,
        punished=punished,
    )
    try:
        await bot.send_message(
            chat_id=chat.linker_id,
            text=text,
            reply_markup=enable_auto_keyboard(t, chat.chat_id),
        )
    except TelegramForbiddenError:
        # §9: a 403 marks the Admin unreachable; the offer is not repeated.
        await BotUserRepository(session).mark_unreachable(chat.linker_id, started_at=clock.now())
    chat.summary_sent = True
    await session.flush()
