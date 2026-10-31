import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db import Database


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        await self.db.init()
        await self.db.seed_owners(frozenset({1}))

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_task_flow_and_independent_reminder(self):
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        task_id = await self.db.add_task(
            1, "تست", now + timedelta(hours=47), 1, "مسئول", "توضیح"
        )
        self.assertEqual(len(await self.db.tasks(1)), 1)
        self.assertEqual(await self.db.reminders(1), [])
        reminder_id = await self.db.add_reminder(1, "یادآور مستقل", now)
        self.assertEqual([r.id for r in await self.db.due_reminders(now)], [reminder_id])
        await self.db.update_task(task_id, 1, "progress", 50)
        self.assertEqual((await self.db.task(task_id, 1)).progress, 50)

    async def test_reminder_time_range_filters(self):
        now = datetime(2026, 9, 10, 10, 0, tzinfo=ZoneInfo("Asia/Tehran"))
        await self.db.add_reminder(1, "سه روز", now + timedelta(days=3))
        await self.db.add_reminder(1, "هفت روز", now + timedelta(days=7))
        await self.db.add_reminder(1, "دورتر", now + timedelta(days=20))

        filtered = await self.db.reminders(1, now=now, within_days=7)
        self.assertEqual([reminder.title for reminder in filtered], ["سه روز", "هفت روز"])
        all_reminders = await self.db.reminders(1, now=now)
        self.assertEqual(len(all_reminders), 3)

    async def test_configurable_reminder_warning_is_once_per_due_time(self):
        tz = ZoneInfo("Asia/Tehran")
        now = datetime(2026, 9, 10, 8, 0, tzinfo=tz)
        reminder_id = await self.db.add_reminder(
            1, "یادآوری آزمایشی", now + timedelta(days=1, hours=4)
        )
        await self.db.update_user_setting(1, "reminder_warning_enabled", 1)
        await self.db.update_user_setting(1, "reminder_warning_hour", 9)
        await self.db.update_user_setting(1, "reminder_warning_minute", 30)

        before = now.replace(hour=9, minute=29)
        warning_time = now.replace(hour=9, minute=30)
        self.assertEqual(await self.db.due_reminder_warnings(before, 9, 0), [])
        warnings = await self.db.due_reminder_warnings(warning_time, 9, 0)
        self.assertEqual([reminder.id for reminder in warnings], [reminder_id])
        await self.db.mark_reminder_warning_sent(reminder_id, warnings[0].due_at, warning_time)
        self.assertEqual(await self.db.due_reminder_warnings(warning_time, 9, 0), [])

    async def test_multiple_task_deadline_warnings_stop_after_completion(self):
        now = datetime.now(ZoneInfo("Asia/Tehran")).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        task_id = await self.db.add_task(
            1, "تسک هشدار", now + timedelta(days=7), 1, "مسئول", ""
        )
        await self.db.update_warning_days(1, "deadline", [7, 1, 0])
        self.assertEqual(
            [(task.id, days) for task, days in
             await self.db.due_task_deadline_alerts(now, 2)],
            [(task_id, 7)],
        )
        await self.db.mark_task_deadline_alert(task_id, 7, now)
        self.assertEqual(await self.db.due_task_deadline_alerts(now, 2), [])
        await self.db.update_task(task_id, 1, "status", "done")
        deadline_day = now + timedelta(days=7)
        self.assertEqual(
            await self.db.due_task_deadline_alerts(deadline_day, 2), []
        )

    async def test_owner_can_manage_access_and_data_is_isolated(self):
        await self.db.seed_owners(frozenset({1}))
        self.assertEqual(await self.db.user_role(1), "owner")
        self.assertTrue(await self.db.add_manager(2))
        self.assertEqual(await self.db.user_role(2), "manager")

        now = datetime.now(ZoneInfo("Asia/Tehran"))
        await self.db.add_task(1, "تسک مالک", now + timedelta(days=3), 1, "الف", "")
        await self.db.add_task(2, "تسک مدیر", now + timedelta(days=3), 2, "ب", "")
        self.assertEqual([task.title for task in await self.db.tasks(1)], ["تسک مالک"])
        self.assertEqual([task.title for task in await self.db.tasks(2)], ["تسک مدیر"])

        self.assertTrue(await self.db.remove_manager(2))
        self.assertIsNone(await self.db.user_role(2))

    async def test_meeting_overlap_and_move(self):
        now = datetime.now(ZoneInfo("Asia/Tehran")) + timedelta(days=1)
        meeting_id = await self.db.add_meeting(1, "جلسه", now, now + timedelta(hours=1), "")
        overlaps = await self.db.overlapping_meetings(
            1, now + timedelta(minutes=30), now + timedelta(hours=2)
        )
        self.assertEqual([meeting.id for meeting in overlaps], [meeting_id])
        moved = now + timedelta(days=1)
        self.assertTrue(await self.db.move_meeting(meeting_id, 1, moved, moved + timedelta(hours=1)))
        self.assertEqual((await self.db.meeting(meeting_id, 1)).starts_at, moved)

    async def test_recurring_meeting_multiple_weekdays_and_disable(self):
        tz = ZoneInfo("Asia/Tehran")
        # Keep the anchor in the future so this test is independent of the
        # machine's current date; disabling a series preserves past events
        # and removes only occurrences after the disable time.
        days_until_monday = (0 - datetime.now(tz).weekday()) % 7 or 7
        start = (datetime.now(tz) + timedelta(days=days_until_monday)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(hours=1)
        series_id, meeting_id = await self.db.create_meeting_recurrence(
            1, "جلسه هفتگی", start, end, "",
            {"frequency": "weekly", "interval": 1, "weekdays": [0, 2]},
        )
        events = await self.db.upcoming_meetings(1, start - timedelta(minutes=1), 10)
        # Lists expose one representative occurrence per recurring series,
        # while the underlying calendar still contains all materialized dates.
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].starts_at.weekday(), 0)
        materialized = await self.db.meetings_between(
            1, start - timedelta(minutes=1), start + timedelta(days=35)
        )
        self.assertGreaterEqual(len(materialized), 5)
        self.assertEqual((await self.db.meeting_recurrence(meeting_id, 1))["series_id"], series_id)
        self.assertTrue(await self.db.set_meeting_recurrence_active(series_id, 1, False))
        future = await self.db.upcoming_meetings(1, start, 10)
        self.assertTrue(all(event.starts_at <= start for event in future))

    async def test_recurring_series_title_lookup_ignores_meeting_prefix(self):
        tz = ZoneInfo("Asia/Tehran")
        start = (datetime.now(tz) + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
        series_id, _ = await self.db.create_meeting_recurrence(
            1, "DBA", start, start + timedelta(hours=1), "",
            {"frequency": "weekly", "interval": 1, "weekdays": [0, 2]},
        )
        self.assertEqual(
            await self.db.active_meeting_recurrence_ids_by_title(1, "جلسات DBA"),
            [series_id],
        )

    async def test_meetings_in_range_deduplicates_recurring_series(self):
        tz = ZoneInfo("Asia/Tehran")
        start = (datetime.now(tz) + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        await self.db.create_meeting_recurrence(
            1, "جلسه بازه‌ای", start, start + timedelta(hours=1), "",
            {"frequency": "daily", "interval": 1, "weekdays": []},
        )
        events = await self.db.meetings_in_range(
            1, start - timedelta(minutes=1), start + timedelta(days=10)
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "جلسه بازه‌ای")

    async def test_event_sharing_permissions_are_event_only(self):
        now = datetime.now(ZoneInfo("Asia/Tehran")) + timedelta(days=1)
        await self.db.add_team_member(1, 99, "نیروی تقویم")
        self.assertEqual(await self.db.user_display_name(1), "")
        self.assertTrue(await self.db.set_user_display_name(1, "  رضا   احمدی  "))
        self.assertEqual(await self.db.user_display_name(1), "رضا احمدی")
        self.assertTrue(await self.db.set_event_share(1, 99, "view"))
        self.assertEqual(await self.db.event_share_permission(1, 99), "view")
        self.assertEqual(await self.db.shared_event_managers(99), [(1, "رضا احمدی", "view")])

        meeting_id = await self.db.add_meeting(1, "ایونت مدیر", now, now + timedelta(hours=1), "")
        self.assertEqual([event.id for event in await self.db.upcoming_meetings(1, now - timedelta(hours=1))], [meeting_id])
        self.assertEqual(await self.db.tasks(99), [])

        self.assertTrue(await self.db.set_event_share(1, 99, "edit"))
        self.assertEqual(await self.db.event_share_permission(1, 99), "edit")
        self.assertTrue(await self.db.delete_meeting(meeting_id, 1))
        self.assertTrue(await self.db.remove_team_member(1, 99))
        self.assertIsNone(await self.db.event_share_permission(1, 99))

    async def test_per_user_meeting_notification_schedule_and_toggle(self):
        defaults = {
            "digest_hour": 22, "digest_minute": 0,
            "morning_hour": 9, "morning_minute": 0,
            "digest_enabled": 1, "morning_enabled": 1,
            "deadline_warning_days": 2, "report_warning_hours": 48,
            "meeting_default_duration": 60,
        }
        self.assertEqual(await self.db.users_for_schedule("digest", 22, 0, 22, 0), [1])
        self.assertEqual(await self.db.users_for_schedule("digest", 9, 0, 22, 0), [])

        await self.db.update_user_setting(1, "digest_hour", 21)
        await self.db.update_user_setting(1, "digest_minute", 30)
        config = await self.db.user_settings(1, defaults)
        self.assertEqual((config["digest_hour"], config["digest_minute"]), (21, 30))
        self.assertEqual(config["meeting_default_duration"], 60)
        await self.db.update_user_setting(1, "meeting_default_duration", 15)
        self.assertEqual(
            (await self.db.user_settings(1, defaults))["meeting_default_duration"], 15
        )
        self.assertEqual(await self.db.users_for_schedule("digest", 21, 30, 22, 0), [1])
        self.assertEqual(await self.db.users_for_schedule("digest", 22, 0, 22, 0), [])

        await self.db.update_user_setting(1, "digest_enabled", 0)
        self.assertEqual(await self.db.users_for_schedule("digest", 21, 30, 22, 0), [])
        today = datetime.now(ZoneInfo("Asia/Tehran")).date()
        self.assertFalse(await self.db.notification_sent_today(1, "digest", today))
        await self.db.mark_notification_sent(1, "digest", today)
        self.assertTrue(await self.db.notification_sent_today(1, "digest", today))

    async def test_meeting_suggestion_window_defaults_and_atomic_update(self):
        defaults = {"meeting_window_start": 480, "meeting_window_end": 1140}
        config = await self.db.user_settings(1, defaults)
        self.assertEqual(
            (config["meeting_window_start"], config["meeting_window_end"]),
            (480, 1140),
        )

        await self.db.update_meeting_window(1, 11 * 60, 19 * 60)
        config = await self.db.user_settings(1, defaults)
        self.assertEqual(
            (config["meeting_window_start"], config["meeting_window_end"]),
            (660, 1140),
        )

        for invalid in ((1140, 660), (661, 1140), (0, 1500)):
            with self.assertRaises(ValueError):
                await self.db.update_meeting_window(1, *invalid)

        config = await self.db.user_settings(1, defaults)
        self.assertEqual(
            (config["meeting_window_start"], config["meeting_window_end"]),
            (660, 1140),
        )

    async def test_event_share_requires_active_team_member(self):
        self.assertFalse(await self.db.set_event_share(1, 404, "view"))
        self.assertEqual(await self.db.shared_event_managers(404), [])
    async def test_management_query_filters_progress(self):
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        low = await self.db.add_task(1, "پیشرفت کم", now + timedelta(days=2), 1, "علی", "")
        high = await self.db.add_task(1, "پیشرفت زیاد", now + timedelta(days=3), 2, "رضا", "")
        await self.db.update_task(low, 1, "progress", 35)
        await self.db.update_task(high, 1, "progress", 70)
        results = await self.db.query_tasks(1, {"progress_lt": 50}, "progress_asc", 20, now)
        self.assertEqual([task.title for task in results], ["پیشرفت کم"])

    async def test_task_list_priority_and_deadline_filters(self):
        now = datetime.now(ZoneInfo("Asia/Tehran")).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        urgent = await self.db.add_task(1, "فوری", now + timedelta(days=3), 1, "علی", "")
        await self.db.add_task(1, "مهم دور", now + timedelta(days=20), 2, "رضا", "")
        await self.db.add_task(1, "بدون مسئول", now + timedelta(days=2), 1, "", "")

        priority_tasks = await self.db.query_tasks(
            1, {"priority": 1}, sort="priority_asc", now=now,
            include_unassigned=False,
        )
        self.assertEqual([task.id for task in priority_tasks], [urgent])
        deadline_tasks = await self.db.query_tasks(
            1, {"due_within_days": 7}, sort="priority_asc", now=now,
            include_unassigned=False,
        )
        self.assertEqual([task.title for task in deadline_tasks], ["فوری"])

    async def test_task_list_progress_filters_and_completed_status(self):
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        zero = await self.db.add_task(1, "صفر", now + timedelta(days=2), 1, "علی", "")
        under_30 = await self.db.add_task(1, "زیر سی", now + timedelta(days=3), 1, "علی", "")
        between = await self.db.add_task(1, "بین هشتاد و نودونه", now + timedelta(days=4), 1, "علی", "")
        done = await self.db.add_task(1, "تکمیل", now + timedelta(days=5), 1, "علی", "")
        await self.db.update_task(under_30, 1, "progress", 20)
        await self.db.update_task(between, 1, "progress", 85)
        await self.db.update_task(done, 1, "status", "done")

        async def ids(filters):
            return [task.id for task in await self.db.query_tasks(
                1, filters, now=now, include_unassigned=False,
                include_done=filters.get("status") == "done",
            )]

        self.assertEqual(await ids({"progress_eq": 0}), [zero])
        self.assertEqual(await ids({"progress_lt": 30}), [zero, under_30])
        self.assertEqual(await ids({"progress_gte": 80, "progress_lt": 100}), [between])
        self.assertEqual(await ids({"status": "done"}), [done])

    async def test_management_query_filters_tasks_unchanged_for_days(self):
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        task_id = await self.db.add_task(
            1, "بدون تغییر اخیر", now + timedelta(days=20), 1, "علی", ""
        )
        before_cutoff = await self.db.query_tasks(
            1, {"unchanged_days": 7}, now=now + timedelta(days=6)
        )
        after_cutoff = await self.db.query_tasks(
            1, {"unchanged_days": 7}, now=now + timedelta(days=8)
        )
        self.assertNotIn(task_id, [task.id for task in before_cutoff])
        self.assertIn(task_id, [task.id for task in after_cutoff])
    async def test_access_code_is_single_use_and_grants_free_access(self):
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        self.assertFalse(await self.db.is_authorized(22, now))
        await self.db.create_access_code("WELCOME-2026")
        self.assertEqual(await self.db.access_codes(), [("WELCOME-2026", True)])
        self.assertTrue(await self.db.redeem_access_code("welcome-2026", 22, "کاربر"))
        self.assertFalse(await self.db.redeem_access_code("WELCOME-2026", 23))
        self.assertTrue(await self.db.is_authorized(22, now))
        self.assertEqual(await self.db.access_codes(), [("WELCOME-2026", False)])

    async def test_access_code_can_be_deactivated(self):
        await self.db.create_access_code("TEMP")
        await self.db.deactivate_access_code("temp")
        self.assertFalse(await self.db.redeem_access_code("TEMP", 50))

    async def test_access_code_can_be_multi_use_and_tracks_unique_users(self):
        await self.db.create_access_code("TEAM-2026", "multi", 2)
        self.assertTrue(await self.db.redeem_access_code("TEAM-2026", 50, "نفر اول"))
        self.assertTrue(await self.db.redeem_access_code("team-2026", 51, "نفر دوم"))
        self.assertTrue(await self.db.redeem_access_code("TEAM-2026", 50, "نام به‌روز"))
        details = await self.db.access_code_details()
        self.assertEqual(details, [("TEAM-2026", False, "multi", 2, [50, 51])])
        self.assertFalse(await self.db.redeem_access_code("TEAM-2026", 52))
        self.assertEqual(await self.db.extend_access_code_capacity("TEAM-2026", 2), 4)
        self.assertTrue(await self.db.redeem_access_code("TEAM-2026", 52))
        self.assertTrue(await self.db.redeem_access_code("TEAM-2026", 53))
        self.assertEqual(
            await self.db.access_code_details(),
            [("TEAM-2026", False, "multi", 4, [50, 51, 52, 53])],
        )
        self.assertFalse(await self.db.redeem_access_code("TEAM-2026", 54))

    async def test_access_code_user_is_independent_not_an_employee(self):
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        await self.db.create_access_code("INDEPENDENT-ACCESS")
        self.assertTrue(await self.db.redeem_access_code("INDEPENDENT-ACCESS", 50, "کاربر مستقل"))
        self.assertTrue(await self.db.is_authorized(50, now))
        self.assertEqual(await self.db.user_role(50), "manager")
        self.assertEqual(await self.db.member_managers(50), [])
        self.assertEqual(await self.db.team_members(1), [])

    async def test_dashboard_metrics(self):
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        first = await self.db.add_task(1, "فوری", now + timedelta(days=2), 1, "الف", "")
        second = await self.db.add_task(1, "عقب‌افتاده", now - timedelta(days=1), 2, "ب", "")
        await self.db.update_task(first, 1, "progress", 50)
        await self.db.update_task(second, 1, "status", "done")
        stats = await self.db.dashboard_stats(1, now)
        self.assertEqual(stats["open"], 1)
        self.assertEqual(stats["done"], 1)
        self.assertEqual(stats["due_week"], 1)
        self.assertEqual(stats["completion_rate"], 50)
        self.assertEqual(stats["priorities"][1], 1)

    async def test_team_assignment_and_reporting(self):
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        await self.db.add_team_member(1, 99, "نیروی تست")
        await self.db.set_user_display_name(1, "مدیر تست")
        self.assertEqual(await self.db.team_members(1), [(99, "نیروی تست")])
        task_id = await self.db.add_task(
            1, "پیگیری قرارداد", now + timedelta(days=4), 1, "نیروی تست", "", 99
        )
        assigned = await self.db.assigned_tasks(99)
        self.assertEqual([task.id for task in assigned], [task_id])
        self.assertEqual(assigned[0].last_changed_by, 1)
        self.assertEqual(assigned[0].last_changed_by_name, "مدیر تست")
        report_id = await self.db.add_task_report(task_id, 1, 99, 65, "مذاکره انجام شد")
        self.assertTrue(report_id)
        task = await self.db.task(task_id, 1)
        self.assertEqual((task.progress, task.last_note), (65, "مذاکره انجام شد"))
        self.assertEqual(task.last_changed_by, 99)
        self.assertEqual(task.last_changed_by_name, "نیروی تست")
        self.assertIsNotNone(task.updated_at)
        reports = await self.db.task_reports(task_id, 1)
        self.assertEqual((reports[0][0], reports[0][1], reports[0][2]), (99, 65, "مذاکره انجام شد"))
        self.assertTrue(await self.db.remove_team_member(1, 99))

    async def test_username_resolution_and_task_copy(self):
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        await self.db.remember_telegram_account(99, "milad_test", "میلاد")
        self.assertEqual(await self.db.telegram_account_by_username("@MILAD_TEST"), (99, "میلاد"))
        task_id = await self.db.add_task(
            1, "تسک با رونوشت", now + timedelta(days=2), 2, "مسئول دستی", "", None, 99
        )
        task = await self.db.task(task_id, 1)
        self.assertEqual(task.cc_manager_id, 99)
        self.assertIsNone(task.assignee_user_id)
        self.assertTrue(await self.db.update_task(task_id, 1, "cc_manager_id", None))
        self.assertIsNone((await self.db.task(task_id, 1)).cc_manager_id)
        self.assertTrue(await self.db.update_task(task_id, 1, "cc_manager_id", 99))
        self.assertEqual((await self.db.task(task_id, 1)).cc_manager_id, 99)

    async def test_progress_history_records_manager_and_member_changes(self):
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        task_id = await self.db.add_task(
            1, "تاریخچه پیشرفت", now + timedelta(days=5), 1, "نیرو", "", 99
        )
        await self.db.update_task(task_id, 1, "progress", 20)
        await self.db.add_task_report(task_id, 1, 99, 65, "گزارش نیرو")

        history = await self.db.task_progress_history(task_id, 1)
        self.assertEqual([point[1] for point in history], [0, 20, 65])
        self.assertEqual([point[2] for point in history], [1, 1, 99])
        self.assertTrue(all(point[0].tzinfo is not None for point in history))

    async def test_member_performance_summary(self):
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        await self.db.add_team_member(1, 99, "نیرو")
        early_task = await self.db.add_task(
            1, "تحویل زودهنگام", now + timedelta(days=10), 1, "نیرو", "", 99
        )
        late_task = await self.db.add_task(
            1, "تحویل دیرهنگام", now - timedelta(days=2), 1, "نیرو", "", 99
        )
        open_task = await self.db.add_task(
            1, "در جریان", now + timedelta(days=3), 1, "نیرو", "", 99
        )
        await self.db.update_task(early_task, 1, "progress", 100)
        await self.db.update_task(early_task, 1, "status", "done")
        await self.db.update_task(late_task, 1, "progress", 100)
        await self.db.update_task(late_task, 1, "status", "done")
        await self.db.update_task(open_task, 1, "progress", 40)

        stats = await self.db.member_performance(1, 99, now)
        self.assertEqual((stats["total"], stats["open"], stats["done"]), (3, 1, 2))
        self.assertEqual((stats["early"], stats["late"]), (1, 1))
        self.assertEqual(stats["avg_progress"], 40)


if __name__ == "__main__":
    unittest.main()
