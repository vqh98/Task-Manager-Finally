import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.charts import member_performance_card, progress_chart, weekly_management_card
from app.models import Priority, Task


class ChartTests(unittest.TestCase):
    def test_member_performance_card_is_png(self):
        stats = {
            "total": 12, "open": 5, "paused": 1, "suspended": 1, "done": 5,
            "overdue": 2, "reports": 18, "avg_progress": 47,
            "early_rate": 40, "on_time_rate": 40, "late_rate": 20,
            "average_timing_percent": 12,
        }
        image = member_performance_card("رضا پرستار", stats)
        self.assertTrue(image.startswith(b"\x89PNG"))
        self.assertGreater(len(image), 30_000)

    def test_progress_chart_is_png(self):
        tz = ZoneInfo("Asia/Tehran")
        start = datetime(2026, 8, 1, 10, tzinfo=tz)
        task = Task(1, 1, "Test Task", start + timedelta(days=10), Priority.IMPORTANT,
                    "Member", 60, "", "open", start, 9, None)
        history = [(start, 0, 1), (start + timedelta(days=3), 30, 9),
                   (start + timedelta(days=6), 60, 9)]
        image = progress_chart(task, history, start + timedelta(days=7))
        self.assertTrue(image.startswith(b"\x89PNG"))
        self.assertGreater(len(image), 10_000)

    def test_weekly_management_card_is_png(self):
        image=weekly_management_card({"completed":5,"created":7,"overdue":1,"reports":9},
            {"completed":3,"created":4,"overdue":2,"reports":6},4,3,
            [("تسک پرریسک",45)],[("علی",70)])
        self.assertTrue(image.startswith(b"\x89PNG"))
        self.assertGreater(len(image),20_000)


if __name__ == "__main__":
    unittest.main()
