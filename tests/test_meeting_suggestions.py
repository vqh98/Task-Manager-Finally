import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import app.bot as bot_module


class MeetingSuggestionTests(unittest.IsolatedAsyncioTestCase):
    async def _slots(self, duration_minutes=60, meetings=None, quiet=None):
        timezone = ZoneInfo("Asia/Tehran")
        target_day = date(2026, 9, 15)
        fake_db = SimpleNamespace(
            user_settings=AsyncMock(return_value={
                "meeting_window_start": 11 * 60,
                "meeting_window_end": 19 * 60,
            }),
            meetings_between=AsyncMock(return_value=meetings or []),
            meeting_quiet_hours=AsyncMock(return_value=quiet or []),
        )
        fake_settings = SimpleNamespace(timezone=timezone)
        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", fake_settings, create=True), \
             patch.object(bot_module, "notification_setting_defaults", return_value={}), \
             patch.object(bot_module, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 9, 14, 12, 0, tzinfo=timezone)
            slots = await bot_module.free_meeting_slots(
                42, target_day, timedelta(minutes=duration_minutes), limit=20,
            )
        return slots, fake_db

    async def test_one_hour_suggestions_are_round_and_last_start_is_18(self):
        slots, fake_db = await self._slots()
        self.assertEqual([slot.hour for slot in slots], list(range(11, 19)))
        self.assertTrue(all(slot.minute == 0 for slot in slots))
        start, end = fake_db.meetings_between.await_args.args[1:3]
        self.assertEqual((start.hour, start.minute), (11, 0))
        self.assertEqual((end.hour, end.minute, end.second), (19, 0, 1))

    async def test_duration_and_conflicts_never_push_end_past_window(self):
        timezone = ZoneInfo("Asia/Tehran")
        meeting = SimpleNamespace(
            starts_at=datetime(2026, 9, 15, 13, 30, tzinfo=timezone),
            ends_at=datetime(2026, 9, 15, 14, 30, tzinfo=timezone),
        )
        slots, _ = await self._slots(
            duration_minutes=120,
            meetings=[meeting],
            quiet=[(1, 16 * 60, 17 * 60)],
        )
        self.assertEqual([slot.hour for slot in slots], [11, 17])
        self.assertTrue(all(slot + timedelta(hours=2) <= slot.replace(hour=19) for slot in slots))


class MeetingWindowInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_persian_hour_range_is_saved_in_minutes(self):
        message = SimpleNamespace(
            text="۱۱ تا ۱۹",
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(clear=AsyncMock())
        fake_db = SimpleNamespace(update_meeting_window=AsyncMock())

        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "show_meeting_settings", AsyncMock()):
            await bot_module.save_meeting_window(message, state)

        fake_db.update_meeting_window.assert_awaited_once_with(42, 660, 1140)
        state.clear.assert_awaited_once()
        self.assertIn("۱۱:۰۰", message.answer.await_args.args[0])
        self.assertIn("۱۹:۰۰", message.answer.await_args.args[0])

    async def test_non_round_or_reversed_ranges_are_rejected(self):
        fake_db = SimpleNamespace(update_meeting_window=AsyncMock())
        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)):
            for value in ("11:30 تا 19", "19 تا 11", "11 تا 25", "از 11 تا 19"):
                message = SimpleNamespace(
                    text=value,
                    from_user=SimpleNamespace(id=42),
                    answer=AsyncMock(),
                )
                state = SimpleNamespace(clear=AsyncMock())
                await bot_module.save_meeting_window(message, state)
                state.clear.assert_not_awaited()

        fake_db.update_meeting_window.assert_not_awaited()
