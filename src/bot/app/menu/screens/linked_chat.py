"""The Chat screen as Linking shows it: ✅ {chat} linked, then the status."""

from aiogram.types import InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import MenuAction
from app.menu.screen import Screen
from app.menu.screens.buttons import button
from app.menu.screens.chat import status_lines


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
        [t("menu-link-success", chat=name), "", *status_lines(t, mode, backend, sensitivity)]
    )
    return Screen(
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[button(t, "menu-back", MenuAction.HOME)]]
        ),
    )
