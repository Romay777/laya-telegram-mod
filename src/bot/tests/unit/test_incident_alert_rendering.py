"""Unit: the backend incident and recovery alert texts (§5, §9).

An incident alert tells the Admins which backend failed, why, and what
happens now; the recovery follow-up says it is back. Private-chat text, so
it renders in the Admin's language.
"""

from app.alerts.rendering import render_incident_alert
from app.i18n import translator_for

from tests.support.i18n import started_core


async def test_the_incident_alert_names_the_backend_reason_and_the_fallback() -> None:
    core = await started_core()

    text = render_incident_alert(
        translator_for(core, "en"),
        backend="Jev",
        reason="authentication failed",
        recovering=False,
    )

    assert text == "⚠️ Jev is unavailable: authentication failed. Using Laya"


async def test_the_recovery_alert_says_the_backend_is_back() -> None:
    core = await started_core()

    text = render_incident_alert(
        translator_for(core, "en"),
        backend="Jev",
        reason="authentication failed",
        recovering=True,
    )

    assert text == "✅ Jev is back"


async def test_the_incident_alert_renders_in_russian() -> None:
    core = await started_core()

    text = render_incident_alert(
        translator_for(core, "ru"),
        backend="Jev",
        reason="сбой аутентификации",
        recovering=False,
    )

    assert text == "⚠️ Jev недоступен: сбой аутентификации. Используется Laya"
