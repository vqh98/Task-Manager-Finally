import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import app.bot as bot_module
from app.db import Database
from app.keyboards import task_list_filters


class TaskAssigneeFilterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "tasks.db")
        await self.db.init()
        await self.db.seed_owners(frozenset({1, 9}))
        self.zone = ZoneInfo("Asia/Tehran")
        self.due = datetime.now(self.zone) + timedelta(days=10)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def add(self, name, member=None, owner=1):
        return await self.db.add_task(owner, "تسک " + name, self.due, 2, name, assignee_user_id=member)

    def callback(self, data, owner=1):
        return SimpleNamespace(data=data, from_user=SimpleNamespace(id=owner),
                               message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
                               answer=AsyncMock())

    def patches(self):
        return patch.multiple(bot_module, db=self.db, settings=SimpleNamespace(timezone=self.zone),
                              reject_if_needed=AsyncMock(return_value=False))

    async def test_unassigned_includes_legacy_markers_but_not_linked_users_or_other_owners(self):
        expected = {await self.add(name) for name in ("", "بدون مسئول", "  ", "-", "—")}
        await self.add("بدون مسئول", 22)
        await self.add("مسئول")
        await self.add("", owner=9)
        done = await self.add("")
        await self.db.update_task(done, 1, "status", "done")
        tasks = await self.db.query_tasks(1, {"unassigned": True}, include_unassigned=False, include_done=False)
        self.assertEqual({task.id for task in tasks}, expected)

    async def test_linked_identity_groups_old_names_and_manual_filter_is_exact(self):
        linked = {await self.add("رضا", 22), await self.add("نام قدیمی", 22)}
        manual = await self.add("رضا")
        await self.add("رضا احمدی")
        await self.add("رضا", 33)
        await self.add("رضا", 22, owner=9)
        tasks = await self.db.query_tasks(1, {"assignee_user_id": 22}, include_done=False)
        self.assertEqual({task.id for task in tasks}, linked)
        tasks = await self.db.query_tasks(1, {"assignee_exact": "رضا"}, include_done=False)
        self.assertEqual([task.id for task in tasks], [manual])
        groups = await self.db.task_assignee_groups(1)
        self.assertEqual(next(count for member, _, count in groups if member == 22), 2)
        self.assertTrue(any(member is None and name == "رضا" for member, name, _ in groups))

    async def test_manual_names_with_wildcards_and_quotes_do_not_expand_matches(self):
        target = await self.add("%_' مسئول")
        await self.add("شخص دیگر")
        tasks = await self.db.query_tasks(1, {"assignee_exact": "%_' مسئول"})
        self.assertEqual([task.id for task in tasks], [target])

    async def test_group_list_excludes_done_and_unassigned_but_keeps_retired_assignees(self):
        await self.add("بدون مسئول")
        await self.add("نیروی قدیمی", 22)
        done = await self.add("تکمیل شده", 33)
        await self.db.update_task(done, 1, "status", "done")
        await self.add("شخص دیگر", owner=9)
        self.assertEqual(await self.db.task_assignee_groups(1), [(22, "نیروی قدیمی", 1)])

    async def test_dynamic_filter_keys_are_short_and_resolved_in_current_owner_scope(self):
        name = "نام فارسی طولانی " * 10
        await self.add(name)
        key = bot_module.task_assignee_key(None, name.strip())
        self.assertLessEqual(len(f"tasksendall:{key}".encode()), 64)
        with self.patches():
            config = await bot_module.task_page_config(1, key)
            foreign = await bot_module.task_page_config(9, key)
        self.assertEqual(config[0], {"assignee_exact": name.strip()})
        self.assertIsNone(foreign)

    async def test_unassigned_pagination_and_send_all_keep_filter(self):
        await self.add("")
        await self.add("بدون مسئول")
        await self.add("مسئول")
        callback = self.callback("taskassignee:unassigned")
        with self.patches():
            await bot_module.task_list_by_assignee(callback)
            markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
            keys = [button.callback_data for row in markup.inline_keyboard for button in row]
            self.assertIn("taskpage:unassigned:1", keys)
            callback.data = "taskpage:unassigned:1"
            await bot_module.task_list_page(callback)
            self.assertIn("مورد ۲ از ۲", callback.message.edit_text.await_args.args[0])
            callback.data = "tasksendall:unassigned"
            await bot_module.send_all_visible_tasks(callback)
        text = callback.message.answer.await_args.args[0]
        self.assertIn("تسک‌های بدون مسئول", text)
        self.assertNotIn("تسک مسئول", text)

    async def test_linked_filter_pagination_and_send_all_keep_identity(self):
        await self.add("رضا", 22)
        await self.add("نام قدیمی", 22)
        await self.add("رضا", 33)
        callback = self.callback("taskassignee:au22")
        with self.patches():
            await bot_module.task_list_by_assignee(callback)
            callback.data = "taskpage:au22:1"
            await bot_module.task_list_page(callback)
            self.assertIn("مورد ۲ از ۲", callback.message.edit_text.await_args.args[0])
            callback.data = "tasksendall:au22"
            await bot_module.send_all_visible_tasks(callback)
        self.assertIn("نام قدیمی", callback.message.answer.await_args.args[0])

    async def test_assignee_menu_paginates_and_contains_unassigned_and_back(self):
        for index in range(12):
            await self.add(f"مسئول {index}", 100 + index)
        callback = self.callback("tasklist:assignee")
        with self.patches():
            await bot_module.task_list_assignee_menu(callback)
        buttons = [button for row in callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for button in row]
        keys = [button.callback_data for button in buttons]
        self.assertIn("taskassignee:unassigned", keys)
        self.assertIn("taskassignees:1", keys)
        self.assertIn("tasklist:menu", keys)
        self.assertEqual(len([key for key in keys if key.startswith("taskassignee:au")]), 10)

    async def test_unauthorized_and_foreign_filter_buttons_do_not_render_tasks(self):
        await self.add("شخص خصوصی", 22, owner=9)
        callback = self.callback("taskassignee:au22")
        with self.patches():
            await bot_module.task_list_by_assignee(callback)
        callback.message.edit_text.assert_not_awaited()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])
        with patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=True)):
            await bot_module.task_list_assignee_menu(callback)
        callback.message.edit_text.assert_not_awaited()

    def test_main_filter_menu_exposes_both_entrypoints(self):
        keys = [button.callback_data for row in task_list_filters().inline_keyboard for button in row]
        self.assertIn("tasklist:assignee", keys)
        self.assertIn("taskassignee:unassigned", keys)
