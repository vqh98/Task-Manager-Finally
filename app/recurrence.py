from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re

import jdatetime

WEEKDAY_NAMES = {"شنبه": 5, "یکشنبه": 6, "یک شنبه": 6, "دوشنبه": 0, "دو شنبه": 0,
                 "سه شنبه": 1, "چهارشنبه": 2, "چهار شنبه": 2,
                 "پنجشنبه": 3, "پنج شنبه": 3, "جمعه": 4}
PERSIAN_NUMBERS = {"یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6,
                   "هفت": 7, "هشت": 8, "نه": 9, "ده": 10, "یازده": 11, "دوازده": 12}


@dataclass(slots=True, frozen=True)
class RecurrenceRule:
    frequency: str
    interval: int = 1
    weekdays: tuple[int, ...] = ()
    jalali_day: int | None = None
    ends_on: date | None = None

    def validate(self) -> "RecurrenceRule":
        if self.frequency not in {"daily", "weekly", "monthly_jalali", "even_days", "odd_days"}:
            raise ValueError("frequency")
        if not 1 <= self.interval <= 365:
            raise ValueError("interval")
        if any(day not in range(7) for day in self.weekdays):
            raise ValueError("weekdays")
        if self.frequency == "monthly_jalali" and not 1 <= (self.jalali_day or 0) <= 31:
            raise ValueError("jalali_day")
        return self


def _jalali_month_length(year: int, month: int) -> int:
    if month <= 6:
        return 31
    if month <= 11:
        return 30
    return 30 if jdatetime.date(year, 1, 1).isleap() else 29


def next_occurrence(anchor: datetime, current: datetime, rule: RecurrenceRule) -> datetime | None:
    """Return the next scheduled occurrence, based on schedule dates (never completion time)."""
    rule.validate()
    if rule.frequency == "daily":
        candidate = current + timedelta(days=rule.interval)
    elif rule.frequency in {"even_days", "odd_days"}:
        wanted = 0 if rule.frequency == "even_days" else 1
        candidate = current + timedelta(days=1)
        while candidate.day % 2 != wanted:
            candidate += timedelta(days=1)
    elif rule.frequency == "weekly":
        weekdays = tuple(sorted(set(rule.weekdays or (anchor.weekday(),))))
        candidate = current + timedelta(days=1)
        anchor_week = (anchor.date() - timedelta(days=anchor.weekday()))
        while True:
            candidate_week = candidate.date() - timedelta(days=candidate.weekday())
            weeks = (candidate_week - anchor_week).days // 7
            if candidate.weekday() in weekdays and weeks >= 0 and weeks % rule.interval == 0:
                break
            candidate += timedelta(days=1)
    else:
        jcur = jdatetime.date.fromgregorian(date=current.date())
        month_index = jcur.year * 12 + jcur.month - 1 + rule.interval
        jy, jm = divmod(month_index, 12)
        jm += 1
        day = min(rule.jalali_day or jdatetime.date.fromgregorian(date=anchor.date()).day,
                  _jalali_month_length(jy, jm))
        gregorian = jdatetime.date(jy, jm, day).togregorian()
        candidate = datetime.combine(gregorian, current.timetz())
        if current.tzinfo is not None and candidate.tzinfo is None:
            candidate = candidate.replace(tzinfo=current.tzinfo)
    if rule.ends_on and candidate.date() > rule.ends_on:
        return None
    return candidate


def occurrences_until(anchor: datetime, last: datetime, rule: RecurrenceRule,
                      horizon: datetime, limit: int = 100) -> list[datetime]:
    result: list[datetime] = []
    cursor = last
    while len(result) < limit:
        following = next_occurrence(anchor, cursor, rule)
        if following is None or following > horizon:
            break
        result.append(following)
        cursor = following
    return result


def parse_temporary_weekday_override(text: str, now: datetime) -> list[dict] | None:
    """Split one temporary weekday time override into ordinary weekly series."""
    from app.parsing import extract_meeting_time_ranges, normalize_digits
    clean = re.sub(r"[\u200c\u200d]+", " ", normalize_digits(text or ""))
    ranges = extract_meeting_time_ranges(clean)
    if len(ranges) != 2:
        return None
    found: list[tuple[int, int, int]] = []
    for name, weekday in sorted(WEEKDAY_NAMES.items(), key=lambda item: len(item[0]), reverse=True):
        for match in re.finditer(re.escape(name), clean):
            if not any(start <= match.start() < end for start, end, _ in found):
                found.append((match.start(), match.end(), weekday))
    weekdays = sorted({weekday for _, _, weekday in found})
    if len(weekdays) < 2:
        return None
    override_match = re.search(
        r"(?:تایم|زمان|ساعت)\s*(?:روز\s*)?(?P<day>شنبه|یک\s*شنبه|دو\s*شنبه|سه\s*شنبه|چهار\s*شنبه|پنج\s*شنبه|جمعه)(?:\s*(?:اش|ش))?",
        clean,
    ) or re.search(
        r"(?P<day>شنبه|یک\s*شنبه|دو\s*شنبه|سه\s*شنبه|چهار\s*شنبه|پنج\s*شنبه|جمعه)(?:\s*(?:اش|ش))?.{0,24}?(?:می\s*شود|میشه|تغییر)",
        clean,
    )
    if not override_match:
        return None
    normalized_names = {re.sub(r"\s+", "", key): value for key, value in WEEKDAY_NAMES.items()}
    override_weekday = normalized_names.get(re.sub(r"\s+", "", override_match.group("day")))
    if override_weekday not in weekdays:
        return None
    weeks_match = re.search(
        r"(?:تا|برای)\s*(\d+|یک|دو|سه|چهار|پنج|شش|هفت|هشت|نه|ده|یازده|دوازده)\s*هفته", clean
    )
    if not weeks_match:
        return None
    token = weeks_match.group(1)
    weeks = int(token) if token.isdigit() else PERSIAN_NUMBERS[token]
    if not 1 <= weeks <= 52:
        return None

    def next_day(weekday: int, hour: int, minute: int) -> datetime:
        candidate = (now + timedelta(days=(weekday - now.weekday()) % 7)).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
        return candidate + timedelta(days=7) if candidate <= now else candidate

    def end_for(start: datetime, hour: int, minute: int) -> datetime:
        end = start.replace(hour=hour, minute=minute)
        return end + timedelta(days=1) if end <= start else end

    base_h1, base_m1, base_h2, base_m2 = ranges[0]
    new_h1, new_m1, new_h2, new_m2 = ranges[1]
    parts: list[dict] = []
    other_weekdays = [day for day in weekdays if day != override_weekday]
    if other_weekdays:
        anchor = min(next_day(day, base_h1, base_m1) for day in other_weekdays)
        parts.append({"starts_at": anchor.isoformat(), "ends_at": end_for(anchor, base_h2, base_m2).isoformat(),
                      "recurrence": {"frequency": "weekly", "interval": 1, "weekdays": other_weekdays,
                                     "jalali_day": None, "ends_on": None}, "kind": "unchanged"})
    temporary_start = next_day(override_weekday, new_h1, new_m1)
    temporary_end_date = (temporary_start + timedelta(weeks=weeks - 1)).date()
    parts.append({"starts_at": temporary_start.isoformat(),
                  "ends_at": end_for(temporary_start, new_h2, new_m2).isoformat(),
                  "recurrence": {"frequency": "weekly", "interval": 1, "weekdays": [override_weekday],
                                 "jalali_day": None, "ends_on": temporary_end_date.isoformat()},
                  "kind": "temporary", "occurrences": weeks})
    resumed_start = (temporary_start + timedelta(weeks=weeks)).replace(hour=base_h1, minute=base_m1)
    parts.append({"starts_at": resumed_start.isoformat(),
                  "ends_at": end_for(resumed_start, base_h2, base_m2).isoformat(),
                  "recurrence": {"frequency": "weekly", "interval": 1, "weekdays": [override_weekday],
                                 "jalali_day": None, "ends_on": None}, "kind": "resumed"})
    return parts


def parse_recurrence_text(text: str, anchor: datetime, timezone) -> dict:
    """Parse a broad Persian recurrence instruction into a serializable config."""
    from app.parsing import parse_deadline
    clean=(text or "").replace("ي","ی").replace("ك","ک").replace("‌"," ")
    clean=clean.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩","01234567890123456789"))
    if "روزهای زوج" in clean or "روز زوج" in clean:
        return {"frequency": "even_days", "interval": 1, "weekdays": [], "jalali_day": None, "ends_on": None}
    if "روزهای فرد" in clean or "روز فرد" in clean:
        return {"frequency": "odd_days", "interval": 1, "weekdays": [], "jalali_day": None, "ends_on": None}
    weekday_names={"شنبه":5,"یکشنبه":6,"دوشنبه":0,"سه شنبه":1,"چهارشنبه":2,"پنجشنبه":3,"جمعه":4}
    matches = []
    for name, value in weekday_names.items():
        for found in re.finditer(re.escape(name), clean):
            matches.append((found.start(), found.end(), value))
    covered = {
        value for start, end, value in matches
        if not any(
            other_start <= start and end <= other_end and
            (other_end - other_start) > (end - start)
            for other_start, other_end, _ in matches
        )
    }
    weekdays = tuple(sorted(covered))
    number=re.search(r"هر\s*(\d+)?\s*(روز|هفته|ماه)",clean)
    # A natural forwarded announcement often says only
    # «یکشنبه‌ها و سه‌شنبه‌ها» or «یکشنبه و سه‌شنبه». Two or more explicit
    # weekdays are an unambiguous weekly recurrence even without «هر هفته».
    bare_weekday_recurrence = len(weekdays) >= 2
    if not number and not bare_weekday_recurrence:
        raise ValueError("pattern")
    if number:
        interval=int(number.group(1) or 1); unit=number.group(2)
        frequency={"روز":"daily","هفته":"weekly","ماه":"monthly_jalali"}[unit]
    else:
        interval, frequency = 1, "weekly"
    if frequency=="weekly" and not weekdays: weekdays=(anchor.weekday(),)
    jalali_day=None
    if frequency=="monthly_jalali":
        found=re.search(r"روز\s*(\d{1,2})",clean)
        jalali_day=int(found.group(1)) if found else jdatetime.date.fromgregorian(date=anchor.date()).day
    ends_on=None
    if "بدون پایان" not in clean and "تا " in clean:
        ending=clean.split("تا ",1)[1].strip()
        parsed=parse_deadline(ending,timezone,anchor)
        if parsed: ends_on=parsed.date().isoformat()
    RecurrenceRule(frequency,interval,weekdays,jalali_day,date.fromisoformat(ends_on) if ends_on else None).validate()
    return {"frequency":frequency,"interval":interval,"weekdays":list(weekdays),
            "jalali_day":jalali_day,"ends_on":ends_on}
