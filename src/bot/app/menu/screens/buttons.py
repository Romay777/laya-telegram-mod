"""Shared InlineKeyboardButton builder for the Menu screens (§13 button styles)."""

from aiogram.types import InlineKeyboardButton

from app.i18n import GetText
from app.menu.callbacks import MenuAction, MenuCallback

# Button styles (§13): at most one primary button per screen; on the language
# screen the option matching the Telegram language_code is the primary one.
PRIMARY = "primary"


def button(
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
