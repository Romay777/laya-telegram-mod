"""The Chat screen (§13): what was linked and with what — mode, backend, Sensitivity."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import ChatSettingsCallback, MenuAction
from app.menu.screen import Screen
from app.menu.screens.buttons import button

_BACKENDS = {"laya": "Laya", "jev": "Jev"}


def chat_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    mode: str,
    backend: str,
    sensitivity: str,
    backend_dead: bool = False,
) -> Screen:
    name = chat_title if chat_title else "—"
    lines = status_lines(t, mode, backend, sensitivity)
    if backend_dead:
        lines.append(t("menu-chat-backend-never", backend=_BACKENDS.get(backend, backend)))
    text = "\n".join([name, "", *lines])
    return Screen(
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [button(t, "menu-back", MenuAction.HOME)],
                [
                    InlineKeyboardButton(
                        text=t("menu-chat-settings"),
                        callback_data=ChatSettingsCallback(chat_id=chat_id).pack(),
                    )
                ],
            ]
        ),
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
