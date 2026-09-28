"""The Categories screen (§13): spam, ads and insult, on or off per chat.

The chat's current choices wear the primary style, like the language
screen marks its option. The model is always asked about every label;
a switched-off Category is ignored when the Verdict is picked (§4).
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.repositories.chats import CATEGORY_CODES
from app.i18n import GetText
from app.menu.callbacks import CategoriesCallback, ChatSettingsCallback
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY


def categories_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    enabled: tuple[str, ...],
) -> Screen:
    name = chat_title if chat_title else "—"
    return Screen(
        text="\n".join([name, "", t("menu-categories-text", chat=name)]),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [category_toggle(t, chat_id=chat_id, code=code, enabled=code in enabled)]
                for code in CATEGORY_CODES
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


def category_toggle(
    t: GetText,
    *,
    chat_id: int,
    code: str,
    enabled: bool,
) -> InlineKeyboardButton:
    """One Category's toggle; the enabled ones wear the primary style (§13).

    The Category names live in `alerts.ftl` (§9), shared with the alert
    verdicts: one key, one translation.
    """
    return InlineKeyboardButton(
        text=t(f"category-{code}"),
        callback_data=CategoriesCallback(chat_id=chat_id, code=code).pack(),
        style=PRIMARY if enabled else None,
    )
