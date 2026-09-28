"""The default Chat Notice text, rendered in the Chat Language (§7, §14).

`{duration}` must use Fluent plural forms (`1 час`, `3 часа`, `5 часов`;
"forever" / "навсегда"), and `{reason}` is the Category phrase that fits
the sentence.
"""

import pytest

from app.i18n import translator_for
from app.notices.sender import render_notice
from tests.support.i18n import started_core

NAME = "Alice"


async def test_the_default_notice_names_user_reason_and_duration() -> None:
    core = await started_core()

    text = render_notice(
        translator_for(core, "en"), name=NAME, category="spam", step_seconds=3600
    )

    assert text == (
        "Alice, it looks like your message looks like spam. You can't write here for 1 hour."
    )


async def test_every_category_has_its_own_reason_phrase() -> None:
    core = await started_core()

    ads = render_notice(translator_for(core, "en"), name=NAME, category="ads", step_seconds=3600)
    insult = render_notice(
        translator_for(core, "en"), name=NAME, category="insult", step_seconds=3600
    )

    assert "looks like advertising" in ads
    assert "contains insults" in insult


async def test_en_durations_use_english_plural_forms() -> None:
    core = await started_core()
    render = lambda step: render_notice(  # noqa: E731 — the table reads better this short
        translator_for(core, "en"), name=NAME, category="spam", step_seconds=step
    )

    assert render(3600).endswith("for 1 hour.")
    assert render(3 * 3600).endswith("for 3 hours.")
    assert render(86400).endswith("for 1 day.")
    assert render(0).endswith("for forever.")


async def test_ru_durations_use_russian_plural_forms() -> None:
    core = await started_core()
    render = lambda step: render_notice(  # noqa: E731
        translator_for(core, "ru"), name=NAME, category="spam", step_seconds=step
    )

    assert render(3600).endswith("в чат 1 час.")
    assert render(3 * 3600).endswith("в чат 3 часа.")
    assert render(5 * 3600).endswith("в чат 5 часов.")
    assert render(86400).endswith("в чат 1 день.")
    assert render(0).endswith("в чат навсегда.")
    # The ru reason phrase fits the ru sentence.
    assert "кажется, ваше сообщение похоже на спам" in render(3600)


@pytest.mark.parametrize(
    ("step_seconds", "expected"),
    [
        (5 * 60, "5 minutes"),
        (15 * 60, "15 minutes"),
        (60, "1 minute"),
    ],
)
async def test_minute_ladder_steps_render_too(step_seconds: int, expected: str) -> None:
    core = await started_core()

    text = render_notice(
        translator_for(core, "en"), name=NAME, category="spam", step_seconds=step_seconds
    )

    assert text.endswith(f"for {expected}.")
