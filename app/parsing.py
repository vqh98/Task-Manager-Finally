from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import dateparser
import jdatetime


PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
PERSIAN_MONTHS = {
    "فروردین": 1, "اردیبهشت": 2, "خرداد": 3, "تیر": 4,
    "مرداد": 5, "شهریور": 6, "مهر": 7, "آبان": 8,
    "آذر": 9, "دی": 10, "بهمن": 11, "اسفند": 12,
}

WEEKDAY_NUMBERS = {
    "شنبه": 5,
    "یکشنبه": 6, "یک شنبه": 6, "یک‌شنبه": 6,
    "دوشنبه": 0, "دو شنبه": 0, "دو‌شنبه": 0,
    "سه شنبه": 1, "سه‌شنبه": 1,
    "چهارشنبه": 2, "چهار شنبه": 2, "چهار‌شنبه": 2,
    "پنجشنبه": 3, "پنج شنبه": 3, "پنج‌شنبه": 3,
    "جمعه": 4,
}


def _next_named_weekday(text: str, now: datetime) -> datetime | None:
    """Resolve a Persian weekday as its first future occurrence."""
    for name, weekday in sorted(WEEKDAY_NUMBERS.items(), key=lambda item: len(item[0]), reverse=True):
        if name not in text:
            continue
        delta = (weekday - now.weekday()) % 7
        if re.search(r"(?:هفته\s*)?(?:دیگه|دیگر|آینده|بعد)", text):
            delta += 7
        elif delta == 0:
            delta = 7
        return now + timedelta(days=delta)
    return None


@dataclass(slots=True)
class ParsedMeeting:
    title: str
    starts_at: datetime
    ends_at: datetime


def explicit_relative_day(text: str) -> int | None:
    """Return the explicit relative-day offset, if the text contains one."""
    normalized = re.sub(r"[\u200c\u200d]+", " ", normalize_digits(text or ""))
    if re.search(r"پس\s*فردا", normalized):
        return 2
    if re.search(r"\bفردا\b", normalized):
        return 1
    if re.search(r"\bامروز\b", normalized):
        return 0
    return None


def enforce_explicit_relative_day(
    meeting: ParsedMeeting, source_text: str, timezone: ZoneInfo, now: datetime
) -> ParsedMeeting:
    """Override an AI date when the user used an unambiguous relative phrase."""
    offset = explicit_relative_day(source_text)
    if offset is None:
        return meeting
    target_date = (now + timedelta(days=offset)).date()
    starts_at = meeting.starts_at.astimezone(timezone).replace(
        year=target_date.year, month=target_date.month, day=target_date.day
    )
    ends_at = starts_at + (meeting.ends_at - meeting.starts_at)
    return ParsedMeeting(meeting.title, starts_at, ends_at)


def enforce_explicit_calendar_date(
    meeting: ParsedMeeting, source_text: str, timezone: ZoneInfo, now: datetime
) -> ParsedMeeting:
    """Use the deterministic Jalali converter for explicit calendar dates."""
    normalized = normalize_digits(source_text or "")
    # Normalize whitespace as forwarded Telegram messages may contain newlines,
    # non-breaking spaces, or a ZWNJ around «شهریورماه».
    normalized = re.sub(r"[\u200c\u200d\u00a0]+", " ", normalized)
    has_jalali_date = bool(
        re.search(r"1[34]\d{2}[/-]\d{1,2}[/-]\d{1,2}", normalized)
        or re.search(rf"\d{{1,2}}\s*(?:ماه\s*)?(?:{'|'.join(map(re.escape, PERSIAN_MONTHS))})(?:ماه)?\s*1[34]\d{{2}}", normalized)
        # A day and Persian month without a year is still an explicit
        # calendar date.  AI date extraction must not be allowed to replace
        # it with a guessed relative date (for example ۱۴ instead of ۱۶
        # شهریور).  parse_datetime resolves the omitted year from ``now``.
        or re.search(rf"\d{{1,2}}\s*(?:ماه\s*)?(?:{'|'.join(map(re.escape, PERSIAN_MONTHS))})(?:ماه)?(?!\s*1[34]\d{{2}})", normalized)
    )
    if not has_jalali_date:
        return meeting
    deterministic = parse_datetime(normalized, timezone, now)
    if not deterministic:
        return meeting
    duration = meeting.ends_at - meeting.starts_at
    if duration <= timedelta(0) or duration > timedelta(hours=24):
        duration = timedelta(hours=1)
    return ParsedMeeting(meeting.title, deterministic, deterministic + duration)


def normalize_digits(text: str) -> str:
    return text.translate(PERSIAN_DIGITS).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))


def _fa_hour(value: int) -> str:
    return str(value).translate(PERSIAN_DIGITS)


def _day_label(starts_at: datetime, now: datetime) -> str:
    start_date = starts_at.date()
    if start_date == now.date():
        return "امروز"
    if start_date == (now + timedelta(days=1)).date():
        return "فردا"
    if start_date == (now + timedelta(days=2)).date():
        return "پس‌فردا"
    return ""


def format_meeting_summary(base_title: str, starts_at: datetime, ends_at: datetime,
                           now: datetime | None = None) -> str:
    now = now or starts_at
    time_part = f"ساعت {_fa_hour(starts_at.hour)}"
    if ends_at.hour != starts_at.hour or ends_at.minute:
        end_hour = _fa_hour(ends_at.hour)
        if ends_at.minute:
            end_hour += f":{_fa_hour(ends_at.minute).zfill(2)}"
        time_part += f" الی {end_hour}"
    day = _day_label(starts_at, now)
    parts = [base_title.strip(), time_part]
    if day:
        parts.append(day)
    return " ".join(part for part in parts if part)[:200]


def summarize_meeting_title(text: str) -> str:
    clean = normalize_digits(text)
    if "موکب" in clean and any(word in clean for word in ("خدمت", "پذیرایی", "زائر")):
        return "خدمت‌رسانی در موکب"
    ignored = (
        "سلام", "عرض ادب", "وقت بخیر", "التماس دعا", "با تشکر",
        "ان‌شاءالله", "انشاالله", "صبح بخیر", "شب بخیر",
    )
    candidates: list[str] = []
    months = "|".join(map(re.escape, PERSIAN_MONTHS))

    def without_temporal_metadata(value: str) -> str:
        """Remove date/time fragments while retaining nearby subject text."""
        value = re.sub(r"(?:از\s*)?(?:ساعت\s*)?\d{1,2}(?::\d{2})?\s*(?:تا|الی|[-–])\s*(?:ساعت\s*)?\d{1,2}(?::\d{2})?", " ", value)
        value = re.sub(r"(?:ساعت|در ساعت|از ساعت)\s*\d{1,2}(?::\d{2})?", " ", value)
        value = re.sub(r"\b(?:امروز|فردا|پس\s*فردا|این هفته|هفته\s*(?:بعد|آینده))\b", " ", value)
        value = re.sub(r"\b\d{1,2}\s+(?:ماه\s+)?(?:" + months + r")(?:\s+1[34]\d{2})?\b", " ", value)
        value = re.sub(r"\b1[34]\d{2}[/-]\d{1,2}[/-]\d{1,2}\b", " ", value)
        value = re.sub(r"\b(?:شنبه|یکشنبه|دوشنبه|سه[‌ ]?شنبه|چهارشنبه|پنج[‌ ]?شنبه|جمعه)\b", " ", value)
        value = re.sub(r"\b(?:مورخ|تاریخ|روز)\b", " ", value)
        value = re.sub(r"[|،؛:：]+", " ", value)
        value = re.sub(r"\s+(?:در|برای|از|تا|الی)\s*$", "", value)
        return value.strip(" 🟢🔴🟡⚪️-،؛:：.|")

    for raw_line in clean.splitlines():
        line = raw_line.strip(" 🟢🔴🟡⚪️-:،")
        if not line or any(line.startswith(prefix) for prefix in ignored):
            continue
        # A line that starts with a relative date is a schedule line, not a
        # subject (the subject, when present, is normally on another line).
        if re.match(r"^(?:امروز|فردا|پس\s*فردا|این هفته|هفته\s*(?:بعد|آینده))\b", line) and re.search(r"\d", line):
            continue
        line = without_temporal_metadata(line)
        if line:
            candidates.append(line)
    if not candidates:
        return "جلسه"
    # Promotional announcements often bury the actual event subject inside a
    # long sentence. Extract the concise phrase around an event marker first.
    marker = re.compile(r"(?:افتتاحیه|جلسه|نشست|همایش|رویداد|دیدار|کنفرانس)[^\n.!؟]{0,70}", re.I)
    marked = []
    for value in candidates:
        match = marker.search(value)
        if match:
            subject = re.split(r"\s+(?:و|که|قراره|قرار است|است|هست|می‌باشد)\s+", match.group(0), maxsplit=1)[0]
            subject = subject.strip(" ،؛:.-")
            subject = re.sub(r"(?:‌?ست| است)$", "", subject).strip()
            if len(subject) >= 4:
                marked.append(subject)
    if marked:
        candidates = marked
    # Prefer the most informative remaining sentence; temporal headings have
    # already become empty and are therefore never selected.
    def subject_score(value: str) -> tuple[int, int]:
        # Prefer an explicit event subject over hashtags, signatures and
        # announcement boilerplate; length is only a tie-breaker.
        lowered = value.lower()
        score = 0
        if any(word in lowered for word in ("افتتاحیه", "جلسه", "نشست", "همایش", "رویداد", "دیدار", "برنامه")):
            score += 20
        if value.lstrip().startswith("#") or any(word in lowered for word in ("روابط‌عمومی", "اطلاع‌رسانی")):
            score -= 20
        return score, min(len(value), 100)
    title = max(candidates, key=subject_score)
    title = re.sub(r"^(جلسه|meeting)\s*[:：-]?\s*", "", title, flags=re.I)
    return (title or "جلسه")[:100]


def parse_datetime(text: str, timezone: ZoneInfo, now: datetime | None = None) -> datetime | None:
    text = normalize_digits(text.strip())
    now = now or datetime.now(timezone)
    time_match = re.search(r"(?:ساعت\s*)?(\d{1,2})(?::(\d{2}))", text)
    if not time_match:
        time_match = re.search(r"ساعت\s*(\d{1,2})(?:\s|$)", text)
    hour = int(time_match.group(1)) if time_match else 9
    minute = int(time_match.group(2) or 0) if time_match and time_match.lastindex and time_match.lastindex >= 2 else 0
    # Treat both ordinary spaces and ZWNJ as equivalent in relative phrases.
    relative_text = re.sub(r"[\u200c\u200d]+", " ", text)
    if re.search(r"پس\s*فردا", relative_text):
        return (now + timedelta(days=2)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if re.search(r"\bفردا\b", relative_text):
        return (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if re.search(r"\bامروز\b", relative_text):
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    named_weekday = _next_named_weekday(text, now)
    if named_weekday:
        return named_weekday.replace(hour=hour, minute=minute, second=0, microsecond=0)
    named_months = "|".join(PERSIAN_MONTHS)
    # Persian announcements commonly write «۳ شهریورماه ۱۴۰۵».
    named_date_text = text.replace("ماه", " ")
    named_jalali = re.search(rf"(\d{{1,2}})\s+({named_months})\s+(1[34]\d{{2}})", named_date_text)
    if not named_jalali:
        attached = re.search(rf"(\d{{1,2}})\s+({named_months})ماه\s+(1[34]\d{{2}})", text)
        named_jalali = attached
    if named_jalali:
        day = int(named_jalali.group(1))
        month = PERSIAN_MONTHS[named_jalali.group(2)]
        year = int(named_jalali.group(3))
        gregorian = jdatetime.datetime(year, month, day, hour, minute).togregorian()
        return gregorian.replace(tzinfo=timezone)
    # تاریخ شمسی صریح مانند 1405/05/24 ساعت 10:30
    jalali = re.search(r"(1[34]\d{2})[/-](\d{1,2})[/-](\d{1,2})", text)
    if jalali:
        year, month, day = map(int, jalali.group(1, 2, 3))
        gregorian = jdatetime.datetime(year, month, day, hour, minute).togregorian()
        return gregorian.replace(tzinfo=timezone)
    parsed = dateparser.parse(
        text,
        languages=["fa", "en"],
        settings={"RELATIVE_BASE": now.replace(tzinfo=None), "PREFER_DATES_FROM": "future", "TIMEZONE": str(timezone), "RETURN_AS_TIMEZONE_AWARE": True},
    )
    return parsed.astimezone(timezone) if parsed else None


def _jalali_month_last_day(year: int, month: int) -> int:
    if month <= 6:
        return 31
    if month <= 11:
        return 30
    for day in (30, 29):
        try:
            jdatetime.date(year, month, day)
            return day
        except ValueError:
            continue
    return 29


def _jalali_end(year: int, month: int, day: int, timezone: ZoneInfo) -> datetime:
    value = jdatetime.datetime(year, month, day, 23, 59).togregorian()
    return value.replace(tzinfo=timezone)


def parse_deadline(text: str, timezone: ZoneInfo, now: datetime | None = None) -> datetime | None:
    clean = normalize_digits(text.strip()).replace("ي", "ی").replace("ك", "ک")
    clean = re.sub(r"\s+", " ", clean)
    now = now or datetime.now(timezone)
    days_match = re.search(r"(?:تا\s*)?(\d+)\s*روز\s*(?:دیگه|دیگر|آینده|بعد(?:تر)?)", clean)
    if days_match:
        return (now + timedelta(days=int(days_match.group(1)))).replace(hour=23, minute=59, second=0, microsecond=0)

    # در تعریف محصول، «آخر هفته» پنج‌شنبه است.
    if re.search(r"(?:آخر|اخر)\s*هفته", clean):
        days_until_thursday = (3 - now.weekday()) % 7
        asks_next_week = bool(re.search(r"(?:دیگه|دیگر|آینده|بعد)", clean))
        if asks_next_week:
            days_until_thursday += 7 if days_until_thursday <= 6 else 0
        elif days_until_thursday == 0 and now.hour >= 23:
            days_until_thursday = 7
        return (now + timedelta(days=days_until_thursday)).replace(hour=23, minute=59, second=0, microsecond=0)

    named_weekday = _next_named_weekday(clean, now)
    if named_weekday:
        return named_weekday.replace(hour=23, minute=59, second=0, microsecond=0)

    jalali_now = jdatetime.datetime.fromgregorian(datetime=now)
    if re.search(r"(?:آخر|اخر)\s*(?:همین\s*)?ماه", clean):
        last_day = _jalali_month_last_day(jalali_now.year, jalali_now.month)
        return _jalali_end(jalali_now.year, jalali_now.month, last_day, timezone)

    seasons = {
        "بهار": (3, 31), "تابستان": (6, 31), "پاییز": (9, 30), "زمستان": (12, None),
    }
    for season, (month, fixed_day) in seasons.items():
        if re.search(rf"(?:تا\s*)?(?:آخر|اخر|پایان)\s*{season}", clean):
            day = fixed_day or _jalali_month_last_day(jalali_now.year, 12)
            year = jalali_now.year if (jalali_now.month, jalali_now.day) <= (month, day) else jalali_now.year + 1
            day = fixed_day or _jalali_month_last_day(year, 12)
            return _jalali_end(year, month, day, timezone)

    if re.search(r"(?:تا\s*)?(?:آخر|اخر)\s*سال", clean):
        year = jalali_now.year
        if (jalali_now.month, jalali_now.day) == (12, _jalali_month_last_day(year, 12)) and now.hour >= 23:
            year += 1
        return _jalali_end(year, 12, _jalali_month_last_day(year, 12), timezone)

    for month_name, month_number in PERSIAN_MONTHS.items():
        if re.search(rf"(?:تا\s*)?(?:آخر|اخر|پایان)\s*{month_name}", clean):
            year = jalali_now.year if jalali_now.month <= month_number else jalali_now.year + 1
            return _jalali_end(year, month_number, _jalali_month_last_day(year, month_number), timezone)

    parsed = parse_datetime(clean, timezone, now)
    if not parsed:
        return None
    has_explicit_time = bool(re.search(r"ساعت\s*\d{1,2}|\d{1,2}:\d{2}", clean))
    return parsed if has_explicit_time else parsed.replace(hour=23, minute=59, second=0, microsecond=0)


def parse_forwarded_meeting(text: str, timezone: ZoneInfo, now: datetime | None = None) -> ParsedMeeting | None:
    clean = normalize_digits(text.strip())
    now = now or datetime.now(timezone)
    # Common Persian forwarded format: «پنجشنبه مورخ ۵ شهریور | ساعت ۱۳»
    direct = re.search(r"(?P<day>\d{1,2})\s+(?P<month>\u0641\u0631\u0648\u0631\u062f\u06cc\u0646|\u0627\u0631\u062f\u06cc\u0628\u0647\u0634\u062a|\u062e\u0631\u062f\u0627\u062f|\u062a\u06cc\u0631|\u0645\u0631\u062f\u0627\u062f|\u0634\u0647\u0631\u06cc\u0648\u0631|\u0645\u0647\u0631|\u0622\u0628\u0627\u0646|\u0622\u0630\u0631|\u062f\u06cc|\u0628\u0647\u0645\u0646|\u0627\u0633\u0641\u0646\u062f)(?:\s+(?P<year>1[34]\d{2}))?.*?(?:\u0633\u0627\u0639\u062a\s*)(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?", clean)
    if direct:
        month_map = {"\u0641\u0631\u0648\u0631\u062f\u06cc\u0646":1,"\u0627\u0631\u062f\u06cc\u0628\u0647\u0634\u062a":2,"\u062e\u0631\u062f\u0627\u062f":3,"\u062a\u06cc\u0631":4,"\u0645\u0631\u062f\u0627\u062f":5,"\u0634\u0647\u0631\u06cc\u0648\u0631":6,"\u0645\u0647\u0631":7,"\u0622\u0628\u0627\u0646":8,"\u0622\u0630\u0631":9,"\u062f\u06cc":10,"\u0628\u0647\u0645\u0646":11,"\u0627\u0633\u0641\u0646\u062f":12}
        year = int(direct.group("year") or jdatetime.date.fromgregorian(date=now.date()).year)
        value = jdatetime.datetime(year, month_map[direct.group("month")], int(direct.group("day")),
                                   int(direct.group("hour")), int(direct.group("minute") or 0)).togregorian()
        starts_at = value.replace(tzinfo=timezone)
    else:
        starts_at = parse_datetime(clean, timezone, now)
    if not starts_at:
        return None
    duration_match = re.search(r"(?:مدت|duration)\s*[:：]?\s*(\d+)\s*(ساعت|دقیقه|hour|minute)?", clean, re.I)
    duration = 60
    if duration_match:
        value = int(duration_match.group(1))
        duration = value * 60 if duration_match.group(2) in {"ساعت", "hour"} else value
    range_match = re.search(
        r"(?:از\s*)?(?:ساعت\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:الی|تا|[-–])\s*(?:ساعت\s*)?(\d{1,2})(?::(\d{2}))?",
        clean,
    )
    if range_match:
        h1, m1, h2, m2 = int(range_match.group(1)), int(range_match.group(2) or 0), int(range_match.group(3)), int(range_match.group(4) or 0)
        starts_at = starts_at.replace(hour=h1, minute=m1, second=0, microsecond=0)
        ends_at = starts_at.replace(hour=h2, minute=m2)
        if ends_at <= starts_at:
            ends_at += timedelta(days=1)
    else:
        ends_at = starts_at + timedelta(minutes=duration)
    base_title = summarize_meeting_title(clean)
    return ParsedMeeting(base_title[:200], starts_at, ends_at)
