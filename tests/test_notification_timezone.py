import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import app.bot as bot_module
from aiogram.exceptions import TelegramForbiddenError
from app.db import Database


class NotificationTimezoneTests(unittest.IsolatedAsyncioTestCase):
    async def test_tehran_delivery_times_and_daily_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            await db.init()
            await db.seed_owners(frozenset({1}))
            settings = SimpleNamespace(
                timezone=ZoneInfo("Asia/Tehran"), digest_hour=22,
                digest_minute=0, morning_hour=9, morning_minute=0,
            )
            bot = SimpleNamespace(send_message=AsyncMock())
            with patch.object(bot_module, "settings", settings, create=True), \
                 patch.object(bot_module, "db", db, create=True), \
                 patch.object(db, "meetings_on", AsyncMock(return_value=[object()])), \
                 patch.object(bot_module, "tomorrow_text", AsyncMock(return_value="tomorrow")), \
                 patch.object(bot_module, "user_day_schedule_text", AsyncMock(return_value="today")):
                # UTC instants corresponding to 05:30, 09:00, 18:30, 22:00 Tehran.
                for hour, minute, expected in [(2, 0, 0), (5, 30, 1), (15, 0, 1), (18, 30, 2)]:
                    instant = datetime(2026, 9, 10, hour, minute, tzinfo=timezone.utc)
                    bot.send_message.return_value = SimpleNamespace(date=instant)
                    with patch.object(bot_module, "datetime") as clock:
                        clock.now.return_value = instant
                        await bot_module.send_configurable_meeting_notifications(bot)
                        await bot_module.send_configurable_meeting_notifications(bot)
                    self.assertEqual(bot.send_message.await_count, expected)
                self.assertEqual([c.args[1] for c in bot.send_message.await_args_list], ["today", "tomorrow"])

    async def test_empty_schedules_are_marked_processed_without_sending_a_message(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            await db.init()
            await db.seed_owners(frozenset({1}))
            settings = SimpleNamespace(
                timezone=ZoneInfo("Asia/Tehran"), digest_hour=22,
                digest_minute=0, morning_hour=9, morning_minute=0,
            )
            bot = SimpleNamespace(send_message=AsyncMock())
            no_meetings = AsyncMock(return_value=[])
            tomorrow_report = AsyncMock(return_value="no tomorrow meetings")
            today_report = AsyncMock(return_value="no today meetings")
            with patch.object(bot_module, "settings", settings, create=True), \
                 patch.object(bot_module, "db", db, create=True), \
                 patch.object(db, "meetings_on", no_meetings), \
                 patch.object(bot_module, "tomorrow_text", tomorrow_report), \
                 patch.object(bot_module, "user_day_schedule_text", today_report):
                for hour, kind in [(5, "morning"), (18, "digest")]:
                    minute = 30
                    instant = datetime(2026, 9, 10, hour, minute, tzinfo=timezone.utc)
                    with patch.object(bot_module, "datetime") as clock:
                        clock.now.return_value = instant
                        await bot_module.send_configurable_meeting_notifications(bot)
                        await bot_module.send_configurable_meeting_notifications(bot)
                    local_day = instant.astimezone(settings.timezone).date()
                    self.assertTrue(await db.notification_sent_today(1, kind, local_day))

            bot.send_message.assert_not_awaited()
            tomorrow_report.assert_not_awaited()
            today_report.assert_not_awaited()
            self.assertEqual(no_meetings.await_count, 2)

    async def test_blocked_user_has_both_meeting_notifications_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            await db.init()
            await db.seed_owners(frozenset({1}))
            settings = SimpleNamespace(
                timezone=ZoneInfo("Asia/Tehran"), digest_hour=22,
                digest_minute=0, morning_hour=9, morning_minute=0,
            )
            instant = datetime(2026, 9, 10, 5, 30, tzinfo=timezone.utc)
            bot = SimpleNamespace(send_message=AsyncMock(side_effect=TelegramForbiddenError(
                method=object(), message="Forbidden: bot was blocked by the user"
            )))
            with patch.object(bot_module, "settings", settings, create=True), \
                 patch.object(bot_module, "db", db, create=True), \
                 patch.object(db, "meetings_on", AsyncMock(return_value=[object()])), \
                 patch.object(bot_module, "user_day_schedule_text", AsyncMock(return_value="today")), \
                 patch.object(bot_module, "datetime") as clock:
                clock.now.return_value = instant
                await bot_module.send_configurable_meeting_notifications(bot)

            config = await db.user_settings(1, {
                "digest_enabled": 1, "morning_enabled": 1,
            })
            self.assertEqual(config["digest_enabled"], 0)
            self.assertEqual(config["morning_enabled"], 0)
