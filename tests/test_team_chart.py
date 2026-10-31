import unittest

import app.bot as bot_module


class FakeTeamDatabase:
    def __init__(self):
        self.members = [(2000 + index, f"نیرو {index}") for index in range(1, 21)]

    async def team_members(self, owner_id):
        return self.members

    async def team_member(self, owner_id, member_id):
        return next((item for item in self.members if item[0] == member_id), None)

    async def task_share_permissions(self, owner_id, member_id):
        return {"can_view": True, "can_edit": False, "can_add": False, "can_delete": False}


class TeamChartViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db = bot_module.db if hasattr(bot_module, "db") else None
        bot_module.db = FakeTeamDatabase()

    async def asyncTearDown(self):
        if self.original_db is not None:
            bot_module.db = self.original_db

    async def test_chart_home_is_compact(self):
        text, markup = await bot_module.team_chart_view(1)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("تعداد نیروهای ثبت‌شده: <b>۲۰</b>", text)
        self.assertEqual(callbacks, ["team:add", "team:page:0", "menu:main"])
        self.assertNotIn("نیرو 1", text)

    async def test_member_list_is_paginated(self):
        text, markup = await bot_module.team_members_view(1, 0)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("نیرو 8", text)
        self.assertNotIn("نیرو 9", text)
        self.assertEqual(sum(value.startswith("team:view:") for value in callbacks), 8)
        self.assertIn("team:page:1", callbacks)

    async def test_member_actions_live_on_detail_page(self):
        view = await bot_module.team_member_detail_view(1, 2001, 0)
        self.assertIsNotNone(view)
        callbacks = [button.callback_data for row in view[1].inline_keyboard for button in row]
        self.assertIn("team:profile:2001", callbacks)
        self.assertIn("eventshare:menu:2001", callbacks)
        self.assertIn("taskshare:menu:2001", callbacks)
        self.assertIn("team:confirm_remove:2001:0", callbacks)

    async def test_task_access_view_has_safe_return(self):
        view = await bot_module.task_share_view(1, 2001)
        callbacks = [button.callback_data for row in view[1].inline_keyboard for button in row]
        self.assertIn("team:view:2001:0", callbacks)


if __name__ == "__main__":
    unittest.main()
