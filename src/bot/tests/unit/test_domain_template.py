"""The Notice Template domain (§14): placeholders, validation, rendering.

`render(text, entities, values) → (text, entities)` is pure: entity offsets
move by the length difference in UTF-16 code units — what Telegram counts —
and an entity that fully contains a placeholder stretches over the new value.
"""

import pytest
from app.domain.template import Replacement, TemplateError, render


def test_render_replaces_placeholders_with_their_values() -> None:
    text, entities = render(
        "{user}, it looks like your message {reason}.",
        [],
        {"user": Replacement("Alice"), "reason": Replacement("looks like spam")},
    )

    assert text == "Alice, it looks like your message looks like spam."
    assert entities == []


def test_double_braces_render_as_literal_single_braces() -> None:
    text, _ = render("{{user}} wrote {{ }} today", [], {})

    assert text == "{user} wrote { } today"  # never read as a placeholder (§14)


def test_a_lone_unmatched_brace_is_literal_text() -> None:
    assert render("a { stray", [], {})[0] == "a { stray"
    assert render("a stray } one", [], {})[0] == "a stray } one"


def test_braces_around_junk_are_an_unknown_placeholder() -> None:
    with pytest.raises(TemplateError) as error:
        render("a { stray and a } one", [], {})

    assert error.value.unknown == " stray and a "


def test_values_carry_emoji_and_cyrillic() -> None:
    text, _ = render(
        "{user} — {duration}",
        [],
        {"user": Replacement("Вася 🐸"), "duration": Replacement("1 час")},
    )

    assert text == "Вася 🐸 — 1 час"
