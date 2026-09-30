"""The Minimum Length screen (§13): how short a message may stay unchecked.

Link-free messages shorter than the picked value never reach the model
(§4 step 4); `Off` checks every message. The current choice wears the
primary style, like the sensitivity screen marks its option.
"""

from typing import Final

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import ChatSettingsCallback, MinCharsCallback
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY

#: The presets the screen offers (§4 step 4); 0 checks every message.
MIN_CHARS_PRESETS: Final = (0, 5, 10, 20, 40)


def min_chars_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    min_chars: int,
) -> Screen:
    name = chat_title if chat_title else "—"
    return Screen(
        text="\n".join([name, "", t("menu-min-chars-text")]),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("chat-min-chars-off") if value == 0 else str(value),
                        callback_data=MinCharsCallback(chat_id=chat_id, value=value).pack(),
                        style=PRIMARY if value == min_chars else None,
                    )
                ]
                for value in MIN_CHARS_PRESETS
            ]
            + [
                [
                    InlineKeyboardButton(
                        text=t("menu-back"),
                        callback_data=ChatSettingsCallback(chat_id=chat_id).pack(),
                    )
                ]
            ],
        ),
    )
