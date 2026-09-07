from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re

import jdatetime


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
