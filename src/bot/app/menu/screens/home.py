"""The Home screen (§13): one button per Linked Chat, then the fixed rows."""

from collections.abc import Sequence
from typing import NamedTuple

from aiogram.types import InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import MenuAction
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY, button, chat_button


class ChatSummary(NamedTuple):
    """What Home shows about one Linked Chat: its id and its current title."""

    chat_id: int
    title: str | None


def home_screen(t: GetText, chats: Sequence[ChatSummary]) -> Screen:
    rows = [[chat_button(title or "—", chat_id)] for chat_id, title in chats]
    rows += [
        [button(t, "menu-home-add-to-chat", MenuAction.ADD_TO_CHAT, style=PRIMARY)],
        [button(t, "menu-home-language", MenuAction.LANGUAGE_SCREEN)],
        [button(t, "menu-home-how-it-works", MenuAction.HOW_IT_WORKS)],
    ]
    return Screen(
        text=t("menu-home-text"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
