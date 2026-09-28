"""The Enter chat ID screen (§13): the fallback Linking input prompt (§10)."""

from aiogram.types import InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import MenuAction
from app.menu.screen import Screen
from app.menu.screens.buttons import button


def enter_chat_screen(t: GetText, *, error: bool = False) -> Screen:
    """Ask for the chat's @username, id or a forward; `error` after a failed read.

    The Admin stays on this screen until a readable input arrives or they
    press Back, which cancels the wait.
    """
    lines = [t("menu-enter-chat-text")]
    if error:
        lines += ["", t("menu-enter-chat-invalid")]
    return Screen(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[button(t, "menu-back", MenuAction.HOME)]]
        ),
    )
