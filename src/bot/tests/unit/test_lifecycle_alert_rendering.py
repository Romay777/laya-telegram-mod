"""Unit: the Suspended / Removed / re-activated alert texts (§9, §10).

"Suspension and re-activation" is a named Admin Alert row in §9: what is
missing, the 30-day retention note on removal, and the all-clear. The
suspension alert carries the 🔵 Check again button; its keyboard is built
in the fan-out, the texts here.
"""

from app.alerts.rendering import render_lifecycle_alert
from app.i18n import translator_for

from tests.support.i18n import started_core


async def test_the_suspension_alert_lists_the_missing_rights() -> None:
    core = await started_core()

    text = render_lifecycle_alert(
        translator_for(core, "en"),
        chat_title="My Chat",
        kind="suspended",
        missing_rights=("can_delete_messages",),
        removed_chat_days=30,
    )

    assert text == (
        "🔵 My Chat is suspended\n"
        "The bot is missing admin rights: delete messages\n"
        "Checks are paused until the rights are back."
    )


async def test_the_removal_alert_names_the_retention_days() -> None:
    core = await started_core()

    text = render_lifecycle_alert(
        translator_for(core, "en"),
        chat_title="My Chat",
        kind="removed",
        missing_rights=(),
        removed_chat_days=30,
    )

    assert text == (
        "⛔ My Chat was removed\n"
        "I kept its settings for 30 days — add me back within that time to restore them."
    )


async def test_the_reactivation_alert_says_checks_resumed() -> None:
    core = await started_core()

    text = render_lifecycle_alert(
        translator_for(core, "en"),
        chat_title="My Chat",
        kind="reactivated",
        missing_rights=(),
        removed_chat_days=30,
    )

    assert text == "✅ My Chat is active again\nChecks have resumed."


async def test_the_suspension_alert_renders_in_russian() -> None:
    core = await started_core()

    text = render_lifecycle_alert(
        translator_for(core, "ru"),
        chat_title="Чат",
        kind="suspended",
        missing_rights=("can_restrict_members",),
        removed_chat_days=30,
    )

    assert text == "\n".join(
        [
            "🔵 Чат приостановлен",
            "Нет прав администратора: ограничение участников",
            "Проверки на паузе, пока права не вернутся.",
        ]
    )
