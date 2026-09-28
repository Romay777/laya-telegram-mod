"""Scripted `getChatMember` / `getChat` answers for the fake Bot session.

The Linking checks (§10 step 3) read the live membership of the bot and of the
person who added it; the e2e harness scripts those answers per test.
"""

from aiogram.types import (
    AcceptedGiftTypes,
    ChatFullInfo,
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberOwner,
    ChatPermissions,
    User,
)

from tests.support.updates import BOT_USER


def member_owner(user: User) -> ChatMemberOwner:
    return ChatMemberOwner(user=user, status="creator", is_anonymous=False)


def member_administrator(
    user: User,
    *,
    can_delete_messages: bool = True,
    can_restrict_members: bool = True,
) -> ChatMemberAdministrator:
    return ChatMemberAdministrator(
        user=user,
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
    )


def member_member(user: User) -> ChatMemberMember:
    return ChatMemberMember(user=user, status="member")


def chat_facts(
    chat_id: int,
    chat_type: str,
    title: str | None = None,
    permissions: ChatPermissions | None = None,
) -> ChatFullInfo:
    """A `getChat` answer: what the Check-again re-check needs to know."""
    return ChatFullInfo(
        id=chat_id,
        type=chat_type,
        title=title,
        accent_color_id=0,
        max_reaction_count=0,
        accepted_gift_types=AcceptedGiftTypes(
            unlimited_gifts=False,
            limited_gifts=False,
            unique_gifts=False,
            premium_subscription=False,
            gifts_from_channels=False,
        ),
        permissions=permissions,
    )


__all__ = ["BOT_USER", "chat_facts", "member_administrator", "member_member", "member_owner"]
