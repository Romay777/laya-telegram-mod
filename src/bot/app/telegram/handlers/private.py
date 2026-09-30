"""Private-chat handlers: /start and the Menu callbacks (§13)."""

from datetime import timedelta
from typing import cast

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram_i18n import I18nContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import ALERT_MODES
from app.alerts.fanout import APPEAL_MODES
from app.classifiers.router import ClassifierBackend
from app.clock import Clock
from app.config import BACKENDS, SENSITIVITIES
from app.db.models import BotUser, Chat
from app.db.repositories.chats import ChatRepository
from app.db.repositories.journal import JournalRepository
from app.db.repositories.notice_templates import NoticeTemplateRepository
from app.db.repositories.statistics import StatisticsRepository
from app.db.repositories.subscriptions import AdminSubscriptionRepository
from app.domain import ladder_edits
from app.domain.backends import never_available
from app.domain.guards import belongs_to_chat
from app.domain.lifecycle import rights_missing
from app.domain.linking import DEFAULT_MIN_CHARS, REQUIRED_RIGHTS
from app.i18n import SUPPORTED_LANGUAGES, translator_for
from app.linking.admin_cache import AdminCache
from app.linking.service import FallbackLinkStates, LinkingService
from app.menu.callbacks import (
    BackendCallback,
    CategoriesCallback,
    ChatCallback,
    ChatLanguageCallback,
    ChatModeCallback,
    ChatSettingsCallback,
    EnableAutoCallback,
    ExpiryCallback,
    JournalCallback,
    LadderCallback,
    LadderEditCallback,
    LadderStepCallback,
    MenuAction,
    MenuCallback,
    MinCharsCallback,
    MyAlertsCallback,
    NoticeTemplateCallback,
    NoticeTemplateCancelCallback,
    NoticeTemplateEditCallback,
    NoticeTemplateResetCallback,
    NoticeTemplateSaveCallback,
    ObserveCallback,
    SensitivityCallback,
    StatisticsCallback,
    ViolationCardCallback,
)
from app.menu.navigator import MenuNavigator
from app.menu.screens.home import ChatSummary
from app.menu.screens.min_chars import MIN_CHARS_PRESETS
from app.menu.screens.notice_template import (
    strip_custom_emoji,
    validate_template,
)
from app.menu.screens.statistics import WINDOWS

#: The §13 default: the choice offered right after Linking waits 2 days.
DEFAULT_SUMMARY_AFTER_H = 48

#: The duration a freshly appended Step starts at: 30 days, the largest
#: timed preset, so a new Step never surprises with a short mute.
NEW_STEP_SECONDS = 30 * 86400


class TemplateInputStates(StatesGroup):
    """The FSM state of the template Edit: the bot waits for the Admin's message."""

    waiting_for_template = State()


def create_private_router(summary_after_h: int = DEFAULT_SUMMARY_AFTER_H) -> Router:
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
        clock: Clock,
        classifier: ClassifierBackend | None = None,
    ) -> None:
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        # Every chat-scoped callback re-checks Admin access (§10, §13).
        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        await _show_chat_screen(bot, session, navigator, bot_user, chat, i18n, classifier, clock)
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

    @router.callback_query(EnableAutoCallback.filter(), F.message.chat.type == "private")
    async def enable_auto(
        callback: CallbackQuery,
        callback_data: EnableAutoCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
        clock: Clock,
        classifier: ClassifierBackend | None = None,
    ) -> None:
        """🟢 Enable auto-moderation now — after Linking or from the summary (§13)."""
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        chat.mode = "auto"  # idempotent: the summary button may be pressed late
        await session.flush()
        await _show_chat_screen(bot, session, navigator, bot_user, chat, i18n, classifier, clock)
        await callback.answer()

    @router.callback_query(ObserveCallback.filter(), F.message.chat.type == "private")
    async def observe_first(
        callback: CallbackQuery,
        callback_data: ObserveCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        clock: Clock,
        i18n: I18nContext,
        state: FSMContext,
        classifier: ClassifierBackend | None = None,
    ) -> None:
        """🔵 Observe for 2 days first: schedule the one summary (§13, §11)."""
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        chat.mode = "observation"
        chat.observation_summary_at = clock.now() + timedelta(hours=summary_after_h)
        await session.flush()
        await _show_chat_screen(bot, session, navigator, bot_user, chat, i18n, classifier, clock)
        await callback.answer()

    @router.callback_query(CategoriesCallback.filter(), F.message.chat.type == "private")
    async def categories(
        callback: CallbackQuery,
        callback_data: CategoriesCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """Categories (§13): toggle spam, ads and insult separately for the chat."""
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        chats = ChatRepository(session)
        if callback_data.code is not None:
            # The toggle flips the Category, whatever its current state (§13).
            currently_enabled = await chats.enabled_categories(chat.chat_id)
            await chats.set_category_enabled(
                chat.chat_id,
                callback_data.code,
                enabled=callback_data.code not in currently_enabled,
            )
        await navigator.show_categories(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            enabled=await chats.enabled_categories(chat.chat_id),
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(SensitivityCallback.filter(), F.message.chat.type == "private")
    async def sensitivity(
        callback: CallbackQuery,
        callback_data: SensitivityCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """Sensitivity (§13): Lenient, Balanced or Strict — the §3 preset source."""
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        if callback_data.level in SENSITIVITIES:
            await ChatRepository(session).set_sensitivity(chat.chat_id, callback_data.level)
        await navigator.show_sensitivity(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            sensitivity=await _current_sensitivity(session, chat.chat_id),
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(MinCharsCallback.filter(), F.message.chat.type == "private")
    async def min_chars(
        callback: CallbackQuery,
        callback_data: MinCharsCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """Minimum Length (§13): how short a link-free message stays unchecked (§4)."""
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        # Only the presets the screen offers are stored; a stale or
        # hand-crafted pick is refused server-side, like the Backend's (§13).
        if callback_data.value in MIN_CHARS_PRESETS:
            await ChatRepository(session).set_min_chars(chat.chat_id, callback_data.value)
        await navigator.show_min_chars(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            min_chars=await _current_min_chars(session, chat.chat_id),
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(BackendCallback.filter(), F.message.chat.type == "private")
    async def backend(
        callback: CallbackQuery,
        callback_data: BackendCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
        classifier: ClassifierBackend,
    ) -> None:
        """Classifier Backend (§5, §13): Laya or Jev, unavailable ones disabled.

        A `disabled` button still arrives when its keyboard was stale, so the
        pick is refused server-side unless the backend can actually answer.
        """
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        dead = never_available(
            callback_data.name,
            laya_deployed=classifier.laya_deployed,
            jev_available=classifier.jev_available,
        )
        if callback_data.name in BACKENDS and not dead:
            await ChatRepository(session).set_backend(chat.chat_id, callback_data.name)
        chat = await ChatRepository(session).get(chat.chat_id) or chat
        await navigator.show_backend(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            backend=chat.backend,
            laya_deployed=classifier.laya_deployed,
            jev_available=classifier.jev_available,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(ChatLanguageCallback.filter(), F.message.chat.type == "private")
    async def chat_language(
        callback: CallbackQuery,
        callback_data: ChatLanguageCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """Chat Language (§15, §13): the chat's own texts, not the Admin's."""
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        if callback_data.code in SUPPORTED_LANGUAGES:
            await ChatRepository(session).set_chat_language(chat.chat_id, callback_data.code)
        await navigator.show_chat_language(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            chat_language=await _current_chat_language(session, chat.chat_id),
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(LadderCallback.filter(), F.message.chat.type == "private")
    async def ladder(
        callback: CallbackQuery,
        callback_data: LadderCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """The Penalty Ladder screen (§6, §13): the Steps, Add/Remove, Expiry."""
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        await navigator.show_ladder(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            ladder=tuple(chat.ladder),
            expiry_seconds=chat.expiry_seconds,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(LadderStepCallback.filter(), F.message.chat.type == "private")
    async def ladder_step(
        callback: CallbackQuery,
        callback_data: LadderStepCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """One Step's duration presets (§6, §13); a pick is stored at once."""
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        if not 0 <= callback_data.index < len(chat.ladder):
            # A stale keyboard names a Step the ladder no longer has (§13).
            await navigator.show_ladder(
                bot=bot,
                session=session,
                user=bot_user,
                chat_id=chat.chat_id,
                chat_title=chat.title,
                ladder=tuple(chat.ladder),
                expiry_seconds=chat.expiry_seconds,
                locale=i18n.locale,
            )
            await callback.answer()
            return

        if callback_data.seconds is None:
            # The screen was merely opened: show the Step's duration presets.
            await navigator.show_ladder_step(
                bot=bot,
                session=session,
                user=bot_user,
                chat_id=chat.chat_id,
                chat_title=chat.title,
                index=callback_data.index,
                seconds=chat.ladder[callback_data.index],
                locale=i18n.locale,
            )
            await callback.answer()
            return

        ladder = ladder_edits.set_step(
            tuple(chat.ladder), callback_data.index, callback_data.seconds
        )
        await ChatRepository(session).set_ladder(chat.chat_id, ladder)
        await navigator.show_ladder(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            ladder=ladder,
            expiry_seconds=chat.expiry_seconds,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(LadderEditCallback.filter(), F.message.chat.type == "private")
    async def ladder_edit(
        callback: CallbackQuery,
        callback_data: LadderEditCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """+ Add step appends one; 🔴 Remove last drops the last (§6, §13).

        The ladder is always 1-10 Steps: the disabled buttons keep the Admin
        inside the limit, and the domain rules refuse it again here.
        """
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        ladder = tuple(chat.ladder)
        if callback_data.edit == "add":
            ladder = ladder_edits.add_step(ladder, NEW_STEP_SECONDS)
        elif callback_data.edit == "remove":
            ladder = ladder_edits.remove_last(ladder)
        await ChatRepository(session).set_ladder(chat.chat_id, ladder)
        await navigator.show_ladder(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            ladder=ladder,
            expiry_seconds=chat.expiry_seconds,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(ExpiryCallback.filter(), F.message.chat.type == "private")
    async def expiry(
        callback: CallbackQuery,
        callback_data: ExpiryCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """An Expiry preset (§6, §13): when a Violation stops being Active.

        The period counts per Violation from when it was recorded; `never`
        means no expiry. The change applies from the next Violation — it
        never touches the recorded ones and never lifts a Restriction.
        """
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        stored_expiry = ladder_edits.decode_expiry(callback_data.seconds)
        await ChatRepository(session).set_expiry(chat.chat_id, stored_expiry)
        await navigator.show_ladder(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            ladder=tuple(chat.ladder),
            expiry_seconds=stored_expiry,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(MyAlertsCallback.filter(), F.message.chat.type == "private")
    async def my_alerts(
        callback: CallbackQuery,
        callback_data: MyAlertsCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """My alerts (§9): pick what this Admin gets for this chat."""
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        subs = AdminSubscriptionRepository(session)
        previous_mode = await subs.get_mode(chat.chat_id, bot_user.user_id)
        if callback_data.mode in ALERT_MODES:
            # The mode was validated against ALERT_MODES just above.
            await subs.set_mode(
                chat.chat_id, user_id=bot_user.user_id, alert_mode=callback_data.mode
            )
        await navigator.show_my_alerts(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            alert_mode=await subs.get_mode(chat.chat_id, bot_user.user_id),
            locale=i18n.locale,
        )
        # §9: switching off the last Appeal-receiving recipient warns first.
        if (
            callback_data.mode == "off"
            and previous_mode in APPEAL_MODES
            and not await subs.user_ids_with_modes(chat.chat_id, modes=APPEAL_MODES)
        ):
            await callback.answer(
                text=translator_for(i18n.core, i18n.locale)("menu-alerts-last-appeal-warning"),
                show_alert=True,
            )
            return
        await callback.answer()

    @router.callback_query(NoticeTemplateCallback.filter(), F.message.chat.type == "private")
    async def notice_template(
        callback: CallbackQuery,
        callback_data: NoticeTemplateCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """The Notice Template screen (§14): the current one, or the default."""
        # Opening the Menu cancels any input wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        template = await NoticeTemplateRepository(session).get(chat.chat_id)
        await navigator.show_notice_template(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            chat_language=chat.chat_language,
            template_text=template.text if template is not None else None,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(NoticeTemplateEditCallback.filter(), F.message.chat.type == "private")
    async def notice_template_edit(
        callback: CallbackQuery,
        callback_data: NoticeTemplateEditCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """Edit (§14): the bot waits for the Admin's formatted message."""
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        await state.set_state(TemplateInputStates.waiting_for_template)
        await state.update_data(
            template_chat_id=chat.chat_id, template_chat_language=chat.chat_language
        )
        await navigator.show_template_edit(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(NoticeTemplateSaveCallback.filter(), F.message.chat.type == "private")
    async def notice_template_save(
        callback: CallbackQuery,
        callback_data: NoticeTemplateSaveCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """🟢 Save (§14): store the previewed text and entities as received."""
        data = await state.get_data()
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        text, entities = data.get("template_text"), data.get("template_entities")
        if text is None or data.get("template_chat_id") != chat.chat_id:
            # No previewed draft (the wait was cancelled): back to the screen.
            template = await NoticeTemplateRepository(session).get(chat.chat_id)
            await navigator.show_notice_template(
                bot=bot,
                session=session,
                user=bot_user,
                chat_id=chat.chat_id,
                chat_title=chat.title,
                chat_language=chat.chat_language,
                template_text=template.text if template is not None else None,
                locale=i18n.locale,
            )
            await callback.answer()
            return
        await NoticeTemplateRepository(session).save(
            chat.chat_id,
            text=str(text),
            entities=list(entities or []),
            updated_by=bot_user.user_id,
        )
        await navigator.show_notice_template(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            chat_language=chat.chat_language,
            template_text=str(text),
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(NoticeTemplateResetCallback.filter(), F.message.chat.type == "private")
    async def notice_template_reset(
        callback: CallbackQuery,
        callback_data: NoticeTemplateResetCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """🔴 Reset to default (§14): the stored template is dropped."""
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        await NoticeTemplateRepository(session).delete(chat.chat_id)
        await navigator.show_notice_template(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            chat_language=chat.chat_language,
            template_text=None,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(NoticeTemplateCancelCallback.filter(), F.message.chat.type == "private")
    async def notice_template_cancel(
        callback: CallbackQuery,
        callback_data: NoticeTemplateCancelCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """Cancel on the Preview (§14): the draft is dropped, the stored
        template — or the default — stands."""
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        template = await NoticeTemplateRepository(session).get(chat.chat_id)
        await navigator.show_notice_template(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            chat_language=chat.chat_language,
            template_text=template.text if template is not None else None,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.message(StateFilter(TemplateInputStates.waiting_for_template), F.chat.type == "private")
    async def template_input(
        message: Message,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
    ) -> None:
        """The template message arrives: read it, delete it, preview it (§14).

        Only text messages carry the formatting §14 stores; anything else is
        not a template, so the wait continues. The message's `text` and
        `entities` are kept as received — `custom_emoji` entities are
        removed, their fallback emoji characters stay in the text.
        """
        text, entities = message.text, message.entities or []
        if text is None:
            return

        data = await state.get_data()
        chat_id = data.get("template_chat_id")
        chat_language = data.get("template_chat_language", "en")
        if chat_id is None or not await admin_cache.is_admin(bot, chat_id, message.from_user.id):
            await state.clear()
            return

        # The read input is deleted (§13) — the one exception is the fallback
        # Linking forward, not this message.
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        stored_text, stored_entities = strip_custom_emoji(text, entities)
        # The draft survives the wait being over: 🟢 Save reads it from here.
        await state.set_state(None)
        await state.update_data(
            template_chat_id=chat_id,
            template_text=stored_text,
            template_entities=stored_entities,
        )
        validation = validate_template(i18n.core, str(chat_language), stored_text, stored_entities)
        await navigator.show_template_preview(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=int(chat_id),
            chat_language=str(chat_language),
            text=stored_text,
            validation=validation,
            locale=i18n.locale,
        )

    @router.callback_query(JournalCallback.filter(), F.message.chat.type == "private")
    async def open_journal(
        callback: CallbackQuery,
        callback_data: JournalCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
        clock: Clock,
    ) -> None:
        """The Journal (§13): the chat's Violations, newest first, 5 per page.

        Reached from the Chat screen or a burst summary's Open journal
        button (§9); `page` says which five to show.
        """
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        await _show_journal_page(
            bot, session, navigator, bot_user, chat, callback_data.page, i18n, callback
        )

    @router.callback_query(StatisticsCallback.filter(), F.message.chat.type == "private")
    async def open_statistics(
        callback: CallbackQuery,
        callback_data: StatisticsCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
        clock: Clock,
    ) -> None:
        """Statistics (§13): the counts for the last 7 or 30 days.

        A pick of the other window re-renders the screen with it; the
        numbers always come from the database, so they are current.
        """
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        days = callback_data.days if callback_data.days in WINDOWS else WINDOWS[0]
        stats = await StatisticsRepository(session).statistics(
            chat.chat_id, since=clock.now() - timedelta(days=days)
        )
        await navigator.show_statistics(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            stats=stats,
            days=days,
            locale=i18n.locale,
        )
        await callback.answer()

    @router.callback_query(ViolationCardCallback.filter(), F.message.chat.type == "private")
    async def open_violation_card(
        callback: CallbackQuery,
        callback_data: ViolationCardCallback,
        bot: Bot,
        session: AsyncSession,
        bot_user: BotUser,
        navigator: MenuNavigator,
        admin_cache: AdminCache,
        i18n: I18nContext,
        state: FSMContext,
        clock: Clock,
    ) -> None:
        """One Journal entry opened (§13): the Violation's card."""
        # Opening the Menu cancels a fallback Linking wait (§13).
        await state.clear()

        chat = await _accessible_chat(bot, admin_cache, session, callback, callback_data.chat_id)
        if chat is None:
            await _access_lost(bot, admin_cache, session, navigator, bot_user, callback, i18n)
            return

        journal = JournalRepository(session)
        card = await journal.card(callback_data.violation_id, now=clock.now())
        if not belongs_to_chat(card, chat.chat_id):
            # A stale entry: back to the Journal's first page (§13).
            await _show_journal_page(bot, session, navigator, bot_user, chat, 0, i18n, callback)
            return
        await navigator.show_violation_card(
            bot=bot,
            session=session,
            user=bot_user,
            chat_id=chat.chat_id,
            chat_title=chat.title,
            card=card,
            page=callback_data.page,
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


async def _show_journal_page(
    bot: Bot,
    session: AsyncSession,
    navigator: MenuNavigator,
    bot_user: BotUser,
    chat: Chat,
    page: int,
    i18n: I18nContext,
    callback: CallbackQuery,
) -> None:
    """Load one Journal page and edit the Menu into it, then answer the press (§13)."""
    entries, total = await JournalRepository(session).page(chat.chat_id, page=page)
    await navigator.show_journal(
        bot=bot,
        session=session,
        user=bot_user,
        chat_id=chat.chat_id,
        chat_title=chat.title,
        entries=entries,
        total=total,
        page=page,
        locale=i18n.locale,
    )
    await callback.answer()


async def _current_sensitivity(session: AsyncSession, chat_id: int) -> str:
    """The stored Sensitivity, re-read after a pick so the screen shows it."""
    chat = await ChatRepository(session).get(chat_id)
    return chat.sensitivity if chat is not None else "balanced"


async def _current_min_chars(session: AsyncSession, chat_id: int) -> int:
    """The stored Minimum Length, re-read after a pick so the screen shows it."""
    chat = await ChatRepository(session).get(chat_id)
    return chat.min_chars if chat is not None else DEFAULT_MIN_CHARS


async def _current_chat_language(session: AsyncSession, chat_id: int) -> str:
    """The stored Chat Language, re-read after a pick so the screen shows it."""
    chat = await ChatRepository(session).get(chat_id)
    return chat.chat_language if chat is not None else "en"


async def _show_chat_screen(
    bot: Bot,
    session: AsyncSession,
    navigator: MenuNavigator,
    bot_user: BotUser,
    chat: Chat,
    i18n: I18nContext,
    classifier: ClassifierBackend | None = None,
    clock: Clock | None = None,
    removed_chat_days: int = 30,
) -> None:
    """The chat's own status screen, after a choice has been made (§13).

    With the router available, a chosen backend that can never answer in
    this Instance says so on the status screen (§5). The lifecycle status
    shows here too (§10): a Suspended Chat names the rights it is missing,
    read live from the bot's own membership; a Removed Chat counts the
    days its settings are still kept.
    """
    backend_dead = classifier is not None and never_available(
        chat.backend,
        laya_deployed=classifier.laya_deployed,
        jev_available=classifier.jev_available,
    )
    missing_rights: tuple[str, ...] = ()
    removed_days_left: int | None = None
    if chat.status == "suspended":
        try:
            bot_member = await bot.get_chat_member(chat.chat_id, bot.id)
        except TelegramAPIError:
            bot_member = None
        missing_rights = (
            rights_missing(
                status=bot_member.status,
                can_delete_messages=bool(getattr(bot_member, "can_delete_messages", False)),
                can_restrict_members=bool(getattr(bot_member, "can_restrict_members", False)),
            )
            if bot_member is not None
            else REQUIRED_RIGHTS
        )
    elif chat.status == "removed" and chat.removed_at is not None and clock is not None:
        days_left = (chat.removed_at + timedelta(days=removed_chat_days) - clock.now()).days
        removed_days_left = max(days_left, 0)
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
        backend_dead=backend_dead,
        status=chat.status,
        missing_rights=missing_rights,
        removed_days_left=removed_days_left,
    )


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
