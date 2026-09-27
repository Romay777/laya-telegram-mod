"""The Chat screen (§13) as Linking shows it: what was linked and with what."""

from aiogram.types import InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import MenuAction
from app.menu.screen import Screen
from app.menu.screens.buttons import button

_BACKENDS = {"laya": "Laya", "jev": "Jev"}


def linked_chat_screen(
    t: GetText,
    chat_title: str | None,
    *,
    mode: str,
    backend: str,
    sensitivity: str,
) -> Screen:
    """✅ {chat} linked, then the Chat screen's mode · backend · Sensitivity."""
    name = chat_title if chat_title else "—"
    text = "\n".join(
        [
            t("menu-link-success", chat=name),
            "",
            t("menu-chat-mode", mode=_mode_label(t, mode)),
            t("menu-chat-backend", backend=_BACKENDS.get(backend, backend)),
            t("menu-chat-sensitivity", sensitivity=_sensitivity_label(t, sensitivity)),
        ]
    )
    return Screen(
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[button(t, "menu-back", MenuAction.HOME)]]
        ),
    )


def _mode_label(t: GetText, mode: str) -> str:
    if mode == "observation":
        return t("chat-mode-observation")
    return t("chat-mode-auto")


def _sensitivity_label(t: GetText, sensitivity: str) -> str:
    return t(f"chat-sensitivity-{sensitivity}")
