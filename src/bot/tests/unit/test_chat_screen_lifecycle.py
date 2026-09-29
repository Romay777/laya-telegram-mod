"""Unit: the Chat screen's lifecycle status lines (§13, §10).

The screen shows the suspended state and what is missing, the removal's
remaining days, and carries 🔵 Check again while suspended. Pure screen
functions over a Fluent core.
"""

from app.i18n import translator_for
from app.menu.screens.chat import chat_screen

from tests.support.i18n import started_core


async def test_the_suspended_screen_names_the_missing_rights_and_offers_check_again() -> None:
    core = await started_core()

    screen = chat_screen(
        translator_for(core, "en"),
        "My Chat",
        chat_id=-100200,
        mode="observation",
        backend="laya",
        sensitivity="balanced",
        status="suspended",
        missing_rights=("can_restrict_members",),
    )

    assert "suspended" in screen.text
    assert "restrict members" in screen.text
    (check_row,) = screen.reply_markup.inline_keyboard[:1]
    (button,) = check_row
    assert button.text == "🔵 Check again"
    assert button.callback_data == "link-check:-100200"


async def test_the_active_screen_says_ok_and_has_no_check_again() -> None:
    core = await started_core()

    screen = chat_screen(
        translator_for(core, "en"),
        "My Chat",
        chat_id=-100200,
        mode="observation",
        backend="laya",
        sensitivity="balanced",
    )

    assert "suspended" not in screen.text
    callback_datas = [
        button.callback_data
        for row in screen.reply_markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]
    assert not any(data.startswith("link-check:") for data in callback_datas)


async def test_the_removed_screen_counts_the_days_left() -> None:
    core = await started_core()

    screen = chat_screen(
        translator_for(core, "en"),
        "My Chat",
        chat_id=-100200,
        mode="observation",
        backend="laya",
        sensitivity="balanced",
        status="removed",
        removed_days_left=12,
    )

    assert "Removed" in screen.text
    assert "12" in screen.text
