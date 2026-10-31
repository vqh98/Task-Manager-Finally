import unittest

import app.bot as bot_module


class FakeAccessDatabase:
    async def managers(self):
        return [(1000 + index, f"کاربر {index}") for index in range(1, 21)]

    async def access_code_details(self):
        return [
            ("ONE", True, "single", 1, []),
            ("TEAM", True, "multi", 100, [1001, 1002]),
        ]


class AccessManagementViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db = bot_module.db if hasattr(bot_module, "db") else None
        bot_module.db = FakeAccessDatabase()

    async def asyncTearDown(self):
        if self.original_db is not None:
            bot_module.db = self.original_db

    async def test_home_is_compact_and_does_not_render_every_user(self):
        text, markup = await bot_module.access_management_view()
        buttons = [button for row in markup.inline_keyboard for button in row]
        self.assertEqual(len(buttons), 4)
        self.assertIn("کاربران فعال: <b>۲۰</b>", text)
        self.assertNotIn("کاربر 1 —", text)

    async def test_users_are_paginated_eight_per_page(self):
        text, markup = await bot_module.access_users_view(0)
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("کاربر 8", text)
        self.assertNotIn("کاربر 9", text)
        self.assertEqual(sum(value.startswith("access_user:view:") for value in callbacks), 8)
        self.assertIn("access_users:page:1", callbacks)

    async def test_code_list_keeps_actions_in_detail_pages(self):
        text, markup = await bot_module.access_codes_view("all")
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("ONE", text)
        self.assertIn("TEAM", text)
        self.assertIn("access_code:view:ONE", callbacks)
        self.assertIn("access_code:view:TEAM", callbacks)
        self.assertFalse(any(value.startswith("access_code:disable:") for value in callbacks))


if __name__ == "__main__":
    unittest.main()
