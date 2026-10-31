import unittest

from aiogram.fsm.storage.base import StorageKey

from app.fsm_storage import PruningMemoryStorage


class PruningMemoryStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_do_not_create_empty_records(self):
        storage = PruningMemoryStorage()
        key = StorageKey(bot_id=1, chat_id=2, user_id=3)
        self.assertIsNone(await storage.get_state(key))
        self.assertEqual(await storage.get_data(key), {})
        self.assertEqual(len(storage.storage), 0)

    async def test_clear_removes_record(self):
        storage = PruningMemoryStorage()
        key = StorageKey(bot_id=1, chat_id=2, user_id=3)
        await storage.set_state(key, "Meeting:confirm")
        await storage.set_data(key, {"meeting_source": "long message"})
        self.assertEqual(len(storage.storage), 1)
        await storage.set_state(key, None)
        await storage.set_data(key, {})
        self.assertEqual(len(storage.storage), 0)

    async def test_state_and_data_remain_compatible(self):
        storage = PruningMemoryStorage()
        key = StorageKey(bot_id=1, chat_id=2, user_id=3)
        await storage.set_state(key, "Task:title")
        await storage.set_data(key, {"title": "demo"})
        self.assertEqual(await storage.get_state(key), "Task:title")
        self.assertEqual(await storage.get_data(key), {"title": "demo"})
