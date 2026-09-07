from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings


logger = logging.getLogger(__name__)


def _unchanged_days_hint(text: str) -> int | None:
    normalized = (text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    normalized = normalized.replace("\u200c", " ")
    if not (
        re.search(r"(?:بدون|هیچ)\s+تغییر", normalized)
        or re.search(r"تغییر.{0,20}ن(?:کرد|شده|داشته)", normalized)
    ):
        return None
    words = {
        "یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5,
        "شش": 6, "هفت": 7, "هشت": 8, "نه": 9, "ده": 10,
    }
    match = re.search(
        r"(?:در\s+)?(?P<count>\d+|یک|دو|سه|چهار|پنج|شش|هفت|هشت|نه|ده)?\s*"
        r"(?P<unit>روز|هفته|ماه)(?:\s+(?:اخیر|گذشته))?",
        normalized,
    )
    if not match:
        return None
    count_raw = match.group("count") or "یک"
    count = int(count_raw) if count_raw.isdigit() else words[count_raw]
    multiplier = {"روز": 1, "هفته": 7, "ماه": 30}[match.group("unit")]
    return max(0, min(3650, count * multiplier))


@dataclass(slots=True)
class ParsedAIMeeting:
    title: str
    starts_at: datetime
    ends_at: datetime
    notify_usernames: list[str]


@dataclass(slots=True)
class ParsedAITask:
    title: str | None
    deadline: datetime | None
    priority: int | None
    assignee: str | None
    note: str | None
    missing_fields: list[str]

@dataclass(slots=True)
class ParsedAIReminder:
    title: str
    due_at: datetime
    repeat_type: str | None = None


class AIInterpreter:
    """Compatibility helpers for semantic command routing tests and callers."""

    @staticmethod
    def command_is_complex(text: str, context: str = "") -> bool:
        clean = re.sub(r"[‌s]+", " ", (text or "").strip().lower())
        references = ("همان", "همون", "این یکی", "قبلی", "بعدی", "اولی", "دومی", "سومی")
        sequences = (" و بعد ", " سپس ", " بعدش ", " همچنین ", "؛", ";")
        mutations = ("جابه جا", "جابه‌جا", "عوض", "ببر", "بنداز", "عقب", "جلو")
        return (
            any(x in clean for x in sequences)
            or (any(x in clean for x in references) and context not in {"", "{}"})
            or sum(clean.count(x) for x in mutations) >= 2
            or len(clean) > 500
        )

    @staticmethod
    def _plan_needs_retry(raw: dict) -> bool:
        actions = raw.get("actions") if isinstance(raw, dict) else None
        if not isinstance(actions, list) or not actions:
            return True
        return any(
            not isinstance(action, dict)
            or action.get("operation") in {None, "", "unknown"}
            or float(action.get("confidence", 0)) < .72
            or not isinstance(action.get("missing_fields", []), list)
            for action in actions
        )


class AIService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def available(self) -> bool:
        return bool(self._settings.openai_api_key)

    async def transcribe_voice(self, audio_bytes: bytes, filename: str = "voice.ogg") -> str | None:
        if not self.available:
            return None
        url = f"{self._settings.openai_base_url.rstrip('/')}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self._settings.openai_api_key}"}
        if audio_bytes.startswith(b"RIFF"):
            filename, content_type = "voice.wav", "audio/wav"
        elif audio_bytes.startswith(b"OggS"):
            filename, content_type = "voice.ogg", "audio/ogg"
        else:
            content_type = "application/octet-stream"
        files = {"file": (filename, audio_bytes, content_type)}
        data = {"model": self._settings.openai_whisper_model, "language": "fa"}
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(url, headers=headers, files=files, data=data)
                response.raise_for_status()
                payload = response.json()
            text = (payload.get("text") or "").strip()
            return text or None
        except Exception:
            logger.exception("Voice transcription failed")
            return None

    async def classify_creation_intent(self, text: str) -> str | None:
        data = await self._chat_json(
            "Classify Persian creation requests. Return JSON with intent task, meeting, or unknown. Meetings and events happen at a start time; tasks are work with a deadline.", text)
        intent = str((data or {}).get("intent") or "").lower()
        return intent if intent in {"task", "meeting"} else None

    @staticmethod
    def _responses_text(payload: dict) -> str:
        """Extract text from native and OpenAI-compatible Responses payloads."""
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        for output in payload.get("output") or []:
            if not isinstance(output, dict):
                continue
            for content in output.get("content") or []:
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        raise ValueError("Responses API returned no text output")

    async def _chat_json(self, system: str, user: str) -> dict | None:
        if not self.available:
            return None
        url = f"{self._settings.openai_base_url.rstrip('/')}/responses"
        headers = {
            "Authorization": f"Bearer {self._settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._settings.openai_model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "text": {"format": {"type": "json_object"}},
            "temperature": 0.1,
        }
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(url, headers=headers, json=body)
                response.raise_for_status()
                content = self._responses_text(response.json())
            return json.loads(content)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "AI Responses request failed: status=%s body=%s",
                exc.response.status_code,
                exc.response.text[:1000],
            )
            return None
        except Exception:
            logger.exception("AI Responses request failed")
            return None

    async def parse_natural_deadline(
        self, text: str, timezone: ZoneInfo, now: datetime,
    ) -> datetime | None:
        """Resolve unrestricted Persian deadline language through the model."""
        data = await self._chat_json(
            "You resolve Persian natural-language deadlines semantically, not by matching examples. "
            "Return JSON with deadline (ISO8601 with timezone), confidence (0..1), and ambiguous (boolean). "
            "Interpret the whole message and all ordinary Persian descriptions: relative periods, named weekdays, "
            "Jalali/Gregorian dates, work-week language, before/after another described point, and colloquial wording. "
            "In Iran the end of the working week is Thursday. A bare weekday means its first future occurrence; "
            "if today is that weekday, use the same weekday next week. Date-only task deadlines end at 23:59. "
            "Never invent a date when the request is genuinely ambiguous; then return deadline null.",
            f"Now: {now.isoformat()}; Timezone: {timezone}; Text: {text}",
        )
        if not isinstance(data, dict) or data.get("ambiguous") is True:
            return None
        try:
            if float(data.get("confidence", 0)) < .72 or not data.get("deadline"):
                return None
            value = datetime.fromisoformat(str(data["deadline"]))
            return value.replace(tzinfo=timezone) if value.tzinfo is None else value.astimezone(timezone)
        except (TypeError, ValueError):
            return None

    async def parse_management_command(
        self, text: str, now: datetime, context: dict | None = None,
    ) -> dict | None:
        """Convert free Persian management language into a validated executable plan."""
        context = context or {}
        data = await self._chat_json(
            "You are the semantic command planner for a Persian task manager. Understand the user's intended outcome, "
            "including colloquial, indirect, multi-clause, relative-time, and contextual requests. Do not depend on "
            "specific example phrases. Return exactly one JSON operation. Allowed operations: task_query, dashboard, "
            "tomorrow_meetings, create_task, create_meeting, update_task_progress, update_task_note, "
            "update_task_deadline, update_task_status, move_meeting, unsupported. "
            "For task_query return filters (progress_lt/lte/gt/gte, status, priority, assignee_contains, "
            "title_contains, overdue, due_within_days, unchanged_days), sort, and limit. "
            "unchanged_days=N means tasks whose last change was N or more full days ago. For example, Persian "
            "requests meaning 'items that had no change during the last week' must return unchanged_days=7. "
            "For create_task return task_text containing the user's complete request. For create_meeting return "
            "meeting_text containing the complete request. For task mutations select target_task_id only from Context "
            "and return target_title plus the new progress/note/deadline/status. For move_meeting select "
            "target_meeting_id only from Context and return starts_at and ends_at ISO8601; preserve duration unless "
            "the user changes it. Resolve Persian and Jalali time relative to Now. In Iran end of working week is "
            "Thursday and a bare weekday is its first future occurrence. Never fabricate an ID. If the request is "
            "ambiguous or cannot be represented safely, use unsupported with a concise Persian message. Also return "
            "confidence 0..1 and requires_confirmation; every mutation must require confirmation.",
            f"Now: {now.isoformat()}; Context: {json.dumps(context, ensure_ascii=False)}; Command: {text}",
        )
        if not isinstance(data, dict):
            return None
        allowed = {
            "task_query", "dashboard", "tomorrow_meetings", "create_task", "create_meeting",
            "update_task_progress", "update_task_note", "update_task_deadline",
            "update_task_status", "move_meeting", "unsupported",
        }
        operation = str(data.get("operation") or "").strip()
        try:
            confidence = float(data.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        if operation not in allowed or (confidence < .65 and operation not in {"unsupported", "task_query", "dashboard", "tomorrow_meetings"}):
            return {"operation": "unsupported", "message": "فرمان به اندازه کافی روشن نبود؛ لطفاً منظور را کمی دقیق‌تر بنویسید."}
        if operation == "unsupported":
            return {"operation": operation, "message": str(data.get("message") or "این فرمان با اطمینان کافی قابل اجرا نیست.")[:300]}
        if operation in {"dashboard", "tomorrow_meetings"}:
            return {"operation": operation}
        if operation in {"create_task", "create_meeting"}:
            key = "task_text" if operation == "create_task" else "meeting_text"
            return {"operation": operation, key: str(data.get(key) or text).strip()[:4000]}
        if operation == "task_query":
            raw_filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
            filters: dict[str, object] = {}
            for key in ("progress_lt", "progress_lte", "progress_gt", "progress_gte"):
                if key in raw_filters:
                    try:
                        filters[key] = max(0, min(100, int(raw_filters[key])))
                    except (TypeError, ValueError):
                        pass
            status = str(raw_filters.get("status") or "").lower()
            if status in {"open", "done", "paused", "suspended", "all"}:
                filters["status"] = status
            try:
                priority = int(raw_filters.get("priority"))
                if priority in (1, 2, 3, 4):
                    filters["priority"] = priority
            except (TypeError, ValueError):
                pass
            for key in ("assignee_contains", "title_contains"):
                value = str(raw_filters.get(key) or "").strip()[:100]
                if value:
                    filters[key] = value
            if isinstance(raw_filters.get("overdue"), bool):
                filters["overdue"] = raw_filters["overdue"]
            try:
                if "due_within_days" in raw_filters:
                    filters["due_within_days"] = max(0, min(365, int(raw_filters["due_within_days"])))
            except (TypeError, ValueError):
                pass
            try:
                if "unchanged_days" in raw_filters:
                    filters["unchanged_days"] = max(0, min(3650, int(raw_filters["unchanged_days"])))
            except (TypeError, ValueError):
                pass
            if "unchanged_days" not in filters:
                unchanged_hint = _unchanged_days_hint(text)
                if unchanged_hint is not None:
                    filters["unchanged_days"] = unchanged_hint
            sort = str(data.get("sort") or "deadline_asc")
            if sort not in {"progress_asc", "progress_desc", "deadline_asc", "deadline_desc", "priority_asc"}:
                sort = "deadline_asc"
            try:
                limit = max(1, min(50, int(data.get("limit") or 20)))
            except (TypeError, ValueError):
                limit = 20
            return {"operation": operation, "filters": filters, "sort": sort, "limit": limit}

        plan = {"operation": operation, "requires_confirmation": True}
        id_key = "target_meeting_id" if operation == "move_meeting" else "target_task_id"
        valid_ids = {
            int(item["id"]) for item in context.get("meetings" if operation == "move_meeting" else "tasks", [])
            if isinstance(item, dict) and str(item.get("id", "")).isdigit()
        }
        try:
            target_id = int(data.get(id_key))
        except (TypeError, ValueError):
            target_id = 0
        if target_id not in valid_ids:
            return {"operation": "unsupported", "message": "مورد هدف با اطمینان در فهرست شما پیدا نشد."}
        plan[id_key] = target_id
        plan["target_title"] = str(data.get("target_title") or "")[:200]
        if operation == "update_task_progress":
            try:
                plan["progress"] = max(0, min(100, int(data["progress"])))
            except (KeyError, TypeError, ValueError):
                return {"operation": "unsupported", "message": "درصد پیشرفت جدید مشخص نیست."}
        elif operation == "update_task_note":
            plan["note"] = str(data.get("note") or "").strip()[:2000]
            if not plan["note"]:
                return {"operation": "unsupported", "message": "متن یادداشت مشخص نیست."}
        elif operation == "update_task_status":
            status = str(data.get("status") or "").lower()
            if status not in {"open", "done", "paused", "suspended"}:
                return {"operation": "unsupported", "message": "وضعیت جدید معتبر نیست."}
            plan["status"] = status
        elif operation == "update_task_deadline":
            plan["deadline"] = str(data.get("deadline") or "")
            try:
                parsed = datetime.fromisoformat(plan["deadline"])
                plan["deadline"] = (parsed.replace(tzinfo=now.tzinfo) if parsed.tzinfo is None else parsed.astimezone(now.tzinfo)).isoformat()
            except (TypeError, ValueError):
                return {"operation": "unsupported", "message": "ددلاین جدید با اطمینان قابل تشخیص نبود."}
        elif operation == "move_meeting":
            try:
                starts = datetime.fromisoformat(str(data["starts_at"]))
                ends = datetime.fromisoformat(str(data["ends_at"]))
                if starts.tzinfo is None: starts = starts.replace(tzinfo=now.tzinfo)
                if ends.tzinfo is None: ends = ends.replace(tzinfo=now.tzinfo)
                if ends <= starts: raise ValueError
                plan["starts_at"], plan["ends_at"] = starts.isoformat(), ends.isoformat()
            except (KeyError, TypeError, ValueError):
                return {"operation": "unsupported", "message": "زمان جدید جلسه با اطمینان قابل تشخیص نبود."}
        return plan
    async def summarize_meeting(self, text: str) -> str | None:
        data = await self._chat_json(
            "You summarize Persian event messages into one short Persian line under 80 chars. "
            "Include topic, time range if present, and day (امروز/فردا). Return JSON: {\"title\": \"...\"}",
            text,
        )
        title = (data or {}).get("title")
        return title.strip() if isinstance(title, str) and title.strip() else None

    async def parse_meeting(
        self, text: str, timezone: ZoneInfo, now: datetime,
    ) -> ParsedAIMeeting | None:
        data = await self._chat_json(
            "Extract meeting info from the ENTIRE Persian text. Return JSON with keys: "
            "title (only the meeting subject/topic; do not include greetings, date, weekday, time, duration, or boilerplate), "
            "starts_at (ISO8601), ends_at (ISO8601), "
            "notify_usernames (array of @usernames or names to notify, without @). "
            "Extract date and time independently from any line or sentence, regardless of order or formatting. "
            "Use Asia/Tehran timezone. Relative-day words are exact: امروز means the calendar date of Now, "
            "فردا means exactly Now's calendar date plus 1 day, and پس‌فردا means exactly plus 2 days; "
            "never add an extra day and do not infer these from UTC. If end time missing, assume 1 hour after start.",
            f"Now: {now.isoformat()}\nTimezone: {timezone}\nText:\n{text}",
        )
        if not data:
            return None
        try:
            starts_at = datetime.fromisoformat(str(data["starts_at"]))
            ends_at = datetime.fromisoformat(str(data["ends_at"]))
            if starts_at.tzinfo is None:
                starts_at = starts_at.replace(tzinfo=timezone)
            else:
                starts_at = starts_at.astimezone(timezone)
            if ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=timezone)
            else:
                ends_at = ends_at.astimezone(timezone)
            title = str(data.get("title") or "جلسه").strip()[:200]
            notify = data.get("notify_usernames") or []
            usernames = [str(item).lstrip("@").strip() for item in notify if str(item).strip()]
            return ParsedAIMeeting(title, starts_at, ends_at, usernames)
        except (KeyError, TypeError, ValueError):
            logger.exception("Could not parse AI meeting payload: %s", data)
            return None

    async def parse_task(
        self, text: str, timezone: ZoneInfo, now: datetime,
    ) -> ParsedAITask | None:
        data = await self._chat_json(
            "Extract task info from Persian manager speech/text. Return JSON with keys: "
            "title, deadline (ISO8601 or null), priority (1=urgent+important,2=important,"
            "3=urgent,4=low,null if unknown), assignee (person name,null), note (string), "
            "missing_fields (array of Persian labels for missing required fields among: "
            "عنوان,ددلاین,اولویت,مسئول,توضیح). Required: title,deadline,priority,assignee.",
            f"Now: {now.isoformat()}\nTimezone: {timezone}\nText:\n{text}",
        )
        if not data:
            return None
        deadline = None
        raw_deadline = data.get("deadline")
        if raw_deadline:
            try:
                deadline = datetime.fromisoformat(str(raw_deadline))
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone)
                else:
                    deadline = deadline.astimezone(timezone)
            except ValueError:
                deadline = None
        priority = data.get("priority")
        if priority is not None:
            try:
                priority = int(priority)
                if priority not in (1, 2, 3, 4):
                    priority = None
            except (TypeError, ValueError):
                priority = None
        missing = [str(item) for item in (data.get("missing_fields") or []) if str(item).strip()]
        title = str(data.get("title")).strip() if data.get("title") else None
        assignee = str(data.get("assignee")).strip() if data.get("assignee") else None
        note = str(data.get("note")).strip() if data.get("note") else None
        if not missing:
            for label, value in (
                ("عنوان", title), ("ددلاین", deadline), ("اولویت", priority), ("مسئول", assignee),
            ):
                if value is None or value == "":
                    missing.append(label)
        return ParsedAITask(title, deadline, priority, assignee, note, missing)

    async def parse_reminder(self, text: str, timezone: ZoneInfo, now: datetime) -> ParsedAIReminder | None:
        data = await self._chat_json(
            "متن فارسی کاربر را به یک یادآور تبدیل کن. JSON برگردان با کلیدهای "
            "title (عنوان کوتاه)، due_at (ISO8601 با منطقه زمانی)، repeat_type "
            "(null یا monthly). تاریخ‌های شمسی و عبارت‌های طبیعی مثل «۲۹ هر ماه»، "
            "«فردا»، «آخر هفته» و متن کارت را بفهم. اگر متن مربوط به تسک یا جلسه است null برگردان.",
            f"Now: {now.isoformat()}\nTimezone: {timezone}\nText:\n{text}",
        )
        if not data or not data.get("due_at"):
            return None
        try:
            due_at = datetime.fromisoformat(str(data["due_at"]))
            due_at = due_at.replace(tzinfo=timezone) if due_at.tzinfo is None else due_at.astimezone(timezone)
            title = str(data.get("title") or "یادآور").strip()[:200]
            repeat = str(data.get("repeat_type") or "").strip().lower() or None
            return ParsedAIReminder(title, due_at, "monthly" if repeat in {"monthly", "ماهانه"} else None)
        except (TypeError, ValueError):
            logger.exception("Could not parse AI reminder payload: %s", data)
            return None


def clean_transcript(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())
