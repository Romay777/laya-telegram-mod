"""The Chat screen as Linking shows it: ✅ {chat} linked, then the choice (§13).

Every chat starts in Observation Mode, so the Linker picks right here:
🟢 Enable auto-moderation now, or 🔵 Observe for 2 days first — which
schedules the one Observation summary (§11).
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import GetText
from app.menu.callbacks import EnableAutoCallback, MenuAction, ObserveCallback
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY, SUCCESS, button
from app.menu.screens.chat import status_lines


def linked_chat_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    mode: str,
    backend: str,
    sensitivity: str,
) -> Screen:
    """✅ {chat} linked, the status, and the Auto-moderation choice (§13)."""
    name = chat_title if chat_title else "—"
    text = "\n".join(
        [t("menu-link-success", chat=name), "", *status_lines(t, mode, backend, sensitivity)]
    )
    return Screen(
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("menu-link-enable-auto"),
                        callback_data=EnableAutoCallback(chat_id=chat_id).pack(),
                        style=SUCCESS,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("menu-link-observe"),
                        callback_data=ObserveCallback(chat_id=chat_id).pack(),
                        style=PRIMARY,
                    )
                ],
                [button(t, "menu-back", MenuAction.HOME)],
            ]
        ),
    )
