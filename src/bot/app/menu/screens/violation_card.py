"""The Violation card (§13): one Journal entry opened.

The Member and the Category, the confidence and the Step, when it happened
and its current state, and the quoted text — or the no-longer-stored line
once the retention has purged it (§8). 🟢 Lift restriction is the Admin
Alert action itself (§9): first click wins, every copy is edited.
"""

from aiogram.types import DisabledButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.alerts.rendering import quoted_alert
from app.db.repositories.journal import ViolationCard
from app.i18n import GetText
from app.menu.callbacks import JournalCallback, LiftRestrictionCallback
from app.menu.screen import Screen
from app.menu.screens.buttons import SUCCESS
from app.notices.durations import duration_text

#: The card's state names, in the Admin's language (§13).
_STATE_KEYS = {
    "active": "menu-card-state-active",
    "expired": "menu-card-state-expired",
    "false_positive": "menu-card-state-false-positive",
}


def violation_card_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    card: ViolationCard,
    page: int,
) -> Screen:
    name = chat_title if chat_title else "—"
    lines = [
        t("menu-card-header", chat=name),
        # The Member's id is a name, not a quantity: no number grouping.
        t("alert-violation-member", member=str(card.user_id)),
        t("menu-card-category", category=t(f"category-{card.category}")),
    ]
    if card.confidence is not None:
        lines.append(t("menu-card-confidence", confidence=round(card.confidence * 100)))
    if card.step_seconds is not None:  # a sender-chat ban took no Step (§4)
        lines.append(t("alert-violation-step", duration=duration_text(t, card.step_seconds)))
    lines += [
        t("menu-card-when", when=_when(card.created_at)),
        t("menu-card-state", state=t(_STATE_KEYS.get(card.state, card.state))),
    ]
    text, entities = quoted_alert(t, "\n".join(lines), card.flagged_text, card.flagged_entities)
    return Screen(
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [_lift_button(t, chat_id=chat_id, card=card)],
                [
                    InlineKeyboardButton(
                        text=t("menu-back"),
                        callback_data=JournalCallback(chat_id=chat_id, page=page).pack(),
                    )
                ],
            ]
        ),
        entities=tuple(entities),
    )


def _lift_button(t: GetText, *, chat_id: int, card: ViolationCard) -> InlineKeyboardButton:
    """🟢 Lift restriction, the Admin Alert action (§9); gone means disabled.

    A False Positive was already lifted — first click wins (§9) — so the
    button stays visible but does nothing.
    """
    return InlineKeyboardButton(
        text=t("alert-lift-button"),
        callback_data=LiftRestrictionCallback(
            chat_id=chat_id, violation_id=card.violation_id
        ).pack(),
        style=SUCCESS,
        disabled=DisabledButton() if card.state == "false_positive" else None,
    )


def _when(moment) -> str:
    """The moment the Violation happened, as the card shows it."""
    return moment.strftime("%Y-%m-%d %H:%M")
