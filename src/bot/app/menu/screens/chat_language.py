"""The Chat Language screen (§15, §13): 🇷🇺 or 🇬🇧 for the chat's own texts.

The choice controls the Chat Notices, the placeholder values and the
Appeal button — not the Admin's own interface language, which stays where
it is. The current choice wears the primary style.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import SUPPORTED_LANGUAGES, GetText
from app.menu.callbacks import ChatLanguageCallback, ChatSettingsCallback
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY


def chat_language_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    chat_language: str,
) -> Screen:
    name = chat_title if chat_title else "—"
    return Screen(
        text="\n".join([name, "", t("menu-chat-language-text")]),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(f"menu-language-{code}"),
                        callback_data=ChatLanguageCallback(chat_id=chat_id, code=code).pack(),
                        style=PRIMARY if code == chat_language else None,
                    )
                ]
                for code in SUPPORTED_LANGUAGES
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
