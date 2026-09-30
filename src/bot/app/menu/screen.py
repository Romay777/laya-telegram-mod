"""A rendered Menu screen: the text and keyboard one screen edit puts in place."""

from dataclasses import dataclass, field
from typing import Any

from aiogram.types import InlineKeyboardMarkup


@dataclass(frozen=True)
class Screen:
    text: str
    reply_markup: InlineKeyboardMarkup | None = None
    # Telegram MessageEntity[] over `text`, as dicts; the Violation card
    # quotes the flagged message with them (§13).
    entities: tuple[dict[str, Any], ...] = field(default_factory=tuple)
