"""The Menu screens (§13): one module per screen."""

from app.menu.screens import notice_template as notice_template_screens
from app.menu.screens.add_chat import add_chat_screen
from app.menu.screens.backend import backend_screen
from app.menu.screens.categories import categories_screen
from app.menu.screens.chat import chat_screen
from app.menu.screens.chat_language import chat_language_screen
from app.menu.screens.enter_chat import enter_chat_screen
from app.menu.screens.home import home_screen
from app.menu.screens.how_it_works import how_it_works_screen
from app.menu.screens.journal import journal_screen
from app.menu.screens.ladder import ladder_screen
from app.menu.screens.ladder_step import ladder_step_screen
from app.menu.screens.language import language_screen
from app.menu.screens.link_expired import link_expired_screen
from app.menu.screens.link_failed import link_failed_screen
from app.menu.screens.linked_chat import linked_chat_screen
from app.menu.screens.min_chars import min_chars_screen
from app.menu.screens.my_alerts import my_alerts_screen
from app.menu.screens.sensitivity import sensitivity_screen
from app.menu.screens.settings import settings_screen
from app.menu.screens.statistics import statistics_screen
from app.menu.screens.violation_card import violation_card_screen

__all__ = [
    "add_chat_screen",
    "backend_screen",
    "categories_screen",
    "chat_language_screen",
    "chat_screen",
    "enter_chat_screen",
    "home_screen",
    "how_it_works_screen",
    "journal_screen",
    "ladder_screen",
    "ladder_step_screen",
    "language_screen",
    "link_expired_screen",
    "link_failed_screen",
    "linked_chat_screen",
    "min_chars_screen",
    "my_alerts_screen",
    "notice_template_screens",
    "sensitivity_screen",
    "settings_screen",
    "statistics_screen",
    "violation_card_screen",
]
