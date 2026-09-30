"""The Journal screen (§13): the chat's Violations, newest first, 5 per page.

One button per Violation opens its card; ◀ ▶ page through them, `disabled`
at either end. Tapping an entry carries the page along, so the card's Back
returns to where the Admin was.
"""

from datetime import datetime

from aiogram.types import DisabledButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.db.repositories.journal import PAGE_SIZE, JournalEntry
from app.i18n import GetText
from app.menu.callbacks import ChatCallback, JournalCallback, ViolationCardCallback
from app.menu.screen import Screen


def journal_screen(
    t: GetText,
    chat_title: str | None,
    *,
    chat_id: int,
    entries: list[JournalEntry],
    total: int,
    page: int,
) -> Screen:
    name = chat_title if chat_title else "—"
    pages = max(-(-total // PAGE_SIZE), 1)  # the total pages, at least one
    lines = [t("menu-journal-header", chat=name)]
    if total:
        lines.append(t("menu-journal-page", page=page + 1, total=pages))
    else:
        lines.append(t("menu-journal-empty"))
    rows = [
        [_entry_button(t, chat_id=chat_id, entry=entry, page=page) for entry in entries],
    ]
    if pages > 1:
        rows.append(_pager_row(t, chat_id=chat_id, page=page, pages=pages))
    rows.append(
        [
            InlineKeyboardButton(
                text=t("menu-back"),
                callback_data=ChatCallback(chat_id=chat_id).pack(),
            )
        ]
    )
    return Screen(text="\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def _entry_button(
    t: GetText, *, chat_id: int, entry: JournalEntry, page: int
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=t(
            "menu-journal-entry",
            category=t(f"category-{entry.category}"),
            when=_when(entry.created_at),
        ),
        callback_data=ViolationCardCallback(
            chat_id=chat_id, violation_id=entry.violation_id, page=page
        ).pack(),
    )


def _pager_row(t: GetText, *, chat_id: int, page: int, pages: int) -> list[InlineKeyboardButton]:
    """◀ ▶ around the pages; `disabled` where there is nothing to move to (§13)."""
    return [
        InlineKeyboardButton(
            text="◀",
            callback_data=JournalCallback(chat_id=chat_id, page=page - 1).pack(),
            disabled=DisabledButton() if page <= 0 else None,
        ),
        InlineKeyboardButton(
            text="▶",
            callback_data=JournalCallback(chat_id=chat_id, page=page + 1).pack(),
            disabled=DisabledButton() if page + 1 >= pages else None,
        ),
    ]


def _when(moment: datetime) -> str:
    """The moment a Violation happened, as the Journal entries show it."""
    return moment.strftime("%Y-%m-%d %H:%M")
