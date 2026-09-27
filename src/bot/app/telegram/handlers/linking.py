"""Linking handlers: `my_chat_member` promotions and the Check-again callback (§10)."""

from aiogram import Router


def create_linking_router() -> Router:
    router = Router(name="linking")
    return router
