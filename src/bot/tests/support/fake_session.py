"""A recording fake Bot API session (§17).

Every outgoing Bot API call is recorded as a `RecordedCall`; responses are
scripted per method type, with sane defaults (send_message returns the next
synthetic message id, edits return True, and so on).
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageText,
    GetMe,
    GetUpdates,
    RestrictChatMember,
    SendMessage,
)
from aiogram.methods.base import TelegramMethod
from aiogram.types import Chat, Message, User


@dataclass
class RecordedCall:
    method_name: str
    method: TelegramMethod
    result: Any


class FakeBotSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[RecordedCall] = []
        self._next_message_id = 100
        self._scripted: dict[type[TelegramMethod], list[Any]] = {}

    def script(self, method_type: type[TelegramMethod], outcome: Any) -> None:
        """Queue an outcome for the next call of `method_type`: a result or an exception."""
        self._scripted.setdefault(method_type, []).append(outcome)

    def call_names(self) -> list[str]:
        return [call.method_name for call in self.calls]

    def calls_of(self, method_name: str) -> list[RecordedCall]:
        return [call for call in self.calls if call.method_name == method_name]

    async def make_request(  # type: ignore[override]
        self,
        bot: Any,
        method: TelegramMethod[Any],
        timeout: int | None = None,
    ) -> Any:
        try:
            result = self._response_for(method)
        except Exception:
            self.calls.append(RecordedCall(type(method).__name__, method, None))
            raise
        self.calls.append(RecordedCall(type(method).__name__, method, result))
        return result

    def _response_for(self, method: TelegramMethod[Any]) -> Any:
        queue = self._scripted.get(type(method))
        if queue:
            outcome = queue.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return self._default_response(method)

    def _default_response(self, method: TelegramMethod[Any]) -> Any:
        if isinstance(method, SendMessage):
            return Message(
                message_id=self._take_message_id(),
                date=datetime.now(UTC),
                chat=method.chat_id
                if isinstance(method.chat_id, Chat)
                else Chat(id=method.chat_id, type="private"),
            )
        if isinstance(method, EditMessageText | DeleteMessage | AnswerCallbackQuery):
            return True
        if isinstance(method, RestrictChatMember):
            return True
        if isinstance(method, GetMe):
            return User(
                id=42, is_bot=True, first_name="Laya Moderator", username="laya_moderator_bot"
            )
        if isinstance(method, GetUpdates):
            return []
        raise AssertionError(f"unexpected Bot API call in test: {type(method).__name__}")

    def _take_message_id(self) -> int:
        self._next_message_id += 1
        return self._next_message_id

    async def stream_content(  # type: ignore[override]
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        raise AssertionError("file downloads are not expected in tests")
        yield b""  # pragma: no cover

    async def close(self) -> None:
        pass
