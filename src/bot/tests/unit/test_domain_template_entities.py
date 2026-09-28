"""The entity side of template rendering (§14, §17).

Offsets shift in UTF-16 code units; an entity that fully contains a
placeholder stretches over the new value. Covers the §17 cases: emoji and
Cyrillic text, nested and adjacent entities, a placeholder inside a bold
span, escapes, unknown placeholders, and the length limit.
"""

import pytest
from app.domain.template import MAX_RENDER_U16, Replacement, TemplateError, render, validate


def test_an_entity_after_a_replacement_shifts_by_the_utf16_difference() -> None:
    text, entities = render(
        "{user} wrote hello",
        [{"type": "bold", "offset": 12, "length": 5}],  # "hello"
        {"user": Replacement("Вася 🐸")},  # placeholder span was 6 units
    )

    assert text == "Вася 🐸 wrote hello"
    assert entities == [{"type": "bold", "offset": 13, "length": 5}]


def test_an_entity_that_contains_a_placeholder_stretches_over_the_value() -> None:
    text, entities = render(
        "hi {user}!",
        [{"type": "bold", "offset": 0, "length": 9}],  # over "hi {user}"
        {"user": Replacement("Вася 🐸")},
    )

    assert text == "hi Вася 🐸!"
    assert entities == [{"type": "bold", "offset": 0, "length": 10}]


def test_a_bold_span_ending_on_the_placeholder_stretches_to_its_value() -> None:
    text, entities = render(
        "**{user}**",
        [{"type": "bold", "offset": 0, "length": 8}],  # over "**{user}"
        {"user": Replacement("Alice")},
    )

    assert text == "**Alice**"
    assert entities == [{"type": "bold", "offset": 0, "length": 7}]  # "**Alice"


def test_nested_and_adjacent_entities_all_follow_their_text() -> None:
    text, entities = render(
        "{user} says {reason}!",
        [
            {"type": "bold", "offset": 0, "length": 6},  # "{user}"
            {"type": "italic", "offset": 7, "length": 4},  # "says"
            {"type": "underline", "offset": 12, "length": 8},  # "{reason}"
        ],
        {"user": Replacement("Вася 🐸"), "reason": Replacement("spam")},
    )

    assert text == "Вася 🐸 says spam!"
    assert {"type": "bold", "offset": 0, "length": 7} in entities  # stretched (contains)
    assert {"type": "italic", "offset": 8, "length": 4} in entities  # shifted after
    assert {"type": "underline", "offset": 13, "length": 4} in entities  # stretched (contains)


def test_an_entity_ending_inside_a_placeholder_clamps_to_its_start() -> None:
    text, entities = render(
        "hey {user}!",
        [{"type": "bold", "offset": 0, "length": 5}],  # "hey {" — the brace is literal
        {"user": Replacement("Alice")},
    )

    assert text == "hey Alice!"
    assert entities == [{"type": "bold", "offset": 0, "length": 4}]


def test_an_entity_starting_inside_a_placeholder_clamps_to_its_end() -> None:
    text, entities = render(
        "{user}!",
        [{"type": "bold", "offset": 3, "length": 4}],  # "er}!" — mid-placeholder
        {"user": Replacement("Alice")},
    )

    assert text == "Alice!"
    assert entities == [{"type": "bold", "offset": 5, "length": 1}]


def test_escapes_shift_the_entities_of_the_text_after_them() -> None:
    text, entities = render(
        "{{hi}} {user}",
        [{"type": "bold", "offset": 7, "length": 6}],  # "{user}" before collapsing
        {"user": Replacement("Alice")},
    )

    assert text == "{hi} Alice"
    assert entities == [{"type": "bold", "offset": 5, "length": 5}]


def test_unknown_placeholders_raise_and_name_the_first() -> None:
    with pytest.raises(TemplateError) as error:
        render("{user} and {frobnicate}", [], {"user": Replacement("Alice")})

    assert error.value.unknown == "frobnicate"


def test_validation_names_the_unknown_and_flags_a_missing_user() -> None:
    worst = {"user": Replacement("W"), "reason": Replacement("R")}

    found = validate("{user} and {frobnicate}", [], worst)

    assert found.unknown == "frobnicate"
    assert not found.missing_user
    assert not found.ok


def test_validation_warns_on_a_missing_user_but_stays_savable() -> None:
    worst = {"reason": Replacement("R")}

    found = validate("hello {reason}", [], worst)

    assert found.unknown is None
    assert found.missing_user
    assert found.ok


def test_validation_rejects_a_worst_case_render_past_1024() -> None:
    worst = {"user": Replacement("x" * (MAX_RENDER_U16 + 1))}

    assert not validate("{user}", [], worst).fits


def test_validation_accepts_a_worst_case_render_at_1024() -> None:
    worst = {"user": Replacement("x" * MAX_RENDER_U16)}

    assert validate("{user}", [], worst).ok
