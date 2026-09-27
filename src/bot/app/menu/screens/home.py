"""The Home screen (§13)."""

from aiogram.types import InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import MenuAction
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY, button


def home_screen(t: GetText) -> Screen:
    return Screen(
        text=t("menu-home-text"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [button(t, "menu-home-add-to-chat", MenuAction.ADD_TO_CHAT, style=PRIMARY)],
                [button(t, "menu-home-language", MenuAction.LANGUAGE_SCREEN)],
                [button(t, "menu-home-how-it-works", MenuAction.HOW_IT_WORKS)],
            ]
        ),
    )
