"""The Linking service (§10): intents, promotion checks, and completing links.

One entry point per way the flow moves:

- `start_link` — the Admin pressed 🟢 Add to chat: mint the one-hour intent
  and return the startgroup deep link.
- `handle_promotion` — a `my_chat_member` update promoted the bot: run the
  checks, then link or report what is missing.
- `check_again` — the Admin pressed 🔵 Check again on the failure screen.
- `complete_pending_start` — the Linker who never started the bot just did.
"""

import asyncio
import contextlib
import secrets
from dataclasses import dataclass
from typing import cast

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import ChatMemberUpdated
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import Clock
from app.db.fsm_storage import PostgresStorage
from app.db.models import BotUser, Chat
from app.db.repositories.chats import ChatRepository
from app.db.repositories.link_intents import LinkIntentRepository
from app.db.repositories.subscriptions import AdminSubscriptionRepository
from app.db.repositories.users import BotUserRepository
from app.domain.linking import LinkingProblems, linking_problems
from app.i18n import guess_locale, translator_for
from app.linking.deep_link import INTENT_TTL, startgroup_url
from app.menu.navigator import MenuNavigator

#: The interface language a Linked Chat gets when the Linker never picked one.
FALLBACK_LANGUAGE = "en"

#: The FSM destiny under which a deferred Linking waits for the Linker's /start.
LINKING_DESTINY = "linking"


@dataclass(frozen=True, slots=True)
class PromotionFacts:
    """What one promotion attempt knows about the chat, the bot and the Linker."""

    chat_id: int
    chat_title: str | None
    chat_type: str
    can_delete_messages: bool
    can_restrict_members: bool
    linker_id: int
    linker_status: str | None
    linker_first_name: str | None = None


class LinkingService:
    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        clock: Clock,
        core: BaseCore,
        prompt_delete_after_s: float = 600.0,
    ) -> None:
        self._clock = clock
        self._core = core
        self._storage = PostgresStorage(session_maker)
        self._prompt_delete_after_s = prompt_delete_after_s
        self._deletion_tasks: set[asyncio.Task[None]] = set()

    async def start_link(self, *, bot: Bot, session: AsyncSession, user: BotUser) -> str:
        """Mint the single-use intent bound to `user`; return the deep link (§10)."""
        me = await bot.get_me()
        token = secrets.token_urlsafe(16)
        await LinkIntentRepository(session).create(
            token=token,
            user_id=user.user_id,
            expires_at=self._clock.now() + INTENT_TTL,
        )
        return startgroup_url(cast(str, me.username), token)

    def _delete_prompt_later(self, bot: Bot, chat_id: int, message_id: int) -> None:
        """Delete the group prompt after its 10 minutes (§10 step 6).

        In-process on purpose: when the scheduler (§11) lands, this due
        deletion moves into it. Held in a set so the task cannot be collected.
        """

        async def delete_later() -> None:
            await asyncio.sleep(self._prompt_delete_after_s)
            with contextlib.suppress(TelegramAPIError):
                await bot.delete_message(chat_id=chat_id, message_id=message_id)

        task = asyncio.create_task(delete_later())
        self._deletion_tasks.add(task)
        task.add_done_callback(self._deletion_tasks.discard)

    async def handle_promotion(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        navigator: MenuNavigator,
        event: ChatMemberUpdated,
        telegram_language_code: str | None,
    ) -> None:
        """A `my_chat_member` update made the bot an administrator (§10 step 2+).

        The three checks run in order. On success the intent is consumed and
        the chat linked; on failure the Admin's Menu lists what is missing. An
        expired or reused token links nothing (§10: the token is single use).
        A Linker who never started the bot cannot be messaged, so the linking
        is deferred to their /start behind a group prompt (§10 step 6).
        """
        linker_id = event.from_user.id
        chat_type = event.chat.type
        # Check 3 needs a live `getChatMember`; an earlier failing check means
        # it does not run (§10 step 3: the checks run in order).
        linker_status = (
            await self._member_status(bot, event.chat.id, linker_id)
            if chat_type == "supergroup"
            else None
        )
        facts = PromotionFacts(
            chat_id=event.chat.id,
            chat_title=event.chat.title,
            chat_type=chat_type,
            can_delete_messages=bool(getattr(event.new_chat_member, "can_delete_messages", False)),
            can_restrict_members=bool(
                getattr(event.new_chat_member, "can_restrict_members", False)
            ),
            linker_id=linker_id,
            linker_status=linker_status,
            linker_first_name=event.from_user.first_name,
        )
        problems = self._problems(facts)

        linker = await BotUserRepository(session).get(linker_id)
        locale = self._locale(linker, telegram_language_code)
        if problems:
            # Without a bot_user there is no Menu to report into (§10 step 6).
            if linker is not None:
                await navigator.show_link_failed(
                    bot=bot,
                    session=session,
                    user=linker,
                    chat_title=facts.chat_title,
                    chat_id=facts.chat_id,
                    problems=problems,
                    locale=locale,
                )
            return

        if linker is None:
            # The Linker cannot be messaged yet: linking waits for their /start.
            await self._defer_to_group_prompt(bot, facts, telegram_language_code)
            return

        # Single use: taken out only now, when the link really completes. An
        # expired or reused token links nothing.
        intent = await LinkIntentRepository(session).consume_valid(linker_id, now=self._clock.now())
        if intent is None:
            return

        chat = await self._complete_link(
            session, facts, chat_language=linker.language or FALLBACK_LANGUAGE
        )
        await self._show_linked(bot, session, navigator, linker, facts, chat, locale)

    def _problems(self, facts: PromotionFacts) -> LinkingProblems:
        """The three §10 checks over one set of facts: exactly what is missing."""
        return linking_problems(
            chat_type=facts.chat_type,
            can_delete_messages=facts.can_delete_messages,
            can_restrict_members=facts.can_restrict_members,
            linker_status=facts.linker_status,
        )

    async def _member_status(self, bot: Bot, chat_id: int, user_id: int) -> str | None:
        """The membership status `getChatMember` reports, or None when unknown."""
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except TelegramAPIError:
            return None
        return member.status

    async def check_again(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        navigator: MenuNavigator,
        user: BotUser,
        chat_id: int,
        locale: str,
    ) -> None:
        """🔵 Check again from the failure screen: the same checks, live (§10 step 5)."""
        facts = await self._facts_live(bot, chat_id, user.user_id)
        problems = self._problems(facts)
        if problems:
            await navigator.show_link_failed(
                bot=bot,
                session=session,
                user=user,
                chat_title=facts.chat_title,
                chat_id=chat_id,
                problems=problems,
                locale=locale,
            )
            return

        intent = await LinkIntentRepository(session).consume_valid(
            user.user_id, now=self._clock.now()
        )
        if intent is None:
            # The intent expired while the failure sat on the screen.
            await navigator.show_link_expired(bot=bot, session=session, user=user, locale=locale)
            return

        chat = await self._complete_link(
            session, facts, chat_language=user.language or FALLBACK_LANGUAGE
        )
        await self._show_linked(bot, session, navigator, user, facts, chat, locale)

    async def _facts_live(self, bot: Bot, chat_id: int, linker_id: int) -> PromotionFacts:
        """The same facts as a promotion, read live instead of from the update."""
        chat = await bot.get_chat(chat_id)
        bot_member = await bot.get_chat_member(chat_id, bot.id)
        return PromotionFacts(
            chat_id=chat_id,
            chat_title=chat.title,
            chat_type=chat.type,
            can_delete_messages=bool(getattr(bot_member, "can_delete_messages", False)),
            can_restrict_members=bool(getattr(bot_member, "can_restrict_members", False)),
            linker_id=linker_id,
            linker_status=await self._member_status(bot, chat_id, linker_id),
        )

    def _locale(self, linker: BotUser | None, telegram_language_code: str | None) -> str:
        """The Linker's interface language (§15), before any choice the client one."""
        if linker is not None and linker.language:
            return linker.language
        return guess_locale(telegram_language_code)

    async def _complete_link(
        self, session: AsyncSession, facts: PromotionFacts, *, chat_language: str
    ) -> Chat:
        """Create the Linked Chat with the §12 defaults and the Linker's subscription."""
        chat = await ChatRepository(session).create_linked(
            chat_id=facts.chat_id,
            title=facts.chat_title,
            linker_id=facts.linker_id,
            linked_at=self._clock.now(),
            chat_language=chat_language,
        )
        await AdminSubscriptionRepository(session).set_mode(
            chat.chat_id, user_id=facts.linker_id, alert_mode="all"
        )
        return chat

    async def _show_linked(
        self,
        bot: Bot,
        session: AsyncSession,
        navigator: MenuNavigator,
        linker: BotUser,
        facts: PromotionFacts,
        chat: Chat,
        locale: str,
    ) -> None:
        try:
            await navigator.show_linked_chat(
                bot=bot,
                session=session,
                user=linker,
                chat_title=facts.chat_title,
                mode=chat.mode,
                backend=chat.backend,
                sensitivity=chat.sensitivity,
                locale=locale,
            )
        except TelegramForbiddenError:
            # The Linker blocked the bot; §9 marks them unreachable.
            linker.reachable = False
            await session.flush()

    async def _defer_to_group_prompt(
        self, bot: Bot, facts: PromotionFacts, telegram_language_code: str | None
    ) -> None:
        """The Linker never started the bot: prompt them in the group (§10 step 6).

        The pending chat is remembered under the Linker's FSM destiny, so their
        /start can finish the linking. The prompt's locale is the Linker's
        Telegram client language: §15 points group text at `chat_language`, but
        no Linked Chat (and so no chat_language) exists yet.
        """
        t = translator_for(self._core, guess_locale(telegram_language_code))
        me = await bot.get_me()
        prompt = await bot.send_message(
            chat_id=facts.chat_id,
            text=t(
                "link-group-prompt",
                name=facts.linker_first_name or "",
                bot_username=me.username or "",
            ),
        )
        await self._storage.set_data(
            self._pending_key(bot, facts.linker_id),
            {"chat_id": facts.chat_id, "chat_title": facts.chat_title or ""},
        )
        self._delete_prompt_later(bot, facts.chat_id, prompt.message_id)

    async def complete_pending_start(
        self, *, bot: Bot, session: AsyncSession, user: BotUser
    ) -> None:
        """Finish a deferred Linking when the prompted Linker presses /start (§10 step 6).

        The same checks run once more; a chat that went away or now fails them
        just clears the pending state, and /start carries on as usual.
        """
        key = self._pending_key(bot, user.user_id)
        data = await self._storage.get_data(key)
        chat_id = data.get("chat_id")
        if not chat_id:
            return

        await self._storage.set_data(key, {})  # the attempt is spent either way
        try:
            facts = await self._facts_live(bot, int(chat_id), user.user_id)
        except TelegramAPIError:
            return  # the chat is gone or the bot was removed
        if self._problems(facts):
            return

        await self._complete_link(session, facts, chat_language=user.language or FALLBACK_LANGUAGE)

    def _pending_key(self, bot: Bot, user_id: int) -> StorageKey:
        return StorageKey(
            bot_id=bot.id,
            chat_id=user_id,
            user_id=user_id,
            thread_id=0,
            destiny=LINKING_DESTINY,
        )
