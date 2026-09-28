"""The Linking failure screen (§10 step 5): exactly what is missing, Check again."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.linking import LinkingProblems
from app.i18n import GetText
from app.menu.callbacks import FallbackCheckCallback, LinkCheckCallback, MenuAction
from app.menu.screen import Screen
from app.menu.screens.buttons import PRIMARY, button


def link_failed_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    problems: LinkingProblems,
    fallback: bool = False,
) -> Screen:
    """`fallback` picks the Check-again flavour: the fallback path has no token."""
    name = chat_title if chat_title else "—"
    lines = [t("menu-link-failed", chat=name)]
    if problems.basic_group:
        lines.append(t("menu-link-problem-basic-group"))
    if problems.missing_rights:
        lines.append(
            t(
                "menu-link-problem-rights",
                rights=", ".join(t(f"menu-right-{right}") for right in problems.missing_rights),
            )
        )
    if problems.from_not_admin:
        lines.append(t("menu-link-problem-not-admin"))

    check = (
        FallbackCheckCallback(chat_id=chat_id) if fallback else LinkCheckCallback(chat_id=chat_id)
    )
    check_against = InlineKeyboardButton(
        text=t("menu-link-check-again"),
        callback_data=check.pack(),
        style=PRIMARY,  # the main action on the screen
    )
    return Screen(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[check_against], [button(t, "menu-back", MenuAction.HOME)]]
        ),
    )
