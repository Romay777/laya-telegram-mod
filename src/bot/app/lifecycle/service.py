"""The Linked Chat lifecycle after Linking (§10): Suspended and Removed.

A `my_chat_member` update drives the §10 transition table over the chat
row: losing a required right (or being demoted) suspends, the rights
coming back re-activates, and the bot being removed or banned removes the
chat and starts its 30-day retention. The Linker — and every Admin whose
alert mode includes Suspension — hears about each transition.

The 🔵 Check again button of the Suspension alert reuses the Linking
callback: this service re-reads the bot's rights live, reactivates the
chat when they are back, and tells the Linker. A chat whose row says
`removed` stays removed — it comes back only by re-adding the bot (§10).
"""

from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatMemberUpdated
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.fanout import AlertFanout
from app.clock import Clock
from app.config import RetentionSettings
from app.db.models import Chat
from app.db.repositories.chats import ChatRepository
from app.domain.lifecycle import LifecycleAction, lifecycle_outcome, rights_missing
from app.linking.admin_cache import AdminCache


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """What one 🔵 Check again press found (§10)."""

    reactivated: bool = False
    suspended: bool = False  # still missing rights
    removed: bool = False  # removed already, or the bot can no longer see the chat
    unknown_chat: bool = False  # no Linked Chat row answers to this id


class ChatLifecycleService:
    def __init__(
        self,
        *,
        clock: Clock,
        fanout: AlertFanout,
        admin_cache: AdminCache,
        removed_chat_days: int = RetentionSettings().removed_chat_days,
    ) -> None:
        self._clock = clock
        self._fanout = fanout
        self._admin_cache = admin_cache
        self._removed_chat_days = removed_chat_days

    async def handle_my_chat_member(
        self, *, bot: Bot, session: AsyncSession, event: ChatMemberUpdated
    ) -> None:
        """One `my_chat_member` event over a Linked Chat row (§10).

        Only Linked Chats act: a chat that never linked has no row and no
        lifecycle. The old status decides whether a lost right is news
        (an already-suspended chat is not suspended twice).
        """
        member = event.new_chat_member
        chat = await ChatRepository(session).get(event.chat.id)
        if chat is None:
            return
        was_active = chat.status == "active"

        outcome = lifecycle_outcome(
            was_active=was_active,
            new_status=member.status,
            can_delete_messages=bool(getattr(member, "can_delete_messages", False)),
            can_restrict_members=bool(getattr(member, "can_restrict_members", False)),
        )
        if not outcome:
            return

        if outcome.action is LifecycleAction.SUSPEND:
            await self._suspend(bot, session, chat, outcome.missing_rights)
        elif outcome.action is LifecycleAction.REACTIVATE:
            await self._reactivate(bot, session, chat)
        elif outcome.action is LifecycleAction.REMOVE:
            await self._remove(bot, session, chat)

    async def check_again(
        self, bot: Bot, session: AsyncSession, *, chat_id: int
    ) -> CheckOutcome:
        """🔵 Check again on a Suspension alert or the Chat screen (§10).

        The bot's own membership is read live; rights back means `active`
        and the Linker is told. The outcome names the screen the presser
        gets: still suspended, removed, or nothing to fix.
        """
        repo = ChatRepository(session)
        chat = await repo.get(chat_id)
        if chat is None:
            return CheckOutcome(unknown_chat=True)
        if chat.status == "removed":
            return CheckOutcome(removed=True)

        bot_member = await self._bot_member(bot, chat_id)
        if bot_member is None:
            # Telegram will not say: the bot cannot see the chat any more,
            # which is what removal looks like from the inside.
            await self._remove(bot, session, chat)
            return CheckOutcome(removed=True)

        missing = rights_missing(
            status=bot_member.status,
            can_delete_messages=bool(getattr(bot_member, "can_delete_messages", False)),
            can_restrict_members=bool(getattr(bot_member, "can_restrict_members", False)),
        )
        if missing:
            await self._suspend(bot, session, chat, missing)
            return CheckOutcome(suspended=True)

        if chat.status == "suspended":
            await self._reactivate(bot, session, chat)
            return CheckOutcome(reactivated=True)
        return CheckOutcome()

    # --- The three transitions ----------------------------------------------

    async def suspend_on_failed_restriction(
        self, bot: Bot, session: AsyncSession, chat: Chat
    ) -> None:
        """A `restrictChatMember` failed for lack of rights: suspend (§6, §10).

        The failing call names no right, so the bot's membership is read
        live; when Telegram will not say either, `can_restrict_members` —
        the right the failed call needed — stands for the list.
        """
        bot_member = await self._bot_member(bot, chat.chat_id)
        missing = (
            rights_missing(
                status=bot_member.status,
                can_delete_messages=bool(getattr(bot_member, "can_delete_messages", False)),
                can_restrict_members=bool(getattr(bot_member, "can_restrict_members", False)),
            )
            if bot_member is not None
            else ("can_restrict_members",)
        )
        await self._suspend(bot, session, chat, missing or ("can_restrict_members",))

    async def _suspend(
        self,
        bot: Bot,
        session: AsyncSession,
        chat: Chat,
        missing_rights: tuple[str, ...],
    ) -> None:
        await ChatRepository(session).mark_suspended(chat.chat_id)
        await self._fanout.suspension_alert(
            bot,
            session,
            admin_cache=self._admin_cache,
            chat=chat,
            missing_rights=missing_rights,
            removed_chat_days=self._removed_chat_days,
        )

    async def _reactivate(self, bot: Bot, session: AsyncSession, chat: Chat) -> None:
        await ChatRepository(session).mark_active(chat.chat_id)
        await self._fanout.reactivation_alerts(
            bot,
            session,
            admin_cache=self._admin_cache,
            chat=chat,
            removed_chat_days=self._removed_chat_days,
        )

    async def _remove(self, bot: Bot, session: AsyncSession, chat: Chat) -> None:
        await ChatRepository(session).mark_removed(
            chat.chat_id, removed_at=self._clock.now()
        )
        await self._fanout.removal_alert(
            bot,
            session,
            admin_cache=self._admin_cache,
            chat=chat,
            removed_chat_days=self._removed_chat_days,
        )

    async def _bot_member(self, bot: Bot, chat_id: int):
        """The bot's own membership, or None when Telegram will not say."""
        try:
            return await bot.get_chat_member(chat_id, bot.id)
        except TelegramAPIError:
            return None
