"""Seam: the Postgres FSM storage.

FSM state is stored in Postgres, so it survives a bot restart: the "restart"
here is a fresh storage instance over the same database (§17).
"""

from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from app.db.fsm_storage import PostgresStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

KEY = StorageKey(bot_id=42, chat_id=100, user_id=1, destiny="default")
OTHER_KEY = StorageKey(bot_id=42, chat_id=100, user_id=2, destiny="default")


async def test_state_and_data_survive_restart(postgres_url: str) -> None:
    first_boot = PostgresStorage(async_sessionmaker(create_async_engine(postgres_url)))

    await first_boot.set_state(KEY, "menu:language")
    await first_boot.set_data(KEY, {"draft": "kept"})
    await first_boot.set_state(OTHER_KEY, None)

    engine = create_async_engine(postgres_url)  # same DB, fresh storage instance
    second_boot = PostgresStorage(async_sessionmaker(engine))
    try:
        assert await second_boot.get_state(KEY) == "menu:language"
        assert await second_boot.get_data(KEY) == {"draft": "kept"}
        assert await second_boot.get_state(OTHER_KEY) is None
        assert await second_boot.get_data(OTHER_KEY) == {}
    finally:
        await engine.dispose()


async def test_update_data_merges_and_states_accept_state_objects(postgres_url: str) -> None:
    class Menu(StatesGroup):
        language = State()

    key = StorageKey(bot_id=42, chat_id=100, user_id=3, destiny="default")
    storage = PostgresStorage(async_sessionmaker(create_async_engine(postgres_url)))

    await storage.set_state(key, Menu.language)
    assert await storage.get_state(key) == Menu.language.state

    await storage.update_data(key, {"a": 1})
    await storage.update_data(key, {"b": 2})
    assert await storage.get_data(key) == {"a": 1, "b": 2}

    await storage.set_state(key, None)  # reset the state, keep the data
    assert await storage.get_state(key) is None
    assert await storage.get_data(key) == {"a": 1, "b": 2}
