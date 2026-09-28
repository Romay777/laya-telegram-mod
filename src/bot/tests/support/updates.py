"""Fabricated Telegram Updates for the e2e harness (§17)."""

from datetime import UTC, datetime

from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    MessageEntity,
    MessageOriginChat,
    Update,
    User,
)

#: The bot's own Telegram user, as the harness's test token (42:…) implies.
BOT_USER = User(id=42, is_bot=True, first_name="Laya Moderator", username="laya_moderator_bot")


def _user(user_id: int, language_code: str | None, username: str | None = None) -> User:
    return User(
        id=user_id, is_bot=False, first_name="Admin", language_code=language_code, username=username
    )


def user(user_id: int, language_code: str | None = None, username: str | None = None) -> User:
    """A Telegram user for scripted getChatMember answers and the like."""
    return _user(user_id, language_code, username)


def _private_chat_message(
    user_id: int,
    message_id: int,
    language_code: str | None,
    text: str | None = None,
    forward_origin: MessageOriginChat | None = None,
    entities: list[MessageEntity] | None = None,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=_user(user_id, language_code),
        text=text,
        entities=entities,
        forward_origin=forward_origin,
    )


def start_update(user_id: int, language_code: str | None, message_id: int = 1) -> Update:
    """/start from a private chat."""
    return Update(
        update_id=0,
        message=_private_chat_message(user_id, message_id, language_code, text="/start"),
    )


def private_text_update(
    user_id: int,
    text: str,
    *,
    message_id: int,
    language_code: str | None,
    entities: list[MessageEntity] | None = None,
) -> Update:
    """A free-text message from the Admin's private chat, formatting included."""
    return Update(
        update_id=0,
        message=_private_chat_message(
            user_id, message_id, language_code, text=text, entities=entities
        ),
    )


def forwarded_message_update(
    user_id: int,
    *,
    origin_chat_id: int,
    message_id: int,
    language_code: str | None,
    origin_title: str | None = "My Chat",
) -> Update:
    """A message forwarded to the bot from the chat it names (the fallback input)."""
    return Update(
        update_id=0,
        message=_private_chat_message(
            user_id,
            message_id,
            language_code,
            forward_origin=MessageOriginChat(
                date=datetime.now(UTC),
                sender_chat=Chat(id=origin_chat_id, type="supergroup", title=origin_title),
            ),
        ),
    )


def private_callback_update(
    user_id: int,
    data: str,
    message_id: int,
    language_code: str | None,
    username: str | None = None,
) -> Update:
    """A callback button press on the Menu message (or on an Admin Alert copy)."""
    return Update(
        update_id=0,
        callback_query=CallbackQuery(
            id="cb1",
            from_user=_user(user_id, language_code, username),
            chat_instance="test-instance",
            message=_private_chat_message(user_id, message_id, language_code),
            data=data,
        ),
    )


def group_callback_update(
    user_id: int,
    data: str,
    *,
    chat_id: int,
    message_id: int,
    sender_name: str = "Member",
    username: str | None = None,
) -> Update:
    """A callback press on an inline keyboard inside a Linked Chat.

    The Appeal button under a Chat Notice is the one such keyboard; the
    `message` on the callback is the bot's own notice (§7, §8).
    """
    return Update(
        update_id=0,
        callback_query=CallbackQuery(
            id="cb1",
            from_user=User(id=user_id, is_bot=False, first_name=sender_name, username=username),
            chat_instance="test-instance",
            message=Message(
                message_id=message_id,
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="supergroup", title="My Chat"),
                from_user=BOT_USER,
                text="notice text",
            ),
            data=data,
        ),
    )


def group_message_update(
    chat_id: int,
    sender_id: int,
    text: str,
    *,
    message_id: int,
    sender_name: str = "Member",
    from_bot: bool = False,
    entities: list[MessageEntity] | None = None,
) -> Update:
    """A text message from a Member of a Linked Chat (or from a bot in it)."""
    return Update(
        update_id=0,
        message=Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="supergroup", title="My Chat"),
            from_user=User(id=sender_id, is_bot=from_bot, first_name=sender_name),
            text=text,
            entities=entities,
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
