"""Unit: the pure chat-lifecycle transitions (§10).

One `my_chat_member` event in, one `LifecycleOutcome` out — the tests read
like the §10 transition table.
"""

from app.domain.lifecycle import LifecycleAction, lifecycle_outcome


def test_active_chat_losing_a_required_right_suspends_and_names_it() -> None:
    outcome = lifecycle_outcome(
        was_active=True,
        new_status="administrator",
        can_delete_messages=False,
        can_restrict_members=True,
    )

    assert outcome.action is LifecycleAction.SUSPEND
    assert outcome.missing_rights == ("can_delete_messages",)


def test_active_chat_suspends_with_both_rights_listed_in_order() -> None:
    outcome = lifecycle_outcome(
        was_active=True,
        new_status="administrator",
        can_delete_messages=False,
        can_restrict_members=False,
    )

    assert outcome.action is LifecycleAction.SUSPEND
    assert outcome.missing_rights == ("can_delete_messages", "can_restrict_members")


def test_demoting_the_bot_suspends_with_every_right_missing() -> None:
    outcome = lifecycle_outcome(
        was_active=True,
        new_status="member",
        can_delete_messages=False,
        can_restrict_members=False,
    )

    assert outcome.action is LifecycleAction.SUSPEND
    assert outcome.missing_rights == ("can_delete_messages", "can_restrict_members")


def test_bot_removed_or_banned_removes_the_chat_whatever_stood_before() -> None:
    for new_status in ("left", "kicked"):
        outcome = lifecycle_outcome(
            was_active=True,
            new_status=new_status,
            can_delete_messages=True,
            can_restrict_members=True,
        )
        assert outcome.action is LifecycleAction.REMOVE
        assert outcome.missing_rights == ()


def test_suspended_chat_keeping_its_missing_rights_is_no_news() -> None:
    outcome = lifecycle_outcome(
        was_active=False,
        new_status="administrator",
        can_delete_messages=False,
        can_restrict_members=True,
    )

    assert not outcome


def test_suspended_chat_regaining_rights_reactivates() -> None:
    outcome = lifecycle_outcome(
        was_active=False,
        new_status="administrator",
        can_delete_messages=True,
        can_restrict_members=True,
    )

    assert outcome.action is LifecycleAction.REACTIVATE
    assert outcome.missing_rights == ()


def test_an_active_chat_holding_its_rights_is_no_news() -> None:
    outcome = lifecycle_outcome(
        was_active=True,
        new_status="administrator",
        can_delete_messages=True,
        can_restrict_members=True,
    )

    assert not outcome
