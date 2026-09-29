"""The Classifier Backend screen (§5, §13): Laya or Jev for this chat.

The current choice wears the primary style; a backend that can never be
available in this Instance is shown `disabled` (§5) — one that is merely
down right now stays tappable, because it can come back.
"""

from aiogram.types import DisabledButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import BACKENDS
from app.domain.backends import BACKEND_NAMES, never_available
from app.i18n import GetText
from app.menu.callbacks import BackendCallback, ChatSettingsCallback
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY


def backend_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    backend: str,
    laya_deployed: bool,
    jev_available: bool,
) -> Screen:
    name = chat_title if chat_title else "—"
    return Screen(
        text="\n".join([name, "", t("menu-backend-text")]),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _backend_button(
                        t,
                        chat_id=chat_id,
                        name=name_,
                        backend=backend,
                        laya_deployed=laya_deployed,
                        jev_available=jev_available,
                    )
                ]
                for name_ in BACKENDS
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


def _backend_button(
    t: GetText,
    *,
    chat_id: int,
    name: str,
    backend: str,
    laya_deployed: bool,
    jev_available: bool,
) -> InlineKeyboardButton:
    never = never_available(name, laya_deployed=laya_deployed, jev_available=jev_available)
    label = BACKEND_NAMES.get(name, name)
    if never:
        label = t("menu-backend-unavailable", backend=label)
    return InlineKeyboardButton(
        text=label,
        callback_data=BackendCallback(chat_id=chat_id, name=name).pack(),
        style=PRIMARY if name == backend else None,
        disabled=DisabledButton() if never else None,
    )
