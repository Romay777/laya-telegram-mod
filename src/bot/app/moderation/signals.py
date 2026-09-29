"""Cheap checks over the message text, before the classifier runs (§4 steps 3-4).

Text messages are the whole ticket here: the text is extracted with its
URLs for the question spec's `state`, and the short-message filter keeps
tiny chit-chat away from the model. The member-based threshold adjustments
of §3 arrive with their own ticket.

Telegram entity offsets count UTF-16 code units, so slicing goes through
UTF-16 too — Python's own indices would drift on astral characters.
"""

import re
from typing import Any

#: An invite link: `t.me/+…` or `t.me/joinchat/…` (§3).
_INVITE = re.compile(r"t\.me/(?:\+|joinchat/)")

#: The entity types that carry a URL or an @mention.
_LINK_ENTITY_TYPES = ("url", "text_link")
_MENTION_ENTITY_TYPES = ("mention",)


def word_count(text: str) -> int:
    return len(text.split())


def urls_in(text: str, entities: list[dict[str, Any]]) -> list[str]:
    """The URLs of §4 step 3: `url` entities and `text_link` targets."""
    urls: list[str] = []
    for entity in entities:
        if entity.get("type") == "url":
            url = _slice_utf16(text, entity["offset"], entity["length"])
            if url:
                urls.append(url)
        elif entity.get("type") == "text_link" and entity.get("url"):
            urls.append(entity["url"])
    return urls


def has_link_invite_or_mention(text: str, entities: list[dict[str, Any]]) -> bool:
    """Whether the message carries a link, an invite, or an @mention (§4 step 4)."""
    if _INVITE.search(text):
        return True
    for entity in entities:
        if entity.get("type") in _LINK_ENTITY_TYPES:
            return True
        if entity.get("type") in _MENTION_ENTITY_TYPES:
            return True
        url = entity.get("url")
        if url and _INVITE.search(url):
            return True
    return False


def has_invite_link(text: str, entities: list[dict[str, Any]]) -> bool:
    """The §3 invite-link signal: `t.me/+…` or `t.me/joinchat/…` anywhere.

    The text is searched directly, and so is every `text_link` target: an
    invite hidden behind labelled text is still an invite (§3).
    """
    if _INVITE.search(text):
        return True
    return any(
        entity.get("type") in _LINK_ENTITY_TYPES
        and entity.get("url")
        and _INVITE.search(entity["url"])
        for entity in entities
    )


def is_short(*, text: str, entities: list[dict[str, Any]], min_words: int) -> bool:
    """The §4 step 4 skip: under `min_words` words and link-free."""
    return word_count(text) < min_words and not has_link_invite_or_mention(text, entities)


def extract_state(text: str, entities: list[dict[str, Any]]) -> dict[str, Any]:
    """The question spec's `state`: the message and its URLs (§5)."""
    return {"message": text, "urls": urls_in(text, entities)}


def _slice_utf16(text: str, offset: int, length: int) -> str:
    """Slice by UTF-16 code units, the offsets Telegram sends."""
    encoded = text.encode("utf-16-le")
    return encoded[offset * 2 : (offset + length) * 2].decode("utf-16-le", errors="ignore")
