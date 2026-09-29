"""Pure rendering of Admin Alert texts (§9).

An alert is private-chat text, so it renders in the Admin's language (§15).
The deleted message is quoted at the end of the alert with a `blockquote`
entity over it, and its stored entities move to follow the alert header —
by UTF-16 code units, the offsets Telegram counts. No Telegram, no DB.
"""

from typing import Any

from app.domain.template import utf16_len
from app.i18n import GetText
from app.notices.durations import duration_text

__all__ = [
    "message_link",
    "render_appeal_alert",
    "render_channel_alert",
    "render_incident_alert",
    "render_lifecycle_alert",
    "render_suspicion_alert",
    "render_violation_alert",
    "utf16_len",
]


def message_link(chat_id: int, message_id: int) -> str:
    """The t.me link to a message in a private supergroup (§9).

    Telegram's public links drop the `-100` supergroup prefix from the
    chat id: `-1004501234567` becomes `t.me/c/4501234567/77`.
    """
    return f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{message_id}"


def _quoted_alert(
    t: GetText,
    header: str,
    flagged_text: str | None,
    flagged_entities: list[dict[str, Any]] | None,
    header_entities: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Append the flagged message as a `blockquote` quote, entities kept (§9).

    The stored entities shift past the header by UTF-16 code units, the
    offsets Telegram counts. When the stored text has been purged, the alert
    says so instead (§8) — but the header's own entities, such as the
    Suspicion's message link, survive either way.
    """
    base = list(header_entities or [])
    if flagged_text is None:
        return f"{header}\n\n{t('alert-text-not-stored')}", base

    shift = utf16_len(header) + 2  # the blank line between header and quote
    shifted = [{**entity, "offset": entity["offset"] + shift} for entity in flagged_entities]
    quote = {"type": "blockquote", "offset": shift, "length": utf16_len(flagged_text)}
    return f"{header}\n\n{flagged_text}", [*base, *shifted, quote]


def _facts_header(
    t: GetText,
    *,
    chat_title: str,
    member_name: str,
    category: str,
    confidence: float,
    step_seconds: int,
) -> str:
    """The facts both alert kinds open with: chat, Member, Verdict, Step (§9)."""
    return "\n".join(
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
    """The Violation alert: chat, Member, Category, confidence, Step, quote (§9)."""
    header = _facts_header(
        t,
        chat_title=chat_title,
        member_name=member_name,
        category=category,
        confidence=confidence,
        step_seconds=step_seconds,
    )
    return _quoted_alert(t, header, flagged_text, flagged_entities)


def render_suspicion_alert(
    t: GetText,
    *,
    chat_title: str,
    member_name: str,
    category: str,
    confidence: float,
    url: str,
    flagged_text: str | None,
    flagged_entities: list[dict[str, Any]] | None,
) -> tuple[str, list[dict[str, Any]]]:
    """The Suspicion alert (§9): the facts, a link to the message, the quote.

    The message stays in the chat, so the link points Admins at it; it is
    made clickable with a `text_link` entity over the URL line itself.
    """
    fact_lines = [
        t("alert-suspicion-header", chat=chat_title),
        t("alert-violation-member", member=member_name),
        t(
            "alert-violation-verdict",
            category=t(f"category-{category}"),
            confidence=round(confidence * 100),
        ),
    ]
    header = "\n".join([*fact_lines, url])
    link = {
        "type": "text_link",
        "offset": utf16_len("\n".join(fact_lines)) + 1,  # past the newline
        "length": utf16_len(url),
        "url": url,
    }
    return _quoted_alert(t, header, flagged_text, flagged_entities, header_entities=[link])


def render_channel_alert(
    t: GetText,
    *,
    chat_title: str,
    channel_title: str,
    category: str,
    confidence: float,
    flagged_text: str | None,
    flagged_entities: list[dict[str, Any]] | None,
) -> tuple[str, list[dict[str, Any]]]:
    """The foreign-channel alert (§4): the chat, the banned channel, the quote.

    No Member and no Step exists here — the channel was banned outright and
    has no ladder to climb; the alert carries the 🟢 Unban button instead.
    """
    header = "\n".join(
        [
            t("alert-channel-header", chat=chat_title),
            t("alert-channel-name", channel=channel_title),
            t(
                "alert-violation-verdict",
                category=t(f"category-{category}"),
                confidence=round(confidence * 100),
            ),
        ]
    )
    return _quoted_alert(t, header, flagged_text, flagged_entities)


def render_incident_alert(
    t: GetText,
    *,
    backend: str,
    reason: str,
    recovering: bool,
    using_laya: bool = True,
) -> str:
    """The backend incident alert (§5, §9): which backend, why, what now.

    The opening failure names the fallback ("Using Laya") only while Laya
    actually serves the check; with nothing serving it, the alert names the
    outage alone. The follow-up on the first success says the backend is
    back. No quote, no buttons.
    """
    if recovering:
        return t("alert-incident-recovery", backend=backend)
    return t(
        "alert-incident-with-laya" if using_laya else "alert-incident",
        backend=backend,
        reason=reason,
    )


def render_appeal_alert(
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
    """The Appeal alert (§8): the Violation's facts, the appeal line, the quote.

    The deleted message is quoted with `blockquote` formatting over its
    stored entities; purged text renders the no-longer-stored line instead.
    """
    header = "\n".join(
        [
            _facts_header(
                t,
                chat_title=chat_title,
                member_name=member_name,
                category=category,
                confidence=confidence,
                step_seconds=step_seconds,
            ),
            t("alert-appeal-line"),
        ]
    )
    return _quoted_alert(t, header, flagged_text, flagged_entities)


def render_lifecycle_alert(
    t: GetText,
    *,
    chat_title: str,
    kind: str,
    missing_rights: tuple[str, ...],
    removed_chat_days: int,
) -> str:
    """The Suspended / Removed / re-activated alert of §9's table row.

    `kind` is `suspended` (the missing rights are listed), `removed` (the
    retention window is named), or `reactivated` (the all-clear). Plain
    lines, no quote — there is no message to show.
    """
    if kind == "suspended":
        return "\n".join(
            [
                t("alert-suspended-header", chat=chat_title),
                t(
                    "alert-suspended-missing",
                    rights=", ".join(t(f"menu-right-{right}") for right in missing_rights),
                ),
                t("alert-suspended-check"),
            ]
        )
    if kind == "removed":
        return "\n".join(
            [
                t("alert-removed-header", chat=chat_title),
                t("alert-removed-kept", days=removed_chat_days),
            ]
        )
    return "\n".join(
        [
            t("alert-reactivated-header", chat=chat_title),
            t("alert-reactivated-checks"),
        ]
    )
