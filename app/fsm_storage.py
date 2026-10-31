from __future__ import annotations

from typing import Any

from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage


class PruningMemoryStorage(MemoryStorage):
    """Memory FSM storage that does not retain empty records forever.

    Aiogram's default MemoryStorage is backed by a defaultdict. Merely reading
    the state for a new chat creates a permanent empty entry, and clear()
    leaves that entry behind. This variant preserves the same behavior while
    avoiding entries for inactive users.
    """

    async def get_state(self, key: StorageKey) -> str | None:
        record = self.storage.get(key)
        return record.state if record else None

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        record = self.storage.get(key)
        return record.data.copy() if record else {}

    async def set_state(self, key: StorageKey, state: Any = None) -> None:
        if state is None:
            record = self.storage.get(key)
            if record is None:
                return
            record.state = None
            if not record.data:
                self.storage.pop(key, None)
            return
        await super().set_state(key, state)

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        if not data:
            record = self.storage.get(key)
            if record is None:
                return
            record.data = {}
            if record.state is None:
                self.storage.pop(key, None)
            return
        await super().set_data(key, data)
