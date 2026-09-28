"""A Violation's Chat Notice from its Notice Template (§7, §14).

`{user}` renders as a `text_mention` of the Member's display name;
`{reason}` is the Category phrase in the Chat Language; `{duration}` uses
the Fluent plural forms; `{strike}` is `N/M`, capped at `M/M`. Without a
stored template the default text in the Chat Language is used.
"""

from app.i18n import translator_for
from app.notices.template_render import render_template_notice

from tests.support.i18n import started_core


def values(member_id: int = 123, name: str = "Вася 🐸") -> dict:
    return {"user_id": member_id, "name": name}


async def test_a_template_renders_user_as_text_mention() -> None:
    core = await started_core()

    text, entities = render_template_notice(
        translator_for(core, "en"),
        text="{user}, it looks like your message {reason}.",
        entities=[],
        member=values(),
        category="spam",
        step_seconds=3600,
        active_violations=1,
        ladder_len=3,
    )

    assert text == "Вася 🐸, it looks like your message looks like spam."
    assert entities == [
        {
            "type": "text_mention",
            "offset": 0,
            "length": 7,
            "user": {"id": 123, "is_bot": False, "first_name": "Вася 🐸"},
        }
    ]


async def test_reason_duration_and_strike_follow_the_chat_language() -> None:
    core = await started_core()

    text, _ = render_template_notice(
        translator_for(core, "ru"),
        text="{user}: {reason}, {duration}, {strike}",
        entities=[],
        member=values(),
        category="spam",
        step_seconds=3 * 3600,
        active_violations=2,
        ladder_len=3,
    )

    assert text == "Вася 🐸: похоже на спам, 3 часа, 2/3"


async def test_strike_is_capped_once_the_last_step_repeats() -> None:
    core = await started_core()

    text, _ = render_template_notice(
        translator_for(core, "en"),
        text="{strike}",
        entities=[],
        member=values(),
        category="spam",
        step_seconds=0,
        active_violations=9,
        ladder_len=3,
    )

    assert text == "3/3"


async def test_forever_durations_render_in_both_languages() -> None:
    core = await started_core()

    en, _ = render_template_notice(
        translator_for(core, "en"),
        text="{duration}",
        entities=[],
        member=values(),
        category="spam",
        step_seconds=0,
        active_violations=1,
        ladder_len=3,
    )
    ru, _ = render_template_notice(
        translator_for(core, "ru"),
        text="{duration}",
        entities=[],
        member=values(),
        category="spam",
        step_seconds=0,
        active_violations=1,
        ladder_len=3,
    )

    assert en == "forever"
    assert ru == "навсегда"


async def test_without_a_template_the_default_text_applies() -> None:
    core = await started_core()

    text, entities = render_template_notice(
        translator_for(core, "en"),
        text=None,
        entities=None,
        member=values(name="Alice"),
        category="spam",
        step_seconds=3600,
        active_violations=1,
        ladder_len=3,
    )

    assert text == (
        "Alice, it looks like your message looks like spam. You can't write here for 1 hour."
    )
    assert entities == []
