"""The Add a chat screen (§13): the startgroup deep link and the fallback entry.

The primary path opens Telegram's group picker; "I added the bot already"
takes the fallback path (§10) for a bot that was added by hand.
"""

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
                [button(t, "menu-add-chat-added-already", MenuAction.ADDED_ALREADY)],
                [button(t, "menu-back", MenuAction.HOME)],
            ]
        ),
    )
