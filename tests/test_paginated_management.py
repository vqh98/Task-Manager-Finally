import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import app.bot as bot_module
from app.models import Meeting, Priority, Task


class PaginatedManagementTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_list_edits_one_card_with_next_navigation(self):
        tehran = ZoneInfo("Asia/Tehran")
        now = datetime(2026, 10, 11, 10, tzinfo=tehran)
        tasks = [
            Task(i, 42, f"تسک {i}", now + timedelta(days=i), Priority.IMPORTANT,
                 "مسئول", 10 * i, "", "open", now)
            for i in (1, 2, 3)
        ]
        message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
        fake_db = SimpleNamespace(query_tasks=AsyncMock(return_value=tasks))

        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True):
            await bot_module.render_task_list(
                message, 42, title="همه تسک‌ها", page_key="all", page=0,
            )

        message.answer.assert_not_awaited()
        message.edit_text.assert_awaited_once()
        markup = message.edit_text.await_args.kwargs["reply_markup"]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("taskpage:all:1", callbacks)
        self.assertIn("tasksendall:all", callbacks)
        self.assertNotIn("taskpage:all:-1", callbacks)
        self.assertIn("مورد ۱ از ۳", message.edit_text.await_args.args[0])

    async def test_meeting_list_edits_one_card_with_previous_and_next(self):
        tehran = ZoneInfo("Asia/Tehran")
        now = datetime(2026, 10, 11, 10, tzinfo=tehran)
        events = [
            Meeting(i, 42, f"جلسه {i}", now + timedelta(hours=i),
                    now + timedelta(hours=i + 1), "")
            for i in (1, 2, 3)
        ]
        message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
        fake_db = SimpleNamespace(
            meetings_in_range=AsyncMock(return_value=events),
            meeting_recurrence=AsyncMock(return_value=None),
        )

        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            rendered = await bot_module.render_meeting_page(message, 42, "all", page=1)

        self.assertTrue(rendered)
        message.answer.assert_not_awaited()
        message.edit_text.assert_awaited_once()
        markup = message.edit_text.await_args.kwargs["reply_markup"]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("meetingpage:all:0", callbacks)
        self.assertIn("meetingpage:all:2", callbacks)
        self.assertIn("meetingsendall:all", callbacks)
        self.assertIn("meeting:delete:2:all:1", callbacks)
        self.assertIn("مورد ۲ از ۳", message.edit_text.await_args.args[0])

    async def test_delete_meeting_keeps_current_management_panel(self):
        tehran = ZoneInfo("Asia/Tehran")
        meeting = SimpleNamespace(id=22, title="MBA")
        callback = SimpleNamespace(
            data="meeting:delete:22:all:3",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        fake_db = SimpleNamespace(
            meeting=AsyncMock(return_value=meeting),
            delete_meeting=AsyncMock(return_value=True),
        )

        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "render_meeting_page", AsyncMock(return_value=True)) as render:
            await bot_module.delete_meeting(callback)

        fake_db.delete_meeting.assert_awaited_once_with(22, 42)
        render.assert_awaited_once_with(callback.message, 42, "all", 3)
        callback.answer.assert_awaited_once_with("جلسه حذف شد.", show_alert=False)

    async def test_legacy_delete_button_returns_to_range_selector(self):
        meeting = SimpleNamespace(id=22, title="جلسه قدیمی")
        callback = SimpleNamespace(
            data="meeting:delete:22",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        fake_db = SimpleNamespace(
            meeting=AsyncMock(return_value=meeting),
            delete_meeting=AsyncMock(return_value=True),
        )

        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)):
            await bot_module.delete_meeting(callback)

        callback.message.edit_text.assert_awaited_once()
        self.assertIn("بازهٔ زمانی جلسات", callback.message.edit_text.await_args.args[0])
        self.assertIsNotNone(callback.message.edit_text.await_args.kwargs["reply_markup"])

    async def test_send_all_tasks_respects_current_filter(self):
        tehran = ZoneInfo("Asia/Tehran")
        now = datetime(2026, 10, 11, 10, tzinfo=tehran)
        task = Task(
            1, 42, "تسک فوری", now + timedelta(days=1), Priority.URGENT_IMPORTANT,
            "مسئول", 30, "", "open", now,
        )
        callback = SimpleNamespace(
            data="tasksendall:p1", from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()), answer=AsyncMock(),
        )
        fake_db = SimpleNamespace(query_tasks=AsyncMock(return_value=[task]))

        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)):
            await bot_module.send_all_visible_tasks(callback)

        filters = fake_db.query_tasks.await_args.args[1]
        self.assertEqual(filters, {"priority": 1})
        callback.message.answer.assert_awaited_once()
        self.assertIn("تسک فوری", callback.message.answer.await_args.args[0])

    async def test_send_all_meetings_uses_selected_range(self):
        tehran = ZoneInfo("Asia/Tehran")
        now = datetime(2026, 10, 11, 10, tzinfo=tehran)
        event = Meeting(1, 42, "جلسه امروز", now, now + timedelta(hours=1), "")
        callback = SimpleNamespace(
            data="meetingsendall:today", from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()), answer=AsyncMock(),
        )
        fake_db = SimpleNamespace(meetings_in_range=AsyncMock(return_value=[event]))

        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            await bot_module.send_all_visible_meetings(callback)

        callback.message.answer.assert_awaited_once()
        self.assertIn("جلسه امروز", callback.message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
