"""Test support: a Fluent core loaded with the bot's real locales."""

from aiogram_i18n.cores.fluent_compile_core import FluentCompileCore
from app.i18n import LOCALES_PATH


async def started_core() -> FluentCompileCore:
    core = FluentCompileCore(path=LOCALES_PATH)
    await core.startup()
    return core
