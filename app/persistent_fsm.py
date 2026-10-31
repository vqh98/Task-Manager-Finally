import json
from dataclasses import asdict

import aiosqlite
from aiogram.fsm.storage.base import BaseStorage


class PersistentFSMStorage(BaseStorage):
    """Keep active conversation forms across process restarts."""

    def __init__(self, path):
        self.path = str(path)

    def _key(self, key):
        return json.dumps(asdict(key), sort_keys=True)

    async def _read(self, key, column):
        async with aiosqlite.connect(self.path, timeout=15) as connection:
            await connection.execute('CREATE TABLE IF NOT EXISTS conversation_forms (key TEXT PRIMARY KEY, state TEXT, data TEXT NOT NULL DEFAULT \'{}\')')
            row = await (await connection.execute(f'SELECT {column} FROM conversation_forms WHERE key=?', (self._key(key),))).fetchone()
            await connection.commit()
            return row[0] if row else None

    async def _write(self, key, column, value):
        async with aiosqlite.connect(self.path, timeout=15) as connection:
            await connection.execute('CREATE TABLE IF NOT EXISTS conversation_forms (key TEXT PRIMARY KEY, state TEXT, data TEXT NOT NULL DEFAULT \'{}\')')
            await connection.execute(f'INSERT INTO conversation_forms(key,{column}) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET {column}=excluded.{column}', (self._key(key), value))
            await connection.execute("DELETE FROM conversation_forms WHERE key=? AND state IS NULL AND data='{}'", (self._key(key),))
            await connection.commit()

    async def set_state(self, key, state=None):
        await self._write(key, 'state', state.state if hasattr(state, 'state') else state)

    async def get_state(self, key):
        return await self._read(key, 'state')

    async def set_data(self, key, data):
        await self._write(key, 'data', json.dumps(dict(data), ensure_ascii=False))

    async def get_data(self, key):
        return json.loads(await self._read(key, 'data') or '{}')

    async def close(self):
        pass
