"""Shared InlineKeyboardButton builder for the Menu screens (§13 button styles)."""

from aiogram.types import InlineKeyboardButton

from app.i18n import GetText
from app.menu.callbacks import ChatCallback, MenuAction, MenuCallback

# Button styles (§13): at most one primary button per screen; on the language
# screen the option matching the Telegram language_code is the primary one.
PRIMARY = "primary"
#: 🟢 success for confirming or safe actions (Save, Lift restriction, Enable).
SUCCESS = "success"
#: 🔴 danger for punishing or destructive actions (Punish, Reject, Reset).
DANGER = "danger"


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


def url_button(
    t: GetText,
    key: str,
    url: str,
    style: str | None = None,
) -> InlineKeyboardButton:
    """A button that opens a URL instead of pressing a callback (the deep link)."""
    return InlineKeyboardButton(text=t(key), url=url, style=style)


def chat_button(title: str, chat_id: int) -> InlineKeyboardButton:
    """The Home button of one Linked Chat; its callback data carries the chat_id."""
    return InlineKeyboardButton(text=title, callback_data=ChatCallback(chat_id=chat_id).pack())
