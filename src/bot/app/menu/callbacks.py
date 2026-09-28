"""Callback data factories for the Menu (§13).

The screens implemented so far are not chat-scoped, so they carry no
`chat_id`; chat-scoped screens (later tickets) include it so the Admin check
can run.
"""

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class MenuAction(StrEnum):
    SET_LANGUAGE = "set-language"
    LANGUAGE_SCREEN = "language"
    HOME = "home"
    HOW_IT_WORKS = "how-it-works"
    ADD_TO_CHAT = "add-to-chat"
    ADDED_ALREADY = "added-already"


class MenuCallback(CallbackData, prefix="menu"):
    action: MenuAction
    code: str | None = None


class LinkCheckCallback(CallbackData, prefix="link-check"):
    """🔵 Check again on the Linking failure screen (§10 step 5).

    Carries the `chat_id` so the re-check knows which chat's rights to read.
    """

    chat_id: int
