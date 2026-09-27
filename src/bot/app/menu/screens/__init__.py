"""The Menu screens of the walking skeleton (§13): language, Home, How it works."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import MenuAction, MenuCallback
from app.menu.screen import Screen

# Button styles (§13): at most one primary button per screen; on the language
# screen the option matching the Telegram language_code is the primary one.
PRIMARY = "primary"


def _button(
    t: GetText,
    key: str,
    action: MenuAction,
    code: str | None = None,
    style: str | None = None,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=t(key),
        callback_data=MenuCallback(action=action, code=code).pack(),
        style=style,
    )


def language_screen(t: GetText, highlight: str | None) -> Screen:
    """🇷🇺 Русский / 🇬🇧 English; the Telegram language_code option gets `primary`."""

    def option(code: str) -> InlineKeyboardButton:
        return _button(
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


def home_screen(t: GetText) -> Screen:
    return Screen(
        text=t("menu-home-text"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [_button(t, "menu-home-add-to-chat", MenuAction.ADD_TO_CHAT, style=PRIMARY)],
                [_button(t, "menu-home-language", MenuAction.LANGUAGE_SCREEN)],
                [_button(t, "menu-home-how-it-works", MenuAction.HOW_IT_WORKS)],
            ]
        ),
    )


def how_it_works_screen(t: GetText) -> Screen:
    return Screen(
        text=t("menu-how-it-works-text"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_button(t, "menu-back", MenuAction.HOME)]]
        ),
    )
