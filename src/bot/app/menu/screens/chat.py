"""The Chat screen (§13): what was linked and with what — mode, backend, Sensitivity.

The lifecycle status lives here too (§10): a Suspended Chat is shown with
the rights it is missing and a 🔵 Check again button; a Removed Chat shows
how long its settings are still kept.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import (
    ChatSettingsCallback,
    JournalCallback,
    LinkCheckCallback,
    MenuAction,
    StatisticsCallback,
)
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY, button

_BACKENDS = {"laya": "Laya", "jev": "Jev"}


def chat_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    mode: str,
    backend: str,
    sensitivity: str,
    status: str = "active",
    missing_rights: tuple[str, ...] = (),
    removed_days_left: int | None = None,
    backend_dead: bool = False,
) -> Screen:
    name = chat_title if chat_title else "—"
    lines = [t("menu-chat-lifecycle", status=t(f"chat-status-{status}"))]
    if status == "suspended":
        lines.append(
            t(
                "menu-chat-suspended",
                rights=", ".join(t(f"menu-right-{right}") for right in missing_rights),
            )
        )
    if status == "removed" and removed_days_left is not None:
        lines.append(t("menu-chat-removed", days=removed_days_left))
    lines.append("")
    lines += status_lines(t, mode, backend, sensitivity)
    if backend_dead:
        lines.append(t("menu-chat-backend-never", backend=_BACKENDS.get(backend, backend)))
    text = "\n".join([name, *lines])
    rows = [[statistics_button(t, chat_id=chat_id), journal_button(t, chat_id=chat_id)]]
    if status == "suspended":
        rows.insert(
            0,
            [
                InlineKeyboardButton(
                    text=t("menu-chat-check-again"),
                    callback_data=LinkCheckCallback(chat_id=chat_id).pack(),
                    style=PRIMARY,  # the way out of the suspended state
                )
            ],
        )
    rows.append([button(t, "menu-back", MenuAction.HOME)])
    rows.append(
        [
            InlineKeyboardButton(
                text=t("menu-chat-settings"),
                callback_data=ChatSettingsCallback(chat_id=chat_id).pack(),
            )
        ]
    )
    return Screen(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def statistics_button(t: GetText, *, chat_id: int) -> InlineKeyboardButton:
    """Statistics (§13): the chat's counts for the last 7 or 30 days."""
    return InlineKeyboardButton(
        text=t("menu-chat-statistics"),
        callback_data=StatisticsCallback(chat_id=chat_id).pack(),
    )


def journal_button(t: GetText, *, chat_id: int) -> InlineKeyboardButton:
    """Journal (§13): the chat's Violations, newest first, 5 per page."""
    return InlineKeyboardButton(
        text=t("menu-chat-journal"),
        callback_data=JournalCallback(chat_id=chat_id).pack(),
    )


def status_lines(t: GetText, mode: str, backend: str, sensitivity: str) -> list[str]:
    return [
        mode_line(t, mode),
        t("menu-chat-backend", backend=_BACKENDS.get(backend, backend)),
        t("menu-chat-sensitivity", sensitivity=_sensitivity_label(t, sensitivity)),
    ]


def mode_line(t: GetText, mode: str) -> str:
    """The Mode status line, shared by the Chat and Settings screens (§13)."""
    return t("menu-chat-mode", mode=_mode_label(t, mode))


def _mode_label(t: GetText, mode: str) -> str:
    if mode == "observation":
        return t("chat-mode-observation")
    return t("chat-mode-auto")


def _sensitivity_label(t: GetText, sensitivity: str) -> str:
    return t(f"chat-sensitivity-{sensitivity}")
