"""The How it works screen (§13)."""

from aiogram.types import InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import MenuAction
from app.menu.screen import Screen
from app.menu.screens.buttons import button


def how_it_works_screen(t: GetText) -> Screen:
    return Screen(
        text=t("menu-how-it-works-text"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[button(t, "menu-back", MenuAction.HOME)]]
        ),
    )
