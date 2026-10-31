import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import app.bot as bot_module
from app.charts import progress_chart
from app.config import Settings
from app.models import Priority, Task
from app.output_formatting import RTL_MARK, localize_visible_digits, rtl_line


class FormattingTests(unittest.TestCase):
    def setUp(self):
        bot_module.settings = Settings(
            bot_token="test", allowed_user_ids=frozenset({1}),
            timezone=ZoneInfo("Asia/Tehran"), digest_hour=22, digest_minute=0,
            morning_hour=9, morning_minute=0, deadline_warning_days=2,
            report_warning_hours=48, database_path=None, openai_api_key=None,
            openai_base_url="https://api.openai.com/v1", openai_model="gpt-4o-mini",
            openai_whisper_model="whisper-1",
        )

    def test_jalali_date_format(self):
        value = datetime(2026, 10, 5, 10, 30, tzinfo=ZoneInfo("Asia/Tehran"))
        self.assertEqual(bot_module.fmt_date(value), "۱۳ مهر ۱۴۰۵")
        self.assertEqual(bot_module.fmt_dt(value), "۱۳ مهر ۱۴۰۵، ساعت ۱۰:۳۰")

    def test_visible_digits_are_localized_without_corrupting_links(self):
        source = 'جلسه #981 ساعت 10 <a href="https://example.com/a12">لینک 2</a>'
        self.assertEqual(
            localize_visible_digits(source),
            'جلسه #۹۸۱ ساعت ۱۰ <a href="https://example.com/a12">لینک ۲</a>',
        )

    def test_rtl_line_marks_english_meeting_titles(self):
        self.assertEqual(rtl_line("3. MBA"), RTL_MARK + "3. MBA")
        self.assertEqual(rtl_line(RTL_MARK + "MBA"), RTL_MARK + "MBA")

    def test_progress_chart_accepts_legacy_naive_dates(self):
        now = datetime(2026, 8, 13, 20, 0, tzinfo=ZoneInfo("Asia/Tehran"))
        task = Task(
            1, 1, "Legacy task", datetime(2026, 8, 20, 20, 0),
            Priority.IMPORTANT, "Member", 50, "", "open",
            datetime(2026, 8, 10, 20, 0),
        )
        image = progress_chart(task, [(datetime(2026, 8, 10, 20, 0), 0, 1)], now)
        self.assertTrue(image.startswith(b"\x89PNG"))
        self.assertGreater(len(image), 20_000)

    def test_task_text_names_last_editor_relative_to_viewer(self):
        changed_at = datetime(2026, 9, 16, 10, 30, tzinfo=ZoneInfo("Asia/Tehran"))
        task = Task(
            1, 1, "پیگیری قرارداد", datetime(2026, 10, 5, 20, 0, tzinfo=ZoneInfo("Asia/Tehran")),
            Priority.IMPORTANT, "علی", 50, "", "open",
            datetime(2026, 9, 1, 10, 0, tzinfo=ZoneInfo("Asia/Tehran")),
            assignee_user_id=99, updated_at=changed_at, last_changed_by=99,
            last_changed_by_name="علی رضایی",
        )
        manager_view = bot_module.task_text(task, viewer_id=1)
        member_view = bot_module.task_text(task, viewer_id=99)
        self.assertIn("آخرین تغییر: ۲۵ شهریور ۱۴۰۵ توسط علی رضایی", manager_view)
        self.assertIn("آخرین تغییر: ۲۵ شهریور ۱۴۰۵ توسط شما", member_view)


if __name__ == "__main__":
    unittest.main()
