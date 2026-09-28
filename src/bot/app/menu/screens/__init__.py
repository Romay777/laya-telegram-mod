"""The Menu screens (§13): one module per screen."""

from app.menu.screens.add_chat import add_chat_screen
from app.menu.screens.enter_chat import enter_chat_screen
from app.menu.screens.home import home_screen
from app.menu.screens.how_it_works import how_it_works_screen
from app.menu.screens.language import language_screen
from app.menu.screens.link_expired import link_expired_screen
from app.menu.screens.link_failed import link_failed_screen
from app.menu.screens.linked_chat import linked_chat_screen

__all__ = [
    "add_chat_screen",
    "enter_chat_screen",
    "home_screen",
    "how_it_works_screen",
    "language_screen",
    "link_expired_screen",
    "link_failed_screen",
    "linked_chat_screen",
]
