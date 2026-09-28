"""Callback data factories for the Menu (§13).

Every factory that refers to a chat carries the `chat_id`, so the handler can
re-check Admin access before doing anything.
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
    """🔵 Check again on the deep-link Linking failure screen (§10 step 5).

    Carries the `chat_id` so the re-check knows which chat's rights to read.
    """

    chat_id: int


class FallbackCheckCallback(CallbackData, prefix="link-check-fb"):
    """🔵 Check again on the fallback Linking failure screen (§10).

    A separate factory from the deep-link Check again, because the fallback
    path has no one-hour token: its re-check never asks for an intent.
    """

    chat_id: int


class ChatCallback(CallbackData, prefix="chat"):
    """A chat-scoped Menu callback (§13): open (and later manage) one chat."""

    chat_id: int
