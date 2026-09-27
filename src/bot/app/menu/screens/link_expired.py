"""The Linking screen for an intent that expired while the failure was showing."""

from aiogram.types import InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import MenuAction
from app.menu.screen import Screen
from app.menu.screens.buttons import button


def link_expired_screen(t: GetText) -> Screen:
    return Screen(
        text=t("menu-link-expired"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[button(t, "menu-back", MenuAction.HOME)]]
        ),
    )
