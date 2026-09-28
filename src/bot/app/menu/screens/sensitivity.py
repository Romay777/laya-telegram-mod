"""The Sensitivity screen (§13): Lenient, Balanced or Strict.

Each chat's thresholds come from the preset in `config.toml`, keyed by
backend and Sensitivity (§3); the chat's current choice wears the primary
style, like the language screen marks its option.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import SENSITIVITIES
from app.i18n import GetText
from app.menu.callbacks import ChatSettingsCallback, SensitivityCallback
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY


def sensitivity_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    sensitivity: str,
) -> Screen:
    name = chat_title if chat_title else "—"
    return Screen(
        text="\n".join([name, "", t("menu-sensitivity-text")]),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(f"chat-sensitivity-{level}"),
                        callback_data=SensitivityCallback(chat_id=chat_id, level=level).pack(),
                        style=PRIMARY if level == sensitivity else None,
                    )
                ]
                for level in SENSITIVITIES
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
