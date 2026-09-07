from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum


class Priority(IntEnum):
    URGENT_IMPORTANT = 1
    IMPORTANT = 2
    URGENT = 3
    NEITHER = 4

    @property
    def label(self) -> str:
        return {
            1: "🔴 فوری و مهم",
            2: "🟠 مهم، غیرفوری",
            3: "🟡 فوری، کم‌اهمیت",
            4: "🟢 غیرفوری و کم‌اهمیت",
        }[self.value]


@dataclass(slots=True)
class Task:
    id: int
    user_id: int
    title: str
    deadline: datetime
    priority: Priority
    assignee: str
    progress: int
    last_note: str
    status: str
    created_at: datetime
    assignee_user_id: int | None = None
    cc_manager_id: int | None = None
    updated_at: datetime | None = None
    last_changed_by: int | None = None
    last_changed_by_name: str = ""


@dataclass(slots=True)
class Reminder:
    id: int
    user_id: int
    title: str
    due_at: datetime
    repeat_type: str
    repeat_until: datetime | None
    active: bool = True


@dataclass(slots=True)
class Meeting:
    id: int
    user_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    source_text: str
    recurrence_series_id: int | None = None
    recurrence_active: bool = False
