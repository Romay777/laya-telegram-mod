"""One Step's duration presets (§6, §13): 5 minutes to 30 days, or forever.

Tapping a Step on the Penalty Ladder screen opens this screen. The Step's
current duration wears the primary style; saving is one tap on another
preset — the screen then returns to the ladder.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.ladder_edits import STEP_PRESETS
from app.i18n import GetText
from app.menu.callbacks import LadderCallback, LadderStepCallback
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY
from app.notices.sender import duration_text


def ladder_step_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    index: int,
    seconds: int,
) -> Screen:
    name = chat_title if chat_title else "—"
    return Screen(
        text="\n".join([name, "", t("menu-ladder-step-text", step=index + 1)]),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=duration_text(t, preset),
                        callback_data=LadderStepCallback(
                            chat_id=chat_id, index=index, seconds=preset
                        ).pack(),
                        style=PRIMARY if preset == seconds else None,
                    )
                ]
                for preset in STEP_PRESETS
            ]
            + [
                [
                    InlineKeyboardButton(
                        text=t("menu-back"),
                        callback_data=LadderCallback(chat_id=chat_id).pack(),
                    )
                ]
            ],
        ),
    )
