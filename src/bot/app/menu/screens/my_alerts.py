"""The My alerts screen (§9, §13): what this Admin gets for this chat.

Three modes — All, Appeals only, Off. The Linker defaults to All and every
other Admin defaults to Off; the current choice wears the primary style,
like the language screen marks its option.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.alerts import ALERT_MODES
from app.i18n import GetText
from app.menu.callbacks import ChatSettingsCallback, MyAlertsCallback
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY


def my_alerts_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    alert_mode: str,
) -> Screen:
    name = chat_title if chat_title else "—"
    text = "\n".join([name, "", t("menu-my-alerts-text", chat=name)])
    return Screen(
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                *[
                    [
                        InlineKeyboardButton(
                            text=t(f"alert-mode-{mode}"),
                            callback_data=MyAlertsCallback(chat_id=chat_id, mode=mode).pack(),
                            style=PRIMARY if mode == alert_mode else None,
                        )
                    ]
                    for mode in ALERT_MODES
                ],
                [
                    InlineKeyboardButton(
                        text=t("menu-back"),
                        callback_data=ChatSettingsCallback(chat_id=chat_id).pack(),
                    )
                ],
            ]
        ),
    )
