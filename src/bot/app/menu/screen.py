"""A rendered Menu screen: the text and keyboard one screen edit puts in place."""

from dataclasses import dataclass

from aiogram.types import InlineKeyboardMarkup


@dataclass(frozen=True)
class Screen:
    text: str
    reply_markup: InlineKeyboardMarkup | None = None
