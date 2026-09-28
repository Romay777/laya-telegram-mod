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


class ChatSettingsCallback(CallbackData, prefix="chat-settings"):
    """⚙️ Settings on the Chat screen (§13); this ticket's screen is Mode."""

    chat_id: int


class ChatModeCallback(CallbackData, prefix="chat-mode"):
    """The Mode switch (§13): Observation Mode ↔ Auto-moderation."""

    chat_id: int


class EnableAutoCallback(CallbackData, prefix="enable-auto"):
    """🟢 Enable auto-moderation (§13): after Linking, or from the summary.

    Carries the `chat_id` so the handler can re-check Admin access (§13).
    """

    chat_id: int


class ObserveCallback(CallbackData, prefix="observe"):
    """🔵 Observe for 2 days first (§13): schedules the 48-hour summary.

    Carries the `chat_id` so the handler can re-check Admin access (§13).
    """

    chat_id: int


class MyAlertsCallback(CallbackData, prefix="chat-alerts"):
    """The My alerts screen (§9, §13) of one chat.

    `mode` is None when the screen is merely opened; otherwise it names the
    alert mode the Admin just picked: `all`, `appeals` or `off`.
    """

    chat_id: int
    mode: str | None = None


class LiftRestrictionCallback(CallbackData, prefix="lift"):
    """🟢 Lift restriction on a Violation's Admin Alert copy (§9).

    Carries the `chat_id` so the handler can re-check Admin access (§13).
    """

    chat_id: int
    violation_id: int


class AppealCallback(CallbackData, prefix="appeal"):
    """🙋 It's a mistake, the Appeal button under a Chat Notice (§7, §8).

    The callback data carries the Violation id; the presser must be the
    restricted Member, so this one button is not Admin-scoped.
    """

    chat_id: int
    violation_id: int


class AppealDecideCallback(CallbackData, prefix="appeal-decide"):
    """🟢 Lift restriction / 🔴 Reject on an Appeal's Admin Alert copy (§8).

    Carries the `chat_id` so the handler can re-check Admin access (§13);
    the first Admin to press decides (§9).
    """

    chat_id: int
    appeal_id: int
    approve: bool


class SuspicionDecideCallback(CallbackData, prefix="suspicion-decide"):
    """🔴 Punish / Dismiss on a Suspicion's Admin Alert copy (§9).

    Carries the `chat_id` so the handler can re-check Admin access (§13);
    the first Admin to press decides (§9).
    """

    chat_id: int
    suspicion_id: int
    punish: bool


class JournalCallback(CallbackData, prefix="journal"):
    """Open journal, the button on a burst summary (§9) and, later, the Chat
    screen (§13). Carries the `chat_id` so the handler can re-check Admin
    access (§13)."""

    chat_id: int
