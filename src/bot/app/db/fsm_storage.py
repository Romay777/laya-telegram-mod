"""aiogram FSM storage backed by Postgres (§12 `fsm_state`, no Redis).

Keys are the aiogram `StorageKey` columns of the `fsm_state` table. `thread_id`
uses 0 for "no topic" so the composite primary key stays NOT NULL; keys with a
`business_connection_id` are not used by the bot yet and are not distinguishable
here (§12 fixes the table shape).
"""

from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import FsmState

_KEY_ORDER = ("bot_id", "chat_id", "user_id", "thread_id", "destiny")


class PostgresStorage(BaseStorage):
    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self.session_maker = session_maker

    async def set_state(self, key: StorageKey, state: str | State | None = None) -> None:
        if isinstance(state, State):
            state = state.state
        if state is not None and not isinstance(state, str):
            raise TypeError(f"State must be a str, State or None, got {type(state).__name__}")

        async with self.session_maker() as session:
            await session.execute(
                insert(FsmState)
                .values(**_key_columns(key), state=state)
                .on_conflict_do_update(
                    index_elements=list(_KEY_ORDER),
                    set_={"state": state},
                )
            )
            await session.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        async with self.session_maker() as session:
            row = await session.get(FsmState, _key_values(key))
            return row.state if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        async with self.session_maker() as session:
            await session.execute(
                insert(FsmState)
                .values(**_key_columns(key), data=dict(data))
                .on_conflict_do_update(
                    index_elements=list(_KEY_ORDER),
                    set_={"data": dict(data)},
                )
            )
            await session.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self.session_maker() as session:
            row = await session.get(FsmState, _key_values(key))
            return dict(row.data) if row and row.data else {}

    async def close(self) -> None:
        """Nothing to close: every operation uses its own short-lived session."""


def _key_columns(key: StorageKey) -> dict[str, Any]:
    return {
        "bot_id": key.bot_id,
        "chat_id": key.chat_id,
        "user_id": key.user_id,
        "thread_id": key.thread_id or 0,
        "destiny": key.destiny,
    }


def _key_values(key: StorageKey) -> tuple[Any, ...]:
    columns = _key_columns(key)
    return tuple(columns[name] for name in _KEY_ORDER)
