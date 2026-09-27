"""Private-chat handlers: /start and the Menu callbacks (§13)."""

from typing import cast

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram_i18n import I18nContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BotUser
from app.i18n import SUPPORTED_LANGUAGES
from app.linking.service import LinkingService
from app.menu.callbacks import MenuAction, MenuCallback
from app.menu.navigator import MenuNavigator


def create_private_router() -> Router:
    router = Router(name="private")

    @router.message(CommandStart(), F.chat.type == "private")
    async def start(
        message: Message,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        i18n: I18nContext,
    ) -> None:
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
            await navigator.show_home(bot=bot, session=session, user=bot_user, locale=i18n.locale)

    @router.callback_query(MenuCallback.filter(), F.message.chat.type == "private")
    async def menu(
        callback: CallbackQuery,
        callback_data: MenuCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        linking: LinkingService,
        i18n: I18nContext,
    ) -> None:
        if callback_data.action is MenuAction.ADD_TO_CHAT:
            # The primary Linking path starts here (§10 step 1).
            url = await linking.start_link(bot=bot, session=session, user=bot_user)
            await navigator.show_add_chat(
                bot=bot, session=session, user=bot_user, url=url, locale=i18n.locale
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
                await navigator.show_home(bot=bot, session=session, user=bot_user, locale=locale)
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

    return router
