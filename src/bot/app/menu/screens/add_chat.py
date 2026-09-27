"""The Add a chat screen (§13): the startgroup deep link of the primary path."""

from aiogram.types import InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import MenuAction
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY, button, url_button


def add_chat_screen(t: GetText, url: str) -> Screen:
    return Screen(
        text=t("menu-add-chat-text"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [url_button(t, "menu-add-chat-open-picker", url, style=PRIMARY)],
                [button(t, "menu-back", MenuAction.HOME)],
            ]
        ),
    )
