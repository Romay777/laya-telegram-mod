"""Fabricated Telegram Updates for the e2e harness (§17)."""

from datetime import UTC, datetime

from aiogram.types import CallbackQuery, Chat, Message, Update, User


def _user(user_id: int, language_code: str | None) -> User:
    return User(id=user_id, is_bot=False, first_name="Admin", language_code=language_code)


def _private_chat_message(
    user_id: int, message_id: int, language_code: str | None, text: str | None = None
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=_user(user_id, language_code),
        text=text,
    )


def start_update(user_id: int, language_code: str | None, message_id: int = 1) -> Update:
    """/start from a private chat."""
    return Update(
        update_id=0,
        message=_private_chat_message(user_id, message_id, language_code, text="/start"),
    )


def private_callback_update(
    user_id: int,
    data: str,
    message_id: int,
    language_code: str | None,
) -> Update:
    """A callback button press on the Menu message."""
    return Update(
        update_id=0,
        callback_query=CallbackQuery(
            id="cb1",
            from_user=_user(user_id, language_code),
            chat_instance="test-instance",
            message=_private_chat_message(user_id, message_id, language_code),
            data=data,
        ),
    )
