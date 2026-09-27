"""The language screen (§13)."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import MenuAction
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY, button


def language_screen(t: GetText, highlight: str | None) -> Screen:
    """🇷🇺 Русский / 🇬🇧 English; the Telegram language_code option gets `primary`."""

    def option(code: str) -> InlineKeyboardButton:
        return button(
            t,
            f"menu-language-{code}",
            MenuAction.SET_LANGUAGE,
            code=code,
            style=PRIMARY if code == highlight else None,
        )

    return Screen(
        text=t("menu-language-title"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[option("ru"), option("en")]]),
    )
