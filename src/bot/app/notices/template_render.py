"""A Violation's Chat Notice, from its template or the default (§7, §14).

The rendering half of Chat Notices: the default text of §7, and the bridge
from a stored Notice Template to the notice the queue sends — the four
placeholder values in the Chat Language, then the pure
`domain.template.render`. `{user}` is a `text_mention` of the Member's
display name, so it works without a username; `{reason}` is the Category
phrase; `{duration}` uses the Fluent plural forms; `{strike}` is `N/M`,
capped at `M/M` once the last Step repeats.
"""

from typing import Any

from app.domain.template import Replacement, render
from app.i18n import GetText
from app.notices.durations import duration_text


def render_notice(
    t: GetText,
    *,
    name: str,
    category: str,
    step_seconds: int,
) -> str:
    """The default Chat Notice (§7), as pure rendering.

    `t` is a translator bound to the Chat Language (§15) — the one
    `translator_for(core, chat.chat_language)` returns.
    """
    return t(
        "notice-violation",
        user=name,
        reason=t(f"notice-reason-{category}"),
        duration=duration_text(t, step_seconds),
    )


def render_template_notice(
    t: GetText,
    *,
    text: str | None,
    entities: list[dict[str, Any]] | None,
    member: dict[str, Any],
    category: str,
    step_seconds: int,
    active_violations: int,
    ladder_len: int,
) -> tuple[str, list[dict[str, Any]]]:
    """The Chat Notice in the Chat Language (§15): the template, or the default.

    `t` is a translator bound to the Chat Language. `member` carries the
    Member's `user_id` and display `name`. `active_violations` counts the
    Violation being announced, so `{strike}` is at least 1/M.
    """
    if text is None:
        return (
            render_notice(t, name=member["name"], category=category, step_seconds=step_seconds),
            [],
        )
    name = member["name"]
    user_id = member["user_id"]
    return render(
        text,
        entities or [],
        {
            "user": Replacement(
                name,
                entity={
                    "type": "text_mention",
                    "user": {"id": user_id, "is_bot": False, "first_name": name},
                },
            ),
            "reason": Replacement(t(f"notice-reason-{category}")),
            "duration": Replacement(duration_text(t, step_seconds)),
            "strike": Replacement(f"{min(active_violations, ladder_len)}/{ladder_len}"),
        },
    )
