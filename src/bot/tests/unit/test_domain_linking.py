"""Unit: the Linking checks (§10 step 3) as a pure decision.

Given the chat type, the bot's rights and the status of the person who added
the bot, the function returns exactly what is missing, in check order. A
non-supergroup short-circuits: the other checks say nothing useful about a
basic group.

Also: the fallback Linking input (§10), where the Admin names the chat with
an @username or a numeric id.
"""

import pytest
from app.domain.linking import linking_problems, parse_chat_ref


def test_a_supergroup_with_all_rights_and_an_admin_linker_has_no_problems() -> None:
    problems = linking_problems(
        chat_type="supergroup",
        can_delete_messages=True,
        can_restrict_members=True,
        linker_status="creator",
    )

    assert not problems


def test_a_basic_group_short_circuits_to_the_upgrade_explanation() -> None:
    problems = linking_problems(
        chat_type="group",
        can_delete_messages=False,
        can_restrict_members=False,
        linker_status=None,
    )

    assert problems.basic_group
    assert problems.missing_rights == ()
    assert not problems.from_not_admin


def test_missing_rights_are_listed_exactly() -> None:
    problems = linking_problems(
        chat_type="supergroup",
        can_delete_messages=True,
        can_restrict_members=False,
        linker_status="creator",
    )

    assert problems.missing_rights == ("can_restrict_members",)


def test_both_missing_rights_are_listed_in_check_order() -> None:
    problems = linking_problems(
        chat_type="supergroup",
        can_delete_messages=False,
        can_restrict_members=False,
        linker_status="administrator",
    )

    assert problems.missing_rights == ("can_delete_messages", "can_restrict_members")
    assert not problems.from_not_admin


def test_a_linker_who_is_not_an_admin_is_reported() -> None:
    for status in ("member", "restricted", "left", "kicked", None):
        problems = linking_problems(
            chat_type="supergroup",
            can_delete_messages=True,
            can_restrict_members=True,
            linker_status=status,
        )

        assert problems.from_not_admin, f"status {status!r} must not count as an Admin"


def test_missing_rights_and_a_non_admin_linker_are_reported_together() -> None:
    problems = linking_problems(
        chat_type="supergroup",
        can_delete_messages=False,
        can_restrict_members=True,
        linker_status="member",
    )

    assert problems.missing_rights == ("can_delete_messages",)
    assert problems.from_not_admin


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("@my_chat", "@my_chat"),
        ("  @my_chat  ", "@my_chat"),  # stray whitespace is not part of the username
        ("-1001234567890", "-1001234567890"),
        ("-123456", "-123456"),  # a basic group id
    ],
)
def test_a_fallback_input_parses_to_what_getchat_accepts(text: str, expected: str) -> None:
    assert parse_chat_ref(text) == expected


@pytest.mark.parametrize("text", [None, "", "hello", "my_chat", "123456", "@"])
def test_text_that_names_no_chat_parses_to_none(text: str | None) -> None:
    # A positive number is a user id, not a chat; a bare word is not a username.
    assert parse_chat_ref(text) is None
