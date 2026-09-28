"""The Settings screen (§13): this ticket carries the Mode switch.

Every chat starts in Observation Mode; the Admin arms Auto-moderation here.
The other §13 settings (Categories, Sensitivity, …) arrive with their own
tickets and are not shown before they exist.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import ChatCallback, ChatModeCallback, MyAlertsCallback
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
