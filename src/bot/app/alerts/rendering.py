"""Pure rendering of Admin Alert texts (§9).

An alert is private-chat text, so it renders in the Admin's language (§15).
The deleted message is quoted at the end of the alert with a `blockquote`
entity over it, and its stored entities move to follow the alert header —
by UTF-16 code units, the offsets Telegram counts. No Telegram, no DB.
"""

from typing import Any

from app.i18n import GetText
from app.notices.sender import duration_text


def utf16_len(text: str) -> int:
    """The length of `text` in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def render_violation_alert(
    t: GetText,
    *,
    chat_title: str,
    member_name: str,
    category: str,
    confidence: float,
    step_seconds: int,
    flagged_text: str | None,
    flagged_entities: list[dict[str, Any]] | None,
) -> tuple[str, list[dict[str, Any]]]:
    """The Violation alert: chat, Member, Category, confidence, Step, quote (§9).

    Returns the text and the entities to send: the stored entities of the
    deleted message, shifted past the header, plus a `blockquote` over the
    quote. When the stored text has been purged, the alert says so (§8).
    """
    header = "\n".join(
        [
            t("alert-violation-header", chat=chat_title),
            t("alert-violation-member", member=member_name),
            t(
                "alert-violation-verdict",
                category=t(f"category-{category}"),
                confidence=round(confidence * 100),
            ),
            t("alert-violation-step", duration=duration_text(t, step_seconds)),
        ]
    )
    if flagged_text is None:
        return f"{header}\n\n{t('alert-text-not-stored')}", []

    shift = utf16_len(header) + 2  # the blank line between header and quote
    shifted = [{**entity, "offset": entity["offset"] + shift} for entity in flagged_entities]
    quote = {"type": "blockquote", "offset": shift, "length": utf16_len(flagged_text)}
    return f"{header}\n\n{flagged_text}", [*shifted, quote]
