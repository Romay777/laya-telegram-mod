"""The Statistics screen (§13): how the bot is doing in one chat.

The counts of the last 7 or 30 days — messages checked, Violations by
Category, Suspicions, Appeals, False Positives — with the window toggle.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.repositories.journal import ChatStatistics
from app.i18n import GetText
from app.menu.callbacks import ChatCallback, StatisticsCallback
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY

#: The Statistics windows (§13), in toggle order; 7 days is the default.
WINDOWS = (7, 30)


def statistics_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    stats: ChatStatistics,
    days: int,
) -> Screen:
    name = chat_title if chat_title else "—"
    text = "\n".join(
        [
            t("menu-stats-header", chat=name, days=days),
            "",
            t("menu-stats-checked", count=stats.checked),
            *violation_lines(t, stats),
            t("menu-stats-suspicions", count=stats.suspicions),
            t("menu-stats-appeals", count=stats.appeals),
            t("menu-stats-false-positives", count=stats.false_positives),
        ]
    )
    return Screen(
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [_window_button(t, chat_id=chat_id, days=day, current=days) for day in WINDOWS],
                [
                    InlineKeyboardButton(
                        text=t("menu-back"),
                        callback_data=ChatCallback(chat_id=chat_id).pack(),
                    )
                ],
            ]
        ),
    )


def violation_lines(t: GetText, stats: ChatStatistics) -> list[str]:
    """The Violations line, then one line per Category that has any."""
    lines = [t("menu-stats-violations", count=sum(stats.violations_by_category.values()))]
    lines += [
        t("menu-stats-category", category=t(f"category-{code}"), count=count)
        for code, count in sorted(stats.violations_by_category.items())
    ]
    return lines


def _window_button(t: GetText, *, chat_id: int, days: int, current: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=t(f"menu-stats-window-{days}"),
        callback_data=StatisticsCallback(chat_id=chat_id, days=days).pack(),
        style=PRIMARY if days == current else None,
    )
