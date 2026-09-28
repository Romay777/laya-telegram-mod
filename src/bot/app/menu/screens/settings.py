"""The Settings screen (§13): Mode, Categories, Sensitivity, Chat Language, My alerts.

Every chat starts in Observation Mode; the Admin arms Auto-moderation here.
The remaining §13 settings (Penalty Ladder, Notice Template, Backend)
arrive with their own tickets and are not shown before they exist.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import (
    CategoriesCallback,
    ChatCallback,
    ChatLanguageCallback,
    ChatModeCallback,
    MyAlertsCallback,
    SensitivityCallback,
)
from app.menu.screen import Screen
from app.menu.screens.buttons import SUCCESS
from app.menu.screens.chat import mode_line


def settings_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    mode: str,
) -> Screen:
    name = chat_title if chat_title else "—"
    return Screen(
        text="\n".join([name, "", mode_line(t, mode)]),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [mode_button(t, chat_id=chat_id, mode=mode)],
                [categories_button(t, chat_id=chat_id)],
                [sensitivity_button(t, chat_id=chat_id)],
                [chat_language_button(t, chat_id=chat_id)],
                [my_alerts_button(t, chat_id=chat_id)],
                [
                    InlineKeyboardButton(
                        text=t("menu-back"),
                        callback_data=ChatCallback(chat_id=chat_id).pack(),
                    )
                ],
            ]
        ),
    )


def my_alerts_button(t: GetText, *, chat_id: int) -> InlineKeyboardButton:
    """My alerts (§13): each Admin picks their own alert mode per chat (§9)."""
    return InlineKeyboardButton(
        text=t("menu-chat-my-alerts"),
        callback_data=MyAlertsCallback(chat_id=chat_id).pack(),
    )


def categories_button(t: GetText, *, chat_id: int) -> InlineKeyboardButton:
    """Categories (§13): toggle spam, ads and insult per chat."""
    return InlineKeyboardButton(
        text=t("menu-chat-categories"),
        callback_data=CategoriesCallback(chat_id=chat_id).pack(),
    )


def sensitivity_button(t: GetText, *, chat_id: int) -> InlineKeyboardButton:
    """Sensitivity (§13): Lenient, Balanced or Strict."""
    return InlineKeyboardButton(
        text=t("menu-chat-sensitivity-setting"),
        callback_data=SensitivityCallback(chat_id=chat_id).pack(),
    )


def chat_language_button(t: GetText, *, chat_id: int) -> InlineKeyboardButton:
    """Chat Language (§13, §15): the language of the chat's own texts."""
    return InlineKeyboardButton(
        text=t("menu-chat-language"),
        callback_data=ChatLanguageCallback(chat_id=chat_id).pack(),
    )


def mode_button(t: GetText, *, chat_id: int, mode: str) -> InlineKeyboardButton:
    """The §13 Mode toggle: 🟢 Enable auto-moderation, or the way back.

    Enabling Auto-moderation is the confirming action, so it wears the
    🟢 success style (§13); switching back to observation is plain.
    """
    key = "menu-settings-enable-auto" if mode == "observation" else "menu-settings-observe"
    return InlineKeyboardButton(
        text=t(key),
        callback_data=ChatModeCallback(chat_id=chat_id).pack(),
        style=SUCCESS if mode == "observation" else None,
    )
