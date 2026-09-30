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


class CategoriesCallback(CallbackData, prefix="chat-categories"):
    """The Categories screen (§13) of one chat.

    `code` is None when the screen is merely opened; otherwise it names the
    Category the Admin just toggled: `spam`, `ads` or `insult`.
    """

    chat_id: int
    code: str | None = None


class SensitivityCallback(CallbackData, prefix="chat-sensitivity"):
    """The Sensitivity screen (§13) of one chat.

    `level` is None when the screen is merely opened; otherwise it names the
    Sensitivity the Admin just picked: `lenient`, `balanced` or `strict`.
    """

    chat_id: int
    level: str | None = None


class BackendCallback(CallbackData, prefix="chat-backend"):
    """The Classifier Backend screen (§5, §13) of one chat.

    `name` is None when the screen is merely opened; otherwise it names the
    backend the Admin just picked: `laya` or `jev`.
    """

    chat_id: int
    name: str | None = None


class ChatLanguageCallback(CallbackData, prefix="chat-language"):
    """The Chat Language screen (§15, §13) of one chat.

    `code` is None when the screen is merely opened; otherwise it names the
    language the Admin just picked: `ru` or `en`.
    """

    chat_id: int
    code: str | None = None


class LadderCallback(CallbackData, prefix="chat-ladder"):
    """The Penalty Ladder screen (§13) of one chat.

    The screen lists the chat's Steps plus + Add step, 🔴 Remove last and
    the Expiry presets (§6).
    """

    chat_id: int


class LadderStepCallback(CallbackData, prefix="chat-ladder-step"):
    """One Step of the Penalty Ladder (§13): its duration presets.

    `index` is the Step's position on the ladder, starting at 0. It alone
    opens the presets; `seconds` is None when the screen is merely opened,
    otherwise it names the duration the Admin just picked (§6, 0 = forever).
    """

    chat_id: int
    index: int
    seconds: int | None = None


class LadderEditCallback(CallbackData, prefix="chat-ladder-edit"):
    """+ Add step / 🔴 Remove last on the Penalty Ladder screen (§13, §6).

    `edit` names what was pressed: `add` appends a Step, `remove` drops the
    last one. The ladder never leaves 1-10 Steps (§6).
    """

    chat_id: int
    edit: str


class ExpiryCallback(CallbackData, prefix="chat-expiry"):
    """An Expiry preset on the Penalty Ladder screen (§6, §13).

    `seconds` is the period the Admin just picked; `0` means never (§12
    stores None for it, but callback data needs a plain integer).
    """

    chat_id: int
    seconds: int


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


class NoticeTemplateCallback(CallbackData, prefix="chat-template"):
    """The Notice Template screen (§13, §14) of one chat."""

    chat_id: int


class NoticeTemplateEditCallback(CallbackData, prefix="chat-template-edit"):
    """✏️ Edit on the Notice Template screen (§14): the bot waits for a message."""

    chat_id: int


class NoticeTemplateSaveCallback(CallbackData, prefix="chat-template-save"):
    """🟢 Save on the template Preview (§14): store what the Admin sent."""

    chat_id: int


class NoticeTemplateCancelCallback(CallbackData, prefix="chat-template-cancel"):
    """Cancel on the template Preview (§14): the stored template stands."""

    chat_id: int


class NoticeTemplateResetCallback(CallbackData, prefix="chat-template-reset"):
    """🔴 Reset to default on the template Preview (§14): drop the stored one."""

    chat_id: int


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
    """The Journal screen (§13), from the burst summary or the Chat screen.

    Carries the `chat_id` so the handler can re-check Admin access (§13);
    `page` is the page shown, 0 for the newest five.
    """

    chat_id: int
    page: int = 0


class StatisticsCallback(CallbackData, prefix="chat-statistics"):
    """The Statistics screen (§13) of one chat.

    `days` is None when the screen is merely opened; otherwise it is the
    window the Admin just picked: 7 or 30.
    """

    chat_id: int
    days: int | None = None


class ViolationCardCallback(CallbackData, prefix="violation-card"):
    """One Journal entry opened (§13): the Violation's card.

    `page` is the Journal page the entry came from, so Back returns there.
    """

    chat_id: int
    violation_id: int
    page: int = 0


class UnbanChannelCallback(CallbackData, prefix="unban-channel"):
    """🟢 Unban on a foreign channel's Violation alert (§4).

    Carries the `chat_id` so the handler can re-check Admin access (§13);
    the press lifts the sender-chat ban.
    """

    chat_id: int
    violation_id: int
