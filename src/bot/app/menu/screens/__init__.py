"""The Menu screens (§13): one module per screen."""

from app.menu.screens.add_chat import add_chat_screen
from app.menu.screens.home import home_screen
from app.menu.screens.how_it_works import how_it_works_screen
from app.menu.screens.language import language_screen

__all__ = ["add_chat_screen", "home_screen", "how_it_works_screen", "language_screen"]
