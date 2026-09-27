"""Fabricated Telegram Updates for the e2e harness (§17)."""

from datetime import UTC, datetime

from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    Update,
    User,
)

#: The bot's own Telegram user, as the harness's test token (42:…) implies.
BOT_USER = User(id=42, is_bot=True, first_name="Laya Moderator", username="laya_moderator_bot")


def _user(user_id: int, language_code: str | None) -> User:
    return User(id=user_id, is_bot=False, first_name="Admin", language_code=language_code)


def user(user_id: int, language_code: str | None = None) -> User:
    """A Telegram user for scripted getChatMember answers and the like."""
    return _user(user_id, language_code)


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


def my_chat_member_update(
    chat_id: int,
    chat_type: str,
    *,
    linker_id: int,
    language_code: str | None = "en",
    title: str | None = None,
    can_delete_messages: bool = True,
    can_restrict_members: bool = True,
) -> Update:
    """The bot's own status changing: Telegram promotes it to administrator."""
    return Update(
        update_id=0,
        my_chat_member=ChatMemberUpdated(
            chat=Chat(id=chat_id, type=chat_type, title=title),
            from_user=_user(linker_id, language_code),
            date=datetime.now(UTC),
            old_chat_member=ChatMemberMember(user=BOT_USER, status="member"),
            new_chat_member=ChatMemberAdministrator(
                user=BOT_USER,
                status="administrator",
                can_be_edited=False,
                is_anonymous=False,
                can_manage_chat=True,
                can_delete_messages=can_delete_messages,
                can_manage_video_chats=False,
                can_restrict_members=can_restrict_members,
                can_promote_members=False,
                can_change_info=False,
                can_invite_users=False,
                can_post_stories=False,
                can_edit_stories=False,
                can_delete_stories=False,
                can_send_welcome_messages=False,
            ),
        ),
    )
