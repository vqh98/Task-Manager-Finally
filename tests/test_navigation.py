import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app.bot as bot_module


class ActiveFormNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_filter_only_matches_menu_buttons_during_active_state(self):
        navigation_filter = bot_module.ActiveStateNavigationFilter()
        dashboard = SimpleNamespace(text="📊 داشبورد")
        username = SimpleNamespace(text="@someone")
        self.assertTrue(await navigation_filter(dashboard, raw_state="TeamManagement:member"))
        self.assertFalse(await navigation_filter(dashboard, raw_state=None))
        self.assertFalse(await navigation_filter(username, raw_state="TeamManagement:member"))

    async def test_dashboard_button_clears_stale_form_before_navigation(self):
        message = SimpleNamespace(
            text="📊 داشبورد",
            from_user=SimpleNamespace(id=42),
        )
        state = SimpleNamespace(
            get_state=AsyncMock(return_value="TeamManagement:member"),
            clear=AsyncMock(),
        )
        with patch.object(bot_module, "dashboard", new=AsyncMock()) as dashboard:
            await bot_module.leave_stale_form_and_navigate(message, state)
        state.clear.assert_awaited_once()
        dashboard.assert_awaited_once_with(message)

    async def test_team_member_prompt_has_back_button(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(answer=AsyncMock()),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(set_state=AsyncMock())
        with patch.object(bot_module, "reject_if_needed", new=AsyncMock(return_value=False)):
            await bot_module.ask_team_member(callback, state)
        markup = callback.message.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "nav:team")

    async def test_every_owner_main_menu_button_is_a_form_escape_target(self):
        from app.keyboards import main_menu

        button_texts = {
            button.text
            for row in main_menu().keyboard
            for button in row
        }
        self.assertTrue(button_texts.issubset(bot_module.MAIN_MENU_NAVIGATION_TEXTS))


if __name__ == "__main__":
    unittest.main()
