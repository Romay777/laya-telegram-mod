"""The Chat screen (§13): what was linked and with what — mode, backend, Sensitivity."""

from aiogram.types import InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import MenuAction
from app.menu.screen import Screen
from app.menu.screens.buttons import button

_BACKENDS = {"laya": "Laya", "jev": "Jev"}


def chat_screen(
    t: GetText,
    chat_title: str | None,
    *,
    mode: str,
    backend: str,
    sensitivity: str,
) -> Screen:
    name = chat_title if chat_title else "—"
    text = "\n".join([name, "", *_status_lines(t, mode, backend, sensitivity)])
    return Screen(
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[button(t, "menu-back", MenuAction.HOME)]]
        ),
    )


def _status_lines(t: GetText, mode: str, backend: str, sensitivity: str) -> list[str]:
    return [
        t("menu-chat-mode", mode=_mode_label(t, mode)),
        t("menu-chat-backend", backend=_BACKENDS.get(backend, backend)),
        t("menu-chat-sensitivity", sensitivity=_sensitivity_label(t, sensitivity)),
    ]


def _mode_label(t: GetText, mode: str) -> str:
    if mode == "observation":
        return t("chat-mode-observation")
    return t("chat-mode-auto")


def _sensitivity_label(t: GetText, sensitivity: str) -> str:
    return t(f"chat-sensitivity-{sensitivity}")
