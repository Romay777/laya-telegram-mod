"""Unit: ladder-edit rules (§6) — add/remove within 1-10 Steps, preset values."""

from app.domain import ladder_edits
from app.domain.linking import DEFAULT_LADDER

PRESETS = (300, 900, 3600, 10800, 43200, 86400, 259200, 604800, 2592000, 0)
EXPIRY_PRESETS = (604800, 1209600, 2592000, 5184000, 7776000, None)


def test_the_step_presets_are_the_six_from_the_issue() -> None:
    assert ladder_edits.STEP_PRESETS == PRESETS


def test_the_expiry_presets_are_the_six_from_the_issue() -> None:
    assert ladder_edits.EXPIRY_PRESETS == EXPIRY_PRESETS


def test_adding_a_step_appends_it() -> None:
    assert ladder_edits.add_step((3600, 86400), 0) == (3600, 86400, 0)


def test_removing_the_last_step_drops_it() -> None:
    assert ladder_edits.remove_last((3600, 86400, 0)) == (3600, 86400)


def test_a_ladder_never_grows_past_ten_steps() -> None:
    full = tuple(range(1, 11))
    assert ladder_edits.can_add(full) is False
    assert ladder_edits.add_step(full, 0) == full


def test_a_ladder_never_shrinks_below_one_step() -> None:
    single = (3600,)
    assert ladder_edits.can_remove(single) is False
    assert ladder_edits.remove_last(single) == single


def test_a_mid_sized_ladder_can_grow_and_shrink() -> None:
    assert ladder_edits.can_add(DEFAULT_LADDER) is True
    assert ladder_edits.can_remove(DEFAULT_LADDER) is True


def test_setting_a_step_replaces_only_that_step() -> None:
    assert ladder_edits.set_step((3600, 86400, 0), 0, 300) == (300, 86400, 0)
    assert ladder_edits.set_step((3600, 86400, 0), 2, 86400) == (3600, 86400, 86400)


def test_setting_a_step_out_of_range_leaves_the_ladder() -> None:
    assert ladder_edits.set_step((3600,), 5, 300) == (3600,)


def test_setting_a_step_rejects_a_non_preset_duration() -> None:
    assert ladder_edits.set_step((3600,), 0, 123) == (3600,)
