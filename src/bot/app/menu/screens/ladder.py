"""The Penalty Ladder screen (§6, §13): the Steps, Add/Remove, the Expiry.

Admins shape each chat's escalation with buttons only. Each Step opens the
duration presets; + Add step and 🔴 Remove last keep the ladder within its
1-10 Steps and wear the `disabled` mark when they would break that limit.
The Expiry presets say when a Violation stops being Active — counted per
Violation from when it was recorded; `never` means no expiry (§6).
"""

from aiogram.types import DisabledButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.domain import ladder_edits
from app.i18n import GetText
from app.menu.callbacks import (
    ChatSettingsCallback,
    ExpiryCallback,
    LadderEditCallback,
    LadderStepCallback,
)
from app.menu.screen import Screen
from app.menu.screens.buttons import DANGER, PRIMARY
from app.notices.sender import duration_text

#: The callback encoding of "never": callback data is a plain integer, the
#: `chat` row stores None for it (§12).
NEVER = 0


def ladder_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    ladder: tuple[int, ...] | list[int],
    expiry_seconds: int | None,
) -> Screen:
    name = chat_title if chat_title else "—"
    return Screen(
        text="\n".join([name, "", t("menu-ladder-text")]),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [step_button(t, chat_id=chat_id, index=index, seconds=seconds)]
                for index, seconds in enumerate(ladder)
            ]
            + [
                add_remove_row(t, chat_id=chat_id, ladder=ladder),
                *expiry_rows(t, chat_id=chat_id, expiry_seconds=expiry_seconds),
                [
                    InlineKeyboardButton(
                        text=t("menu-back"),
                        callback_data=ChatSettingsCallback(chat_id=chat_id).pack(),
                    )
                ],
            ],
        ),
    )


def add_remove_row(
    t: GetText, *, chat_id: int, ladder: tuple[int, ...] | list[int]
) -> list[InlineKeyboardButton]:
    """+ appends a Step, 🔴 drops the last one; the limit breaker is `disabled`.

    🔴 Remove last wears the danger style (§13: destructive). The Expiry
    header sits on the same row, so the keyboard reads: Steps, Add/Remove
    + "Expires after", presets, Back.
    """
    return [
        InlineKeyboardButton(
            text=t("menu-ladder-add-step"),
            callback_data=LadderEditCallback(chat_id=chat_id, edit="add").pack(),
            disabled=DisabledButton() if not ladder_edits.can_add(ladder) else None,
        ),
        InlineKeyboardButton(
            text=t("menu-ladder-remove-last"),
            callback_data=LadderEditCallback(chat_id=chat_id, edit="remove").pack(),
            style=DANGER,
            disabled=DisabledButton() if not ladder_edits.can_remove(ladder) else None,
        ),
    ]


def step_button(t: GetText, *, chat_id: int, index: int, seconds: int) -> InlineKeyboardButton:
    """One Step on the ladder, labelled with its duration in the Admin's language."""
    return InlineKeyboardButton(
        text=t("menu-ladder-step", step=index + 1, duration=duration_text(t, seconds)),
        callback_data=LadderStepCallback(chat_id=chat_id, index=index).pack(),
    )


def expiry_rows(
    t: GetText, *, chat_id: int, expiry_seconds: int | None
) -> list[list[InlineKeyboardButton]]:
    """The §6 Expiry presets, in two rows of three; the chat's choice wears primary.

    The `never` preset (stored as None on the chat row) is encoded as 0 in
    callback data, so it compares against `or_never(expiry_seconds)`.
    """
    buttons = [
        InlineKeyboardButton(
            text=t(f"menu-ladder-expiry-{label}"),
            callback_data=ExpiryCallback(
                chat_id=chat_id,
                seconds=seconds,
            ).pack(),
            style=PRIMARY if seconds == or_never(expiry_seconds) else None,
        )
        for label, seconds in _EXPIRY_CHOICES
    ]
    return [buttons[:3], buttons[3:]]


def or_never(expiry_seconds: int | None) -> int:
    """The callback encoding of an Expiry: None (never) travels as 0 (§12)."""
    return NEVER if expiry_seconds is None else expiry_seconds


#: The §6 Expiry presets, in screen order, with their Fluent labels.
_EXPIRY_CHOICES: tuple[tuple[str, int], ...] = (
    ("7d", 7 * 86400),
    ("14d", 14 * 86400),
    ("30d", 30 * 86400),
    ("60d", 60 * 86400),
    ("90d", 90 * 86400),
    ("never", NEVER),
)
