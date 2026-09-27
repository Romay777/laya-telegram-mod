"""Fluent i18n for the bot (§15).

Locales are `en` and `ru`; adding a language means adding a locale folder and
a button on the language screens. Private-chat text (the Menu) uses the
Admin's stored language; before any choice is stored, the Telegram client
language is used when we speak it.
"""

from pathlib import Path
from typing import Any, Protocol

from aiogram_i18n.cores.base import BaseCore

LOCALES_PATH = Path(__file__).resolve().parent / "locales" / "{locale}"
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "ru")


def guess_locale(language_code: str | None) -> str:
    """The locale to use before an Admin has picked a language."""
    if language_code:
        code = language_code.split("-")[0].lower()
        if code in SUPPORTED_LANGUAGES:
            return code
    return "en"


class GetText(Protocol):
    def __call__(self, key: str, /, **kwargs: Any) -> str: ...


def translator_for(core: BaseCore[Any], locale: str) -> GetText:
    """A gettext bound to one locale, for code that renders text explicitly."""

    def translate(key: str, /, **kwargs: Any) -> str:
        return core.get(key, locale, **kwargs)

    return translate
