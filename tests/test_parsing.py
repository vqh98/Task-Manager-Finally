import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.parsing import (
    ParsedMeeting, enforce_explicit_calendar_date, enforce_explicit_named_weekday,
    enforce_explicit_relative_day,
    normalize_digits, parse_datetime,
    extract_explicit_meeting_time, extract_meeting_time_ranges, parse_deadline, parse_forwarded_meeting, summarize_meeting_title,
)


TZ = ZoneInfo("Asia/Tehran")


class ParsingTests(unittest.TestCase):
    def test_natural_persian_clock_is_explicit(self):
        cases = {
            "دوشنبه ۸ صبح صبحانه دورهمی کارپ": (8, 0),
            "فردا ۳ عصر جلسه فروش": (15, 0),
            "فردا شب ۹:۳۰ جلسه": (21, 30),
            "جلسه فردا ساعت 11": (11, 0),
            "جلسه در ۱۴۰۵/۰۷/۲۷": None,
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(extract_explicit_meeting_time(source), expected)

    def test_visit_heading_wins_over_conditional_cancellation(self):
        text = (
            "برنامه دیدار با حاج‌آقا مقیمی‌فر دادستان استان\n\n"
            "روز چهارشنبه، بین ساعت 9 تا 11 صبح، از دفتر حاج‌آقا با ما تماس خواهند گرفت.\n"
            "البته در صورتی که برای حاج‌آقا برنامه فوری پیش نیاید، جلسه طبق هماهنگی برگزار می‌شود و در غیر این صورت دیدار لغو خواهد شد.\n"
            "آدرس: خیابان کوهسنگی، بین کوهسنگی 9 و 11، دادگستری استان"
        )
        self.assertEqual(summarize_meeting_title(text), "دیدار با حاج‌آقا مقیمی‌فر دادستان استان")

    def test_normalize_digits(self):
        self.assertEqual(normalize_digits("۱۲۳٤٥"), "12345")

    def test_jalali_datetime(self):
        result = parse_datetime("جلسه ۱۴۰۵/۰۵/۲۴ ساعت ۱۰:۳۰", TZ)
        self.assertIsNotNone(result)
        self.assertEqual((result.hour, result.minute), (10, 30))

    def test_time_range(self):
        now = datetime(2026, 8, 13, 12, tzinfo=TZ)
        result = parse_forwarded_meeting("جلسه فروش\nفردا ساعت 10:30 تا 11:45", TZ, now)
        self.assertIsNotNone(result)
        self.assertEqual(result.title, "فروش")
        self.assertEqual((result.starts_at.hour, result.ends_at.hour, result.ends_at.minute), (10, 11, 45))

    def test_hour_range_variants_are_extracted_deterministically(self):
        variants = (
            "فردا از ساعت ۹ تا ساعت ۱۱",
            "فردا ساعت 9 تا 11",
            "فردا 09:30 الی 11:45",
            "فردا از ۹–۱۱",
        )
        expected = (
            (9, 0, 11, 0),
            (9, 0, 11, 0),
            (9, 30, 11, 45),
            (9, 0, 11, 0),
        )
        for source, value in zip(variants, expected):
            with self.subTest(source=source):
                self.assertEqual(extract_meeting_time_ranges(source), [value])

    def test_multiple_time_ranges_are_extracted_without_including_the_break(self):
        text = "چهارشنبه 22 مهر ماه ساعت 11 الی 13 و 14 الی 17"
        self.assertEqual(
            extract_meeting_time_ranges(text),
            [(11, 0, 13, 0), (14, 0, 17, 0)],
        )
        result = parse_forwarded_meeting(text, TZ, datetime(2026, 10, 10, 12, tzinfo=TZ))
        self.assertIsNotNone(result)
        self.assertEqual((result.starts_at.hour, result.ends_at.hour), (11, 13))

    def test_named_jalali_date(self):
        result = parse_datetime("۲۰ مهر ۱۴۰۵ ساعت ۱۴:۳۰", TZ)
        self.assertIsNotNone(result)
        self.assertEqual((result.year, result.month, result.day), (2026, 10, 12))
        self.assertEqual((result.hour, result.minute), (14, 30))

    def test_named_jalali_date_preserves_explicit_time_range(self):
        result = parse_forwarded_meeting(
            "جلسه معمای پول چهارشنبه ۱۵ مهرماه از ساعت ۱۶:۰۰ الی ۲۰:۰۰",
            TZ,
            datetime(2026, 9, 5, 12, tzinfo=TZ),
        )
        self.assertIsNotNone(result)
        self.assertEqual((result.starts_at.hour, result.starts_at.minute), (16, 0))
        self.assertEqual((result.ends_at.hour, result.ends_at.minute), (20, 0))

    def test_calendar_day_month_without_year_overrides_ai_guess(self):
        now = datetime(2026, 9, 5, 13, 40, tzinfo=TZ)
        ai_result = ParsedMeeting(
            "جلسه", datetime(2026, 9, 5, 12, tzinfo=TZ),
            datetime(2026, 9, 5, 13, tzinfo=TZ),
        )
        result = enforce_explicit_calendar_date(
            ai_result, "جلسه جدید دوشنبه ۱۶ شهریور ماه ساعت ۱۲ تا ۱۳", TZ, now
        )
        self.assertEqual((result.starts_at.month, result.starts_at.day), (9, 7))
        self.assertEqual((result.starts_at.hour, result.ends_at.hour), (12, 13))

    def test_explicit_jalali_date_wins_over_same_named_weekday(self):
        # 12 Mehr 1405 is Sunday. A bare «Sunday» on this Sunday means next
        # week, but the explicitly written calendar date must remain today.
        now = datetime(2026, 10, 4, 11, 16, tzinfo=TZ)
        ai_result = ParsedMeeting(
            "کلوب مدیران اقتصادی",
            datetime(2026, 10, 11, 12, tzinfo=TZ),
            datetime(2026, 10, 11, 13, tzinfo=TZ),
        )
        source = "زمان: یکشنبه، ۱۲ مهرماه، ساعت ۱۲:۰۰"
        weekday_result = enforce_explicit_named_weekday(ai_result, source, TZ, now)
        self.assertEqual(weekday_result, ai_result)
        result = enforce_explicit_calendar_date(weekday_result, source, TZ, now)
        self.assertEqual(result.starts_at, datetime(2026, 10, 4, 12, tzinfo=TZ))
        self.assertEqual(result.ends_at, datetime(2026, 10, 4, 13, tzinfo=TZ))

    def test_parse_datetime_explicit_date_precedes_weekday(self):
        now = datetime(2026, 10, 4, 11, 16, tzinfo=TZ)
        result = parse_datetime("یکشنبه ۱۲ مهرماه ساعت ۱۲:۰۰", TZ, now)
        self.assertEqual(result, datetime(2026, 10, 4, 12, tzinfo=TZ))

    def test_explicit_jalali_meeting_dates_are_not_shifted(self):
        now = datetime(2026, 9, 5, 13, 40, tzinfo=TZ)
        for day, expected in ((11, 2), (12, 3), (13, 4)):
            source = f"جلسه با سعید نعمتی {day} شهریور ۱۴۰۵ ساعت ۱۵"
            ai_result = ParsedMeeting(
                "جلسه", datetime(2026, 9, 1, 15, tzinfo=TZ),
                datetime(2026, 9, 1, 16, tzinfo=TZ),
            )
            result = enforce_explicit_calendar_date(ai_result, source, TZ, now)
            self.assertEqual((result.starts_at.month, result.starts_at.day), (9, expected))
            self.assertEqual((result.starts_at.hour, result.ends_at.hour), (15, 16))

    def test_noisy_mokeb_message_is_summarized(self):
        text = """🟢سلام و عرض ادب، وقت همه مدیران عزیز بخیر

ان‌شاءالله فردا مدیران ارشد، از ساعت ۸:۰۰ الی ۱۱:۰۰ صبح در محل موکب حضور خواهیم داشت تا در خدمت زائرین محترم باشیم و در امر پذیرایی مشارکت کنیم.

التماس دعا"""
        result = parse_forwarded_meeting(text, TZ, datetime(2026, 8, 13, 12, tzinfo=TZ))
        self.assertEqual(result.title, "خدمت‌رسانی در موکب")
        self.assertEqual((result.starts_at.hour, result.ends_at.hour), (8, 11))

    def test_zwnj_in_relative_day_does_not_leak_into_title(self):
        result = parse_forwarded_meeting(
            "جلسه هماهنگی پس‌فردا ساعت ۰۹:۰۵ تا ۱۰:۲۰", TZ,
            datetime(2026, 8, 13, 12, tzinfo=TZ),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.title, "هماهنگی")
        self.assertEqual((result.starts_at.hour, result.starts_at.minute), (9, 5))
        self.assertEqual((result.ends_at.hour, result.ends_at.minute), (10, 20))

    def test_subject_on_same_line_after_relative_schedule_is_retained(self):
        result = parse_forwarded_meeting(
            "سلام عرض ادب\nامروز ساعت ۱۱ جلسه با آقای ملایی", TZ,
            datetime(2026, 8, 13, 12, tzinfo=TZ),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.title, "با آقای ملایی")

    def test_explicit_topic_wins_over_attendees_and_final_instruction(self):
        text = (
            "سلام و عرض ادب\n"
            "شنبه ۱۸ مهر ساعت ۱۶\n"
            "جلسه با جناب هاشمی\n"
            "موضوع: شورای سردبیری\n"
            "افراد حاضر در جلسه: خانم فرخداهی و آقایان ناهید و پرستار\n"
            "لطفاً رأس ساعت در جلسه حضور داشته باشید."
        )
        result = parse_forwarded_meeting(
            text, TZ, datetime(2026, 10, 8, 12, tzinfo=TZ),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.title, "شورای سردبیری")

    def test_meeting_person_wins_over_final_attendance_request(self):
        text = (
            "سلام و عرض ادب\n"
            "یکشنبه ۱۹ مهر ساعت ۱۲\n"
            "جلسه با جناب ملایی\n"
            "افراد حاضر در جلسه: خانم فرخداهی و آقایان ناهید، پرستار، یوسفی\n"
            "لطفاً رأس ساعت در جلسه حضور داشته باشید."
        )
        result = parse_forwarded_meeting(
            text, TZ, datetime(2026, 10, 10, 10, tzinfo=TZ),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.title, "با جناب ملایی")

    def test_own_line_meeting_counterpart_is_extracted_deterministically(self):
        self.assertEqual(
            summarize_meeting_title(
                "جلسه با جناب ملایی\nافراد حاضر در جلسه: خانم فرخداهی و آقای پرستار"
            ),
            "با جناب ملایی",
        )

    def test_relative_and_season_deadlines(self):
        now = datetime(2026, 8, 13, 12, tzinfo=TZ)  # 22 Mordad 1405
        ten_days = parse_deadline("۱۰ روز آینده", TZ, now)
        self.assertEqual((ten_days.date() - now.date()).days, 10)
        self.assertEqual((ten_days.hour, ten_days.minute), (23, 59))
        month_end = parse_deadline("آخر همین ماه", TZ, now)
        month_end_jalali = __import__("jdatetime").datetime.fromgregorian(datetime=month_end)
        self.assertEqual((month_end_jalali.month, month_end_jalali.day), (5, 31))
        autumn_end = parse_deadline("تا آخر پاییز", TZ, now)
        autumn_jalali = __import__("jdatetime").datetime.fromgregorian(datetime=autumn_end)
        self.assertEqual((autumn_jalali.month, autumn_jalali.day), (9, 30))
        year_end = parse_deadline("تا آخر سال", TZ, now)
        year_jalali = __import__("jdatetime").datetime.fromgregorian(datetime=year_end)
        self.assertEqual(year_jalali.month, 12)
        explicit = parse_deadline("۲۰ مهر ۱۴۰۵", TZ, now)
        self.assertEqual((explicit.hour, explicit.minute), (23, 59))

    def test_colloquial_deadlines(self):
        now = datetime(2026, 8, 13, 12, tzinfo=TZ)  # Thursday
        eight_days = parse_deadline("۸ روز دیگه", TZ, now)
        self.assertEqual((eight_days.date() - now.date()).days, 8)
        next_week_end = parse_deadline("آخر هفته دیگه", TZ, now)
        self.assertEqual((next_week_end.date() - now.date()).days, 7)
        next_thursday = parse_deadline("پنج‌شنبه هفته بعد", TZ, now)
        self.assertEqual((next_thursday.date() - now.date()).days, 7)
        autumn = parse_deadline("پایان پاییز", TZ, now)
        autumn_jalali = __import__("jdatetime").datetime.fromgregorian(datetime=autumn)
        self.assertEqual((autumn_jalali.month, autumn_jalali.day), (9, 30))
        aban = parse_deadline("تا آخر آبان", TZ, now)
        aban_jalali = __import__("jdatetime").datetime.fromgregorian(datetime=aban)
        self.assertEqual((aban_jalali.month, aban_jalali.day), (8, 30))

    def test_descriptive_workweek_and_next_weekday(self):
        now = datetime(2026, 8, 16, 12, tzinfo=TZ)  # Sunday
        workweek_end = parse_deadline("آخر هفته آینده", TZ, now)
        self.assertEqual((workweek_end.date() - now.date()).days, 11)
        self.assertEqual(workweek_end.weekday(), 3)

        task_deadline = parse_deadline("این کار برای سه شنبه", TZ, now)
        self.assertEqual((task_deadline.date() - now.date()).days, 2)
        self.assertEqual((task_deadline.hour, task_deadline.minute), (23, 59))

        event = parse_forwarded_meeting("ایونت برنامه ریزی سه‌شنبه ساعت ۱۰", TZ, now)
        self.assertIsNotNone(event)
        self.assertEqual((event.starts_at.date() - now.date()).days, 2)
        self.assertEqual((event.starts_at.hour, event.starts_at.minute), (10, 0))

    def test_same_weekday_means_next_week(self):
        now = datetime(2026, 8, 18, 12, tzinfo=TZ)  # Tuesday
        result = parse_deadline("سه شنبه", TZ, now)
        self.assertEqual((result.date() - now.date()).days, 7)

    def test_ai_relative_day_is_corrected_from_source_text(self):
        now = datetime(2026, 9, 2, 22, 19, tzinfo=TZ)
        ai_result = ParsedMeeting(
            "جلسه", datetime(2026, 9, 4, 11, tzinfo=TZ),
            datetime(2026, 9, 4, 12, tzinfo=TZ),
        )
        corrected = enforce_explicit_relative_day(
            ai_result, "جلسه فردا ساعت ۱۱", TZ, now
        )
        self.assertEqual(corrected.starts_at.date(), datetime(2026, 9, 3).date())
        self.assertEqual(corrected.starts_at.hour, 11)

    def test_ai_wrong_weekday_date_is_corrected_deterministically(self):
        # 2026-09-14 is Monday / 23 Shahrivar 1405. The following Wednesday
        # is 2026-09-16 / 25 Shahrivar, even if AI incorrectly returns Tuesday.
        now = datetime(2026, 9, 14, 18, 31, tzinfo=TZ)
        ai_result = ParsedMeeting(
            "با یعقوبی", datetime(2026, 9, 15, 12, tzinfo=TZ),
            datetime(2026, 9, 15, 13, tzinfo=TZ),
        )
        corrected = enforce_explicit_named_weekday(
            ai_result, "چهارشنبه جلسه با یعقوبی ساعت ۱۲", TZ, now
        )
        self.assertEqual(corrected.starts_at, datetime(2026, 9, 16, 12, tzinfo=TZ))
        self.assertEqual(corrected.ends_at, datetime(2026, 9, 16, 13, tzinfo=TZ))
        jalali = __import__("jdatetime").datetime.fromgregorian(datetime=corrected.starts_at)
        self.assertEqual((jalali.year, jalali.month, jalali.day), (1405, 6, 25))

    def test_same_named_weekday_means_next_week_for_ai_correction(self):
        now = datetime(2026, 9, 16, 8, tzinfo=TZ)  # Wednesday
        ai_result = ParsedMeeting(
            "جلسه", datetime(2026, 9, 16, 12, tzinfo=TZ),
            datetime(2026, 9, 16, 13, tzinfo=TZ),
        )
        corrected = enforce_explicit_named_weekday(ai_result, "چهارشنبه ساعت ۱۲", TZ, now)
        self.assertEqual(corrected.starts_at.date(), datetime(2026, 9, 23).date())

    def test_multiple_weekdays_are_not_forced_to_one_date(self):
        now = datetime(2026, 9, 14, 18, 31, tzinfo=TZ)
        ai_result = ParsedMeeting(
            "جلسه", datetime(2026, 9, 20, 12, tzinfo=TZ),
            datetime(2026, 9, 20, 13, tzinfo=TZ),
        )
        corrected = enforce_explicit_named_weekday(
            ai_result, "جلسه یکشنبه‌ها و چهارشنبه‌ها ساعت ۱۲", TZ, now
        )
        self.assertEqual(corrected, ai_result)

    def test_relative_day_has_priority_over_inconsistent_weekday(self):
        now = datetime(2026, 9, 14, 18, 31, tzinfo=TZ)  # Monday
        relative_corrected = ParsedMeeting(
            "جلسه", datetime(2026, 9, 15, 12, tzinfo=TZ),
            datetime(2026, 9, 15, 13, tzinfo=TZ),
        )
        corrected = enforce_explicit_named_weekday(
            relative_corrected, "فردا چهارشنبه ساعت ۱۲", TZ, now
        )
        self.assertEqual(corrected, relative_corrected)


if __name__ == "__main__":
    unittest.main()
