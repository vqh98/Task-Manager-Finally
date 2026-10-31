import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import app.bot as bot_module
from aiogram.exceptions import TelegramBadRequest
from app.keyboards import main_menu, meeting_confirmation, meeting_range_options


class MeetingManagementTests(unittest.TestCase):
    def setUp(self):
        self.tehran = ZoneInfo("Asia/Tehran")

    def test_suggested_time_date_label_contains_weekday_and_jalali_date(self):
        self.assertEqual(
            bot_module.fmt_meeting_day(date(2026, 10, 22)),
            "پنجشنبه، ۳۰ مهر ۱۴۰۵",
        )

    def test_quiet_range_overlap_is_detected(self):
        fake_settings = SimpleNamespace(timezone=self.tehran)
        starts_at = datetime(2026, 9, 14, 14, 0, tzinfo=self.tehran)
        ends_at = datetime(2026, 9, 14, 15, 0, tzinfo=self.tehran)

        with patch.object(bot_module, "settings", fake_settings, create=True):
            result = bot_module.overlapping_quiet_ranges(
                starts_at, ends_at, [(1, 14 * 60, 15 * 60)]
            )

        self.assertEqual(result, [(14 * 60, 15 * 60)])

    def test_quiet_range_boundaries_do_not_overlap(self):
        fake_settings = SimpleNamespace(timezone=self.tehran)
        quiet = [(1, 14 * 60, 15 * 60)]

        with patch.object(bot_module, "settings", fake_settings, create=True):
            before = bot_module.overlapping_quiet_ranges(
                datetime(2026, 9, 14, 13, 0, tzinfo=self.tehran),
                datetime(2026, 9, 14, 14, 0, tzinfo=self.tehran),
                quiet,
            )
            after = bot_module.overlapping_quiet_ranges(
                datetime(2026, 9, 14, 15, 0, tzinfo=self.tehran),
                datetime(2026, 9, 14, 16, 0, tzinfo=self.tehran),
                quiet,
            )

        self.assertEqual(before, [])
        self.assertEqual(after, [])

    def test_today_button_is_available_in_meeting_management(self):
        buttons = [
            button
            for row in meeting_range_options().inline_keyboard
            for button in row
        ]
        today = next(
            (button for button in buttons if button.callback_data == "meetings:range:today"),
            None,
        )
        self.assertIsNotNone(today)
        self.assertEqual(today.text, "📆 جلسات امروز")

    def test_today_button_is_available_in_main_menu(self):
        buttons = [
            button
            for row in main_menu().keyboard
            for button in row
        ]
        today = next(
            (button for button in buttons if button.text == "📆 جلسات امروز"),
            None,
        )
        self.assertIsNotNone(today)

    def test_meeting_confirmation_defaults_to_no_recurrence(self):
        buttons = [
            button
            for row in meeting_confirmation(60).inline_keyboard
            for button in row
        ]
        recurrence = next(
            (button for button in buttons if button.callback_data == "meeting:repeat"),
            None,
        )
        self.assertIsNotNone(recurrence)
        self.assertEqual(recurrence.text, "🔁 تکرار: بدون تکرار")

    def test_tomorrow_button_is_available_in_meeting_management(self):
        buttons = [
            button
            for row in meeting_range_options().inline_keyboard
            for button in row
        ]

        tomorrow = next(
            (button for button in buttons if button.callback_data == "meetings:range:tomorrow"),
            None,
        )
        self.assertIsNotNone(tomorrow)
        self.assertEqual(tomorrow.text, "📅 جلسات فردا")

    def test_long_forwarded_announcement_is_recognized_as_a_meeting(self):
        message = SimpleNamespace(
            text=(
                "سلام، وقت بخیر\n\nیکشنبه ساعت ۱۴ جلسه تیم تبلیغات «باران» با شورای "
                "سردبیری برگزار می‌شود.\n\nموضوع جلسه: بررسی راهکارهای درآمدی و ابزارهای هوش مصنوعی"
            ),
            caption=None,
            forward_origin=object(),
        )
        self.assertTrue(bot_module.looks_like_scheduled_meeting_message(message))


class MeetingManagementHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_temporary_weekday_override_is_confirmed_as_compound_schedule(self):
        tehran = ZoneInfo("Asia/Tehran")
        source = ("جلسات DBA دوشنبه و چهارشنبه ساعت 17 تا 18 است؛ "
                  "تایم دوشنبه از این هفته میشه 15 تا 17 تا شش هفته آینده")
        message = SimpleNamespace(text=source, caption=None, from_user=SimpleNamespace(id=42), answer=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(return_value={}), update_data=AsyncMock(),
                                set_state=AsyncMock(), clear=AsyncMock())
        fake_ai = SimpleNamespace(
            available=True,
            parse_meeting=AsyncMock(return_value=SimpleNamespace(
                title="DBA", starts_at=datetime(2026, 10, 19, 17, tzinfo=tehran),
                ends_at=datetime(2026, 10, 19, 18, tzinfo=tehran))),
            summarize_meeting=AsyncMock(return_value="DBA"),
        )
        with patch.object(bot_module, "ai_service", fake_ai, create=True), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)):
            await bot_module.meeting_message(message, state)
        saved = state.update_data.await_args.kwargs
        self.assertEqual(len(saved["meeting_schedule_parts"]), 3)
        self.assertIn("۶ نوبت", message.answer.await_args.args[0])

    async def test_compound_schedule_replaces_existing_series_before_creation(self):
        start = datetime(2026, 10, 19, 15, 30, tzinfo=ZoneInfo("Asia/Tehran"))
        parts = [{
            "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=2)).isoformat(),
            "recurrence": {"frequency": "weekly", "interval": 1, "weekdays": [0],
                           "jalali_day": None, "ends_on": "2026-11-23"},
        }]
        fake_db = SimpleNamespace(
            active_meeting_recurrence_ids_by_title=AsyncMock(return_value=[3]),
            set_meeting_recurrence_active=AsyncMock(return_value=True),
            create_meeting_recurrence=AsyncMock(return_value=(13, 1073)),
        )
        with patch.object(bot_module, "db", fake_db, create=True):
            label = await bot_module._persist_meeting_data(
                {"meeting_title": "جلسات DBA", "meeting_schedule_parts": parts, "source_text": ""}, 42
            )
        fake_db.set_meeting_recurrence_active.assert_awaited_once_with(3, 42, False)
        fake_db.create_meeting_recurrence.assert_awaited_once()
        self.assertIn("جایگزین شد", label)

    async def test_natural_morning_time_does_not_offer_suggestions(self):
        tehran = ZoneInfo("Asia/Tehran")
        source = "دوشنبه ۸ صبح صبحانه دورهمی کارپ"
        ai_parsed = SimpleNamespace(
            title="صبحانه دورهمی کارپ",
            starts_at=datetime(2026, 10, 19, 9, tzinfo=tehran),
            ends_at=datetime(2026, 10, 19, 10, tzinfo=tehran),
        )
        message = SimpleNamespace(
            text=source, caption=None, from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={}), update_data=AsyncMock(),
            set_state=AsyncMock(), clear=AsyncMock(),
        )
        fake_ai = SimpleNamespace(
            available=True,
            parse_meeting=AsyncMock(return_value=ai_parsed),
            summarize_meeting=AsyncMock(return_value="صبحانه دورهمی کارپ"),
        )
        fake_db = SimpleNamespace(
            meeting_suggestion_window=AsyncMock(return_value=(8 * 60, 23 * 60)),
            meeting_quiet_hours=AsyncMock(return_value=[]),
            overlapping_meetings=AsyncMock(return_value=[]),
        )

        with patch.object(bot_module, "ai_service", fake_ai, create=True), \
             patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "default_meeting_duration_minutes", AsyncMock(return_value=60)):
            await bot_module.meeting_message(message, state)

        saved = state.update_data.await_args.kwargs
        self.assertEqual(datetime.fromisoformat(saved["starts_at"]).hour, 8)
        self.assertEqual(datetime.fromisoformat(saved["ends_at"]).hour, 9)
        self.assertNotIn("زمان پیشنهادی", message.answer.await_args.args[0])

    async def test_explicit_single_range_overrides_wrong_ai_duration(self):
        tehran = ZoneInfo("Asia/Tehran")
        source = "جلسه با دادستان استان خراسان رضوی فردا از ساعت ۹ تا ساعت ۱۱"
        ai_parsed = SimpleNamespace(
            title="با دادستان استان خراسان رضوی",
            starts_at=datetime(2026, 10, 19, 9, tzinfo=tehran),
            ends_at=datetime(2026, 10, 19, 10, tzinfo=tehran),
        )
        message = SimpleNamespace(
            text=source, caption=None, from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={}), update_data=AsyncMock(),
            set_state=AsyncMock(), clear=AsyncMock(),
        )
        fake_ai = SimpleNamespace(
            available=True,
            parse_meeting=AsyncMock(return_value=ai_parsed),
            summarize_meeting=AsyncMock(return_value="با دادستان استان خراسان رضوی"),
        )
        fake_db = SimpleNamespace(
            meeting_suggestion_window=AsyncMock(return_value=(8 * 60, 23 * 60)),
            meeting_quiet_hours=AsyncMock(return_value=[]),
            overlapping_meetings=AsyncMock(return_value=[]),
        )

        with patch.object(bot_module, "ai_service", fake_ai, create=True), \
             patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "default_meeting_duration_minutes", AsyncMock(return_value=60)):
            await bot_module.meeting_message(message, state)

        saved = state.update_data.await_args.kwargs
        self.assertEqual(datetime.fromisoformat(saved["starts_at"]).hour, 9)
        self.assertEqual(datetime.fromisoformat(saved["ends_at"]).hour, 11)
        self.assertEqual(saved["meeting_duration"], 2 * 60 * 60)

    async def test_explicit_topic_overrides_wrong_ai_summary(self):
        tehran = ZoneInfo("Asia/Tehran")
        source = (
            "شنبه ۱۸ مهر ساعت ۱۶\nجلسه با جناب هاشمی\n"
            "موضوع: شورای سردبیری\nلطفاً رأس ساعت در جلسه حضور داشته باشید."
        )
        ai_parsed = SimpleNamespace(
            title="حضور داشته باشید",
            starts_at=datetime(2026, 10, 10, 16, tzinfo=tehran),
            ends_at=datetime(2026, 10, 10, 17, tzinfo=tehran),
        )
        message = SimpleNamespace(
            text=source, caption=None, from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={}), update_data=AsyncMock(),
            set_state=AsyncMock(), clear=AsyncMock(),
        )
        fake_ai = SimpleNamespace(
            available=True,
            parse_meeting=AsyncMock(return_value=ai_parsed),
            summarize_meeting=AsyncMock(return_value="خانم فرخداهی"),
        )
        fake_db = SimpleNamespace(
            meeting_suggestion_window=AsyncMock(return_value=(8 * 60, 23 * 60)),
            meeting_quiet_hours=AsyncMock(return_value=[]),
            overlapping_meetings=AsyncMock(return_value=[]),
        )

        with patch.object(bot_module, "ai_service", fake_ai, create=True), \
             patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "default_meeting_duration_minutes", AsyncMock(return_value=60)):
            await bot_module.meeting_message(message, state)

        saved = state.update_data.await_args.kwargs
        self.assertEqual(saved["meeting_title"], "شورای سردبیری")

    async def test_person_meeting_overrides_ai_attendance_boilerplate(self):
        tehran = ZoneInfo("Asia/Tehran")
        source = (
            "یکشنبه ۱۹ مهر ساعت ۱۲\nجلسه با جناب ملایی\n"
            "افراد حاضر در جلسه: خانم فرخداهی و آقایان ناهید و پرستار\n"
            "لطفاً رأس ساعت در جلسه حضور داشته باشید."
        )
        ai_parsed = SimpleNamespace(
            title="حضور داشته باشید",
            starts_at=datetime(2026, 10, 11, 12, tzinfo=tehran),
            ends_at=datetime(2026, 10, 11, 13, tzinfo=tehran),
        )
        message = SimpleNamespace(
            text=source, caption=None, from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={}), update_data=AsyncMock(),
            set_state=AsyncMock(), clear=AsyncMock(),
        )
        fake_ai = SimpleNamespace(
            available=True,
            parse_meeting=AsyncMock(return_value=ai_parsed),
            summarize_meeting=AsyncMock(return_value="حضور داشته باشید"),
        )
        fake_db = SimpleNamespace(
            meeting_suggestion_window=AsyncMock(return_value=(8 * 60, 23 * 60)),
            meeting_quiet_hours=AsyncMock(return_value=[]),
            overlapping_meetings=AsyncMock(return_value=[]),
        )

        with patch.object(bot_module, "ai_service", fake_ai, create=True), \
             patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "default_meeting_duration_minutes", AsyncMock(return_value=60)):
            await bot_module.meeting_message(message, state)

        saved = state.update_data.await_args.kwargs
        self.assertEqual(saved["meeting_title"], "با جناب ملایی")

    async def test_confirm_saves_without_a_separate_recurrence_prompt(self):
        callback = SimpleNamespace()
        state = SimpleNamespace()
        with patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "_save_confirmed_meeting", AsyncMock()) as save:
            await bot_module.confirm_meeting(callback, state)

        save.assert_awaited_once_with(callback, state)

    async def test_recurrence_choice_returns_to_confirmation_instead_of_saving(self):
        tehran = ZoneInfo("Asia/Tehran")
        starts_at = datetime(2026, 9, 29, 14, 0, tzinfo=tehran)
        ends_at = starts_at + timedelta(hours=1)
        callback = SimpleNamespace(
            data="meeting:repeat:weekly",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        data = {
            "meeting_title": "جلسه آزمایشی",
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "meeting_duration": 3600,
        }
        state = SimpleNamespace(
            get_data=AsyncMock(return_value=data),
            update_data=AsyncMock(),
        )
        markup = object()
        with patch.object(
            bot_module,
            "meeting_confirmation_view",
            AsyncMock(return_value=("تأیید نهایی", markup)),
        ) as confirmation, patch.object(
            bot_module, "_save_confirmed_meeting", AsyncMock()
        ) as save:
            await bot_module.set_meeting_recurrence(callback, state)

        recurrence = {
            "frequency": "weekly", "interval": 1,
            "weekdays": [starts_at.weekday()],
        }
        state.update_data.assert_awaited_once_with(meeting_recurrence=recurrence)
        confirmation.assert_awaited_once()
        callback.message.edit_text.assert_awaited_once_with(
            "تأیید نهایی", reply_markup=markup,
        )
        save.assert_not_awaited()

    async def test_today_main_menu_button_uses_current_local_day(self):
        tehran = ZoneInfo("Asia/Tehran")
        now = datetime(2026, 9, 29, 0, 5, tzinfo=tehran)
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )

        meetings_on = AsyncMock(return_value=[])
        with patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "db", SimpleNamespace(meetings_on=meetings_on), create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            await bot_module.today_meetings(message)

        meetings_on.assert_awaited_once_with(42, now.date())
        message.answer.assert_awaited_once()
        self.assertIn("امروز", message.answer.await_args.args[0])

    async def test_fallback_recovers_meeting_when_fsm_state_is_stale(self):
        message = SimpleNamespace(
            text=(
                "سلام، وقت بخیر\nیکشنبه ساعت ۱۴ جلسه تیم تبلیغات باران با شورای "
                "سردبیری برگزار می‌شود.\nموضوع جلسه: بررسی راهکارهای درآمدی"
            ),
            caption=None,
            forward_origin=object(),
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_state=AsyncMock(return_value="OldFlow:unknown"),
            clear=AsyncMock(),
        )
        with patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "meeting_message", AsyncMock()) as meeting_handler:
            await bot_module.fallback(message, state)

        state.clear.assert_awaited_once_with()
        meeting_handler.assert_awaited_once_with(message, state)
        message.answer.assert_not_awaited()

    async def test_fallback_keeps_generic_reply_for_unrelated_text(self):
        message = SimpleNamespace(
            text="سلام، خوبی؟", caption=None, forward_origin=None,
            from_user=SimpleNamespace(id=42), answer=AsyncMock(),
        )
        state = SimpleNamespace(get_state=AsyncMock(), clear=AsyncMock())
        with patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "meeting_message", AsyncMock()) as meeting_handler:
            await bot_module.fallback(message, state)

        meeting_handler.assert_not_awaited()
        state.clear.assert_not_awaited()
        message.answer.assert_awaited_once()

    async def test_schedule_warnings_include_configured_quiet_ranges(self):
        tehran = ZoneInfo("Asia/Tehran")
        fake_db = SimpleNamespace(
            overlapping_meetings=AsyncMock(return_value=[]),
            meeting_quiet_hours=AsyncMock(return_value=[(7, 14 * 60, 15 * 60)]),
        )
        fake_settings = SimpleNamespace(timezone=tehran)

        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", fake_settings, create=True):
            meetings, quiet = await bot_module.schedule_warnings(
                42,
                datetime(2026, 9, 14, 14, 0, tzinfo=tehran),
                datetime(2026, 9, 14, 15, 0, tzinfo=tehran),
            )

        self.assertEqual(meetings, [])
        self.assertEqual(quiet, [(14 * 60, 15 * 60)])
        fake_db.meeting_quiet_hours.assert_awaited_once_with(42)

    async def test_quiet_range_warning_offers_explicit_override_button(self):
        tehran = ZoneInfo("Asia/Tehran")
        starts_at = datetime(2026, 9, 14, 14, 0, tzinfo=tehran)
        ends_at = datetime(2026, 9, 14, 15, 0, tzinfo=tehran)
        warnings = ([], [(14 * 60, 15 * 60)])

        with patch.object(bot_module, "schedule_warnings", AsyncMock(return_value=warnings)), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True):
            text, markup = await bot_module.meeting_confirmation_view(
                42, "جلسه آزمایشی", starts_at, ends_at, 60,
            )

        buttons = [button for row in markup.inline_keyboard for button in row]
        override = next(
            (button for button in buttons if button.callback_data == "meeting:confirm"),
            None,
        )
        self.assertIsNotNone(override)
        self.assertEqual(override.text, "⚠️ ثبت با وجود ممنوعیت")
        self.assertIn("بازهٔ ممنوعه", text)

    async def test_regular_overlap_uses_clear_overlap_override_label(self):
        tehran = ZoneInfo("Asia/Tehran")
        meeting = SimpleNamespace(
            id=9,
            title="جلسه قبلی",
            starts_at=datetime(2026, 9, 14, 14, 0, tzinfo=tehran),
            ends_at=datetime(2026, 9, 14, 15, 0, tzinfo=tehran),
        )
        with patch.object(bot_module, "schedule_warnings", AsyncMock(return_value=([meeting], []))), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True):
            _, markup = await bot_module.meeting_confirmation_view(
                42, "جلسه جدید", meeting.starts_at, meeting.ends_at, 60,
            )

        labels = [button.text for row in markup.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("⚠️ ثبت با وجود تداخل", labels)
        self.assertTrue(any(label.startswith("🔄 جابه‌جایی جلسه قبلی") for label in labels))
        self.assertIn("meeting:move:9", callbacks)

    async def test_moving_previous_meeting_automatically_saves_new_meeting(self):
        tehran = ZoneInfo("Asia/Tehran")
        original_start = datetime(2026, 10, 6, 14, 0, tzinfo=tehran)
        original_end = original_start + timedelta(hours=1)
        moved_start = datetime(2026, 10, 6, 16, 0, tzinfo=tehran)
        previous = SimpleNamespace(id=9, title="جلسه قبلی")
        message = SimpleNamespace(
            text="امروز ساعت ۱۶",
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={
                "meeting_owner_id": 42,
                "meeting_title": "جلسه جدید",
                "starts_at": original_start.isoformat(),
                "ends_at": original_end.isoformat(),
                "move_target": "9",
                "move_duration": 3600,
                "move_resume_new": True,
            }),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
            clear=AsyncMock(),
        )
        fake_db = SimpleNamespace(
            meeting=AsyncMock(return_value=previous),
            move_meeting=AsyncMock(return_value=True),
            add_meeting=AsyncMock(return_value=77),
        )
        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "parse_datetime", return_value=moved_start), \
             patch.object(bot_module, "schedule_warnings", AsyncMock(return_value=([], []))):
            await bot_module.save_moved_meeting(message, state)

        fake_db.move_meeting.assert_awaited_once_with(
            9, 42, moved_start, moved_start + timedelta(hours=1),
        )
        fake_db.add_meeting.assert_awaited_once_with(
            42, "جلسه جدید", original_start, original_end, "",
        )
        state.set_state.assert_not_awaited()
        state.clear.assert_awaited_once()
        self.assertIn("جلسه قبلی", message.answer.await_args.args[0])
        self.assertIn("جلسه #77", message.answer.await_args.args[0])

    async def test_move_previous_prompt_offers_delete_and_back_actions(self):
        tehran = ZoneInfo("Asia/Tehran")
        starts_at = datetime(2026, 10, 8, 14, 0, tzinfo=tehran)
        previous = SimpleNamespace(
            id=9,
            title="جلسه قبلی",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
        )
        callback = SimpleNamespace(
            data="meeting:move:9",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(answer=AsyncMock()),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_state=AsyncMock(return_value=bot_module.NewMeeting.confirm.state),
            get_data=AsyncMock(return_value={
                "meeting_owner_id": 42,
                "meeting_title": "جلسه جدید",
                "starts_at": starts_at.isoformat(),
                "ends_at": (starts_at + timedelta(hours=1)).isoformat(),
            }),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        fake_db = SimpleNamespace(meeting=AsyncMock(return_value=previous))

        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)):
            await bot_module.ask_move_meeting(callback, state)

        markup = callback.message.answer.await_args.kwargs["reply_markup"]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("move_choice:confirm_delete_old", callbacks)
        self.assertIn("move_choice:back", callbacks)

    async def test_deleting_previous_conflict_saves_new_meeting(self):
        tehran = ZoneInfo("Asia/Tehran")
        starts_at = datetime(2026, 10, 8, 14, 0, tzinfo=tehran)
        previous = SimpleNamespace(id=9, title="جلسه قبلی")
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={
                "meeting_owner_id": 42,
                "meeting_title": "جلسه جدید",
                "starts_at": starts_at.isoformat(),
                "ends_at": (starts_at + timedelta(hours=1)).isoformat(),
                "move_target": "9",
                "move_resume_new": True,
            }),
            clear=AsyncMock(),
        )
        fake_db = SimpleNamespace(
            meeting=AsyncMock(return_value=previous),
            delete_meeting=AsyncMock(return_value=True),
            add_meeting=AsyncMock(return_value=77),
        )

        with patch.object(bot_module, "db", fake_db, create=True):
            await bot_module.delete_old_and_save_new_meeting(callback, state)

        fake_db.delete_meeting.assert_awaited_once_with(9, 42)
        fake_db.add_meeting.assert_awaited_once_with(
            42, "جلسه جدید", starts_at, starts_at + timedelta(hours=1), "",
        )
        state.clear.assert_awaited_once_with()
        self.assertIn("حذف شد", callback.message.edit_text.await_args.args[0])

    async def test_cancel_new_conflict_does_not_change_previous_meeting(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(clear=AsyncMock())

        await bot_module.cancel_conflicting_new_meeting(callback, state)

        state.clear.assert_awaited_once_with()
        self.assertIn("جلسه قبلی بدون تغییر", callback.message.edit_text.await_args.args[0])

    async def test_moved_new_meeting_can_still_be_confirmed_in_quiet_range(self):
        tehran = ZoneInfo("Asia/Tehran")
        new_start = datetime(2026, 9, 14, 14, 0, tzinfo=tehran)
        message = SimpleNamespace(
            text="امروز ساعت ۱۴",
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={
                "meeting_owner_id": 42,
                "meeting_title": "جلسه آزمایشی",
                "move_target": "new",
                "move_duration": 3600,
            }),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
            clear=AsyncMock(),
        )
        confirmation_markup = object()

        with patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "parse_datetime", return_value=new_start), \
             patch.object(bot_module, "schedule_warnings", AsyncMock(return_value=([], [(840, 900)]))), \
             patch.object(
                 bot_module,
                 "meeting_confirmation_view",
                 AsyncMock(return_value=("امکان ثبت با وجود ممنوعیت", confirmation_markup)),
             ):
            await bot_module.save_moved_meeting(message, state)

        state.update_data.assert_awaited_once_with(
            starts_at=new_start.isoformat(),
            ends_at=(new_start + timedelta(hours=1)).isoformat(),
        )
        state.set_state.assert_awaited_once_with(bot_module.NewMeeting.confirm)
        message.answer.assert_awaited_once_with(
            "امکان ثبت با وجود ممنوعیت", reply_markup=confirmation_markup,
        )

    async def test_existing_meeting_move_warning_offers_override_button(self):
        tehran = ZoneInfo("Asia/Tehran")
        new_start = datetime(2026, 9, 29, 14, 0, tzinfo=tehran)
        message = SimpleNamespace(
            text="امروز ساعت ۱۴",
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={
                "meeting_owner_id": 42,
                "move_target": "7",
                "move_duration": 3600,
            }),
            update_data=AsyncMock(),
            clear=AsyncMock(),
        )
        conflict = SimpleNamespace(
            id=8,
            title="جلسه قبلی",
            starts_at=new_start,
            ends_at=new_start + timedelta(hours=1),
        )

        with patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "parse_datetime", return_value=new_start), \
             patch.object(bot_module, "schedule_warnings", AsyncMock(return_value=([conflict], []))):
            await bot_module.save_moved_meeting(message, state)

        state.update_data.assert_awaited_once_with(
            move_pending_start=new_start.isoformat(),
            move_pending_end=(new_start + timedelta(hours=1)).isoformat(),
        )
        markup = message.answer.await_args.kwargs["reply_markup"]
        buttons = [button for row in markup.inline_keyboard for button in row]
        self.assertIn("⚠️ جابه‌جایی با وجود تداخل", [button.text for button in buttons])
        self.assertIn("move_existing:confirm", [button.callback_data for button in buttons])

    async def test_existing_meeting_move_override_applies_pending_time(self):
        tehran = ZoneInfo("Asia/Tehran")
        new_start = datetime(2026, 9, 29, 14, 0, tzinfo=tehran)
        new_end = new_start + timedelta(hours=1)
        meeting = SimpleNamespace(id=7, title="دانش بنیان")
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={
                "meeting_owner_id": 42,
                "move_target": "7",
                "move_pending_start": new_start.isoformat(),
                "move_pending_end": new_end.isoformat(),
            }),
            clear=AsyncMock(),
        )
        fake_db = SimpleNamespace(
            meeting=AsyncMock(return_value=meeting),
            move_meeting=AsyncMock(return_value=True),
        )

        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True):
            await bot_module.confirm_existing_meeting_move(callback, state)

        fake_db.move_meeting.assert_awaited_once_with(7, 42, new_start, new_end)
        state.clear.assert_awaited_once_with()
        callback.answer.assert_awaited_once_with("جلسه جابه‌جا شد.")

    async def test_today_range_covers_exactly_the_current_local_calendar_day(self):
        tehran = ZoneInfo("Asia/Tehran")
        now = datetime(2026, 9, 14, 13, 30, tzinfo=tehran)
        callback = SimpleNamespace(
            data="meetings:range:today",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        fake_db = SimpleNamespace(meetings_in_range=AsyncMock(return_value=[]))
        fake_settings = SimpleNamespace(timezone=tehran)

        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", fake_settings, create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            await bot_module.list_meetings_in_range(callback)

        _, start, end = fake_db.meetings_in_range.await_args.args[:3]
        expected_start = datetime(2026, 9, 14, 0, 0, tzinfo=tehran)
        self.assertEqual(start, expected_start)
        self.assertEqual(end, expected_start + timedelta(days=1))
        self.assertIn("امروز", callback.message.edit_text.await_args.args[0])

    async def test_tomorrow_range_covers_exactly_the_next_local_calendar_day(self):
        tehran = ZoneInfo("Asia/Tehran")
        now = datetime(2026, 9, 13, 23, 58, tzinfo=tehran)
        callback = SimpleNamespace(
            data="meetings:range:tomorrow",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        fake_db = SimpleNamespace(meetings_in_range=AsyncMock(return_value=[]))
        fake_settings = SimpleNamespace(timezone=tehran)

        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", fake_settings, create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)), \
             patch.object(bot_module, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            await bot_module.list_meetings_in_range(callback)

        _, start, end = fake_db.meetings_in_range.await_args.args[:3]
        expected_start = datetime(2026, 9, 14, 0, 0, tzinfo=tehran)
        self.assertEqual(start, expected_start)
        self.assertEqual(end, expected_start + timedelta(days=1))
        self.assertEqual(fake_db.meetings_in_range.await_args.kwargs["limit"], 100)
        callback.message.edit_text.assert_awaited_once()
        self.assertIn("فردا", callback.message.edit_text.await_args.args[0])

    async def test_repeated_empty_range_click_is_acknowledged_without_error(self):
        tehran = ZoneInfo("Asia/Tehran")
        callback = SimpleNamespace(
            data="meetings:range:tomorrow",
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(
                edit_text=AsyncMock(side_effect=TelegramBadRequest(
                    method=object(), message="Bad Request: message is not modified"
                ))
            ),
            answer=AsyncMock(),
        )
        fake_db = SimpleNamespace(meetings_in_range=AsyncMock(return_value=[]))
        with patch.object(bot_module, "db", fake_db, create=True), \
             patch.object(bot_module, "settings", SimpleNamespace(timezone=tehran), create=True), \
             patch.object(bot_module, "reject_if_needed", AsyncMock(return_value=False)):
            await bot_module.list_meetings_in_range(callback)

        callback.answer.assert_awaited_once_with("این بازه همین حالا نمایش داده شده است.")
