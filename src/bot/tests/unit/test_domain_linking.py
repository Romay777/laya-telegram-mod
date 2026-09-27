"""Unit: the Linking checks (§10 step 3) as a pure decision.

Given the chat type, the bot's rights and the status of the person who added
the bot, the function returns exactly what is missing, in check order. A
non-supergroup short-circuits: the other checks say nothing useful about a
basic group.
"""

from app.domain.linking import linking_problems


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
