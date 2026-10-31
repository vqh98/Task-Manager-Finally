import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import jdatetime

from app.db import Database
from app.recurrence import RecurrenceRule, next_occurrence, occurrences_until, parse_recurrence_text, parse_temporary_weekday_override


TZ=ZoneInfo("Asia/Tehran")


class RecurrenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=Database(Path(self.tmp.name)/"test.db"); await self.db.init()

    async def asyncTearDown(self): self.tmp.cleanup()

    def test_temporary_weekday_override_becomes_three_series(self):
        now = datetime(2026, 10, 18, 12, tzinfo=TZ)
        text = ("جلسات DBA دوشنبه و چهارشنبه ساعت 17 تا 18 است؛ "
                "تایم دوشنبه از این هفته میشه 15 تا 17 تا شش هفته آینده")
        parts = parse_temporary_weekday_override(text, now)
        self.assertIsNotNone(parts)
        self.assertEqual(len(parts), 3)
        unchanged, temporary, resumed = parts
        self.assertEqual(unchanged["recurrence"]["weekdays"], [2])
        self.assertEqual(datetime.fromisoformat(unchanged["starts_at"]).hour, 17)
        self.assertEqual(temporary["recurrence"]["weekdays"], [0])
        self.assertEqual(temporary["occurrences"], 6)
        self.assertEqual((datetime.fromisoformat(temporary["starts_at"]).hour,
                          datetime.fromisoformat(temporary["ends_at"]).hour), (15, 17))
        self.assertEqual(datetime.fromisoformat(resumed["starts_at"]).hour, 17)
        self.assertEqual(datetime.fromisoformat(resumed["starts_at"]).date(),
                         (datetime.fromisoformat(temporary["starts_at"]) + timedelta(weeks=6)).date())

    def test_daily_schedule_does_not_drift(self):
        anchor=datetime(2026,8,10,12,tzinfo=TZ)
        self.assertEqual(next_occurrence(anchor,anchor,RecurrenceRule("daily",2)),anchor+timedelta(days=2))

    def test_biweekly_selected_weekdays(self):
        anchor=datetime(2026,8,15,10,tzinfo=TZ) # Saturday
        values=occurrences_until(anchor,anchor,RecurrenceRule("weekly",2,(5,0)),anchor+timedelta(days=35))
        self.assertEqual([x.weekday() for x in values[:4]],[0,5,0,5])
        saturdays=[x for x in values if x.weekday()==5]
        self.assertEqual((saturdays[1]-saturdays[0]).days,14)

    def test_jalali_monthly_keeps_persian_day(self):
        g=jdatetime.datetime(1405,6,31,10,0).togregorian().replace(tzinfo=TZ)
        nxt=next_occurrence(g,g,RecurrenceRule("monthly_jalali",1,(),31))
        j=jdatetime.datetime.fromgregorian(datetime=nxt)
        self.assertEqual((j.month,j.day),(7,30))

    def test_end_date_and_natural_parser(self):
        anchor=datetime(2026,8,15,10,tzinfo=TZ)
        config=parse_recurrence_text("هر 2 هفته شنبه و دوشنبه بدون پایان",anchor,TZ)
        self.assertEqual(config["frequency"],"weekly"); self.assertEqual(config["interval"],2)
        self.assertEqual(config["weekdays"],[0,5])
        self.assertIsNone(next_occurrence(anchor,anchor,RecurrenceRule("daily",1,(),None,anchor.date())))

    def test_bare_multiple_weekdays_are_weekly_recurrence(self):
        anchor=datetime(2026,8,15,10,tzinfo=TZ)
        text="\u06cc\u06a9\u0634\u0646\u0628\u0647\u200c\u0647\u0627 \u0648 \u0633\u0647\u200c\u0634\u0646\u0628\u0647\u200c\u0647\u0627"
        config=parse_recurrence_text(text, anchor, TZ)
        self.assertEqual(config["frequency"], "weekly")
        self.assertEqual(config["weekdays"], [1, 6])

    async def test_materialization_is_idempotent_and_skip_does_not_stop_series(self):
        anchor=datetime(2026,8,15,10,tzinfo=TZ)
        sid,first=await self.db.create_recurrence_series(1,"پیگیری",anchor,2,"خودم","",1,None,"daily")
        self.assertEqual(await self.db.materialize_recurrences(anchor+timedelta(days=3)),3)
        self.assertEqual(await self.db.materialize_recurrences(anchor+timedelta(days=3)),0)
        await self.db.skip_occurrence(first,1)
        tasks=await self.db.tasks(1,include_done=True,limit=10)
        self.assertEqual(len(tasks),4); self.assertIn("skipped",{t.status for t in tasks})
        self.assertTrue(await self.db.stop_recurrence(sid,1))


if __name__ == "__main__": unittest.main()
