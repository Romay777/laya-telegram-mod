"""Private-chat handlers: /start and the Menu callbacks (§13)."""

from typing import cast

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram_i18n import I18nContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BotUser, Chat
from app.db.repositories.chats import ChatRepository
from app.i18n import SUPPORTED_LANGUAGES, translator_for
from app.linking.admin_cache import AdminCache
from app.linking.service import FallbackLinkStates, LinkingService
from app.menu.callbacks import (
    ChatCallback,
    ChatModeCallback,
    ChatSettingsCallback,
    MenuAction,
    MenuCallback,
)
from app.menu.navigator import MenuNavigator
from app.menu.screens.home import ChatSummary


def create_private_router() -> Router:
    router = Router(name="private")

    @router.message(CommandStart(), F.chat.type == "private")
    async def start(
        message: Message,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        linking: LinkingService,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        # A deferred Linking (the Linker was prompted in the group) finishes here.
        await linking.complete_pending_start(bot=bot, session=session, user=bot_user)

        if bot_user.language is None:
            await navigator.show_language_screen(
                bot=bot,
                session=session,
                user=bot_user,
                telegram_language_code=message.from_user.language_code
                if message.from_user
                else None,
                locale=i18n.locale,
            )
        else:
            await navigator.show_home(
                bot=bot,
                session=session,
                user=bot_user,
                locale=i18n.locale,
                chats=await _administered_chats(bot, admin_cache, session, bot_user.user_id),
            )

    @router.callback_query(MenuCallback.filter(), F.message.chat.type == "private")
    async def menu(
        callback: CallbackQuery,
        callback_data: MenuCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        linking: LinkingService,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        # Any Menu navigation leaves the fallback input screen, so no free-text
        # message is ever consumed as a chat name behind the Admin's back (§13).
        await state.clear()

        if callback_data.action is MenuAction.ADD_TO_CHAT:
            # The primary Linking path starts here (§10 step 1).
            url = await linking.start_link(bot=bot, session=session, user=bot_user)
            await navigator.show_add_chat(
                bot=bot, session=session, user=bot_user, url=url, locale=i18n.locale
            )
            await callback.answer()
            return

        if callback_data.action is MenuAction.ADDED_ALREADY:
            # The fallback Linking path starts here (§10): the bot waits for
            # the chat's @username, id or a forwarded message.
            await state.set_state(FallbackLinkStates.waiting_for_chat)
            await navigator.show_enter_chat(
                bot=bot, session=session, user=bot_user, locale=i18n.locale
            )
            await callback.answer()
            return

        locale = i18n.locale
        if (
            callback_data.action is MenuAction.SET_LANGUAGE
            and callback_data.code in SUPPORTED_LANGUAGES
        ):
            bot_user.language = callback_data.code
            await session.flush()
            locale = cast(str, callback_data.code)  # every Menu string switches at once

        match callback_data.action:
            case MenuAction.SET_LANGUAGE | MenuAction.HOME:
                await navigator.show_home(
                    bot=bot,
                    session=session,
                    user=bot_user,
                    locale=locale,
                    chats=await _administered_chats(bot, admin_cache, session, bot_user.user_id),
                )
            case MenuAction.LANGUAGE_SCREEN:
                await navigator.show_language_screen(
                    bot=bot,
                    session=session,
                    user=bot_user,
                    telegram_language_code=callback.from_user.language_code,
                    locale=locale,
                )
            case MenuAction.HOW_IT_WORKS:
                await navigator.show_how_it_works(
                    bot=bot, session=session, user=bot_user, locale=locale
                )

        await callback.answer()

    @router.callback_query(ChatCallback.filter(), F.message.chat.type == "private")
    async def open_chat(
        callback: CallbackQuery,
        callback_data: ChatCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        # Every chat-scoped callback re-checks Admin access (§10, §13).
        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        await navigator.show_chat(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            mode=chat.mode,
            backend=chat.backend,
            sensitivity=chat.sensitivity,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(ChatSettingsCallback.filter(), F.message.chat.type == "private")
    async def open_settings(
        callback: CallbackQuery,
        callback_data: ChatSettingsCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        await navigator.show_settings(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            mode=chat.mode,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(ChatModeCallback.filter(), F.message.chat.type == "private")
    async def switch_mode(
        callback: CallbackQuery,
        callback_data: ChatModeCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """The Mode switch (§13): arms Auto-moderation, or returns to observation."""
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        chat.mode = "observation" if chat.mode == "auto" else "auto"
        await session.flush()
        await navigator.show_settings(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            mode=chat.mode,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.message(StateFilter(FallbackLinkStates.waiting_for_chat), F.chat.type == "private")
    async def fallback_chat_input(
        message: Message,
        state: FSMContext,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        linking: LinkingService,
        i18n: I18nContext,
    ) -> None:
        # The fallback Linking path (§10): the Admin named the chat.
        await linking.handle_fallback_input(
            bot=bot,
            session=session,
            navigator=navigator,
            user=bot_user,
            state=state,
            message=message,
            locale=i18n.locale,
        )

    return router


async def _accessible_chat(
    bot: Bot,
    admin_cache: AdminCache,
    session: AsyncSession,
    callback: CallbackQuery,
    chat_id: int,
) -> Chat | None:
    """The chat a chat-scoped callback names, if the presser still administers it (§13)."""
    chat = await ChatRepository(session).get(chat_id)
    if chat is None or not await admin_cache.is_admin(bot, chat.chat_id, callback.from_user.id):
        return None
    return chat


async def _access_lost(
    bot: Bot,
    admin_cache: AdminCache,
    session: AsyncSession,
    navigator: MenuNavigator,
    bot_user: BotUser,
    callback: CallbackQuery,
    i18n: I18nContext,
) -> None:
    """A stale callback: a toast explains the dead end, and Home takes over (§13)."""
    t = translator_for(i18n.core, i18n.locale)
    await callback.answer(text=t("menu-chat-access-lost"), show_alert=False)
    await navigator.show_home(
        bot=bot,
        session=session,
        user=bot_user,
        locale=i18n.locale,
        chats=await _administered_chats(bot, admin_cache, session, callback.from_user.id),
    )


async def _administered_chats(
    bot: Bot, admin_cache: AdminCache, session: AsyncSession, user_id: int
) -> list[ChatSummary]:
    """The Linked Chats Home lists: those the user currently administers (§10).

    Admin status is a Telegram fact, taken from the cache over `getChatMember`;
    a chat whose admin the user no longer is drops off the screen.
    """
    chats = await ChatRepository(session).list_linked()
    return [
        ChatSummary(chat_id=chat.chat_id, title=chat.title)
        for chat in chats
        if await admin_cache.is_admin(bot, chat.chat_id, user_id)
    ]
