"""Locale resolution for the i18n middleware (§15).

Private-chat text (the Menu) uses the Admin's stored `bot_user.language`;
before any choice is stored, the Telegram client language is used when we
speak it, and `en` otherwise.
"""

from typing import Any

from aiogram_i18n import I18nMiddleware
from aiogram_i18n.cores.fluent_compile_core import FluentCompileCore
from aiogram_i18n.managers.base import BaseManager

from app.i18n import LOCALES_PATH, guess_locale


class MenuLocaleManager(BaseManager):
    async def get_locale(  # type: ignore[override]
        self,
        bot_user: Any = None,
        telegram_language_code: str | None = None,
        **kwargs: Any,
    ) -> str:
        if bot_user is not None and bot_user.language:
            return str(bot_user.language)
        return guess_locale(telegram_language_code)

    async def set_locale(self, *args: Any, **kwargs: Any) -> None:
        """Unused: the locale changes when the Admin picks a language on the Menu."""


def build_i18n_middleware() -> I18nMiddleware:
    core: FluentCompileCore = FluentCompileCore(path=LOCALES_PATH)
    return I18nMiddleware(core=core, manager=MenuLocaleManager(), default_locale="en")
