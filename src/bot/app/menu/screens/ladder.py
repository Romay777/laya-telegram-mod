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
from app.notices.durations import duration_text


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

    🔴 Remove last wears the danger style (§13: destructive).
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

    The presets come from `ladder_edits.EXPIRY_PRESETS` with the Fluent
    labels in screen order; the callback data carries the `never` encoding.
    """
    labels = ("7d", "14d", "30d", "60d", "90d", "never")
    buttons = [
        InlineKeyboardButton(
            text=t(f"menu-ladder-expiry-{label}"),
            callback_data=ExpiryCallback(
                chat_id=chat_id,
                seconds=ladder_edits.encode_expiry(preset),
            ).pack(),
            style=PRIMARY if preset == expiry_seconds else None,
        )
        for label, preset in zip(labels, ladder_edits.EXPIRY_PRESETS, strict=True)
    ]
    return [buttons[:3], buttons[3:]]
