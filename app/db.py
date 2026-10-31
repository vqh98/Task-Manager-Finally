from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import aiosqlite

from app.models import Meeting, Priority, Reminder, Task


def _canonical_meeting_title(value: str) -> str:
    normalized = " ".join((value or "").replace("ي", "ی").replace("ك", "ک").split()).casefold()
    for prefix in ("جلسات ", "جلسه "):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
    return normalized


_TASK_COLUMNS = """
    t.id,t.user_id,t.title,t.deadline,t.priority,t.assignee,t.progress,t.last_note,
    t.status,t.created_at,t.assignee_user_id,t.cc_manager_id,t.updated_at,t.last_changed_by,
    COALESCE(
        NULLIF(mp.display_name,''),
        NULLIF(tm.display_name,''),
        NULLIF(ta.display_name,''),
        NULLIF(u.display_name,''),
        ''
    )
"""

_TASK_ACTOR_JOINS = """
    LEFT JOIN manager_profiles mp ON mp.user_id=t.last_changed_by
    LEFT JOIN team_members tm
        ON tm.manager_id=t.user_id AND tm.member_id=t.last_changed_by AND tm.active=1
    LEFT JOIN telegram_accounts ta ON ta.user_id=t.last_changed_by
    LEFT JOIN users u ON u.user_id=t.last_changed_by
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    @asynccontextmanager
    async def _connect(self):
        """Open SQLite with settings suitable for concurrent bot handlers."""
        async with aiosqlite.connect(self.path, timeout=30) as db:
            await db.execute("PRAGMA busy_timeout=30000")
            await db.execute("PRAGMA foreign_keys=ON")
            yield db

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            await db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    deadline TEXT NOT NULL,
                    priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 4),
                    assignee TEXT NOT NULL DEFAULT '',
                    progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
                    last_note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_changed_by INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_user_deadline
                    ON tasks(user_id, status, deadline, priority);
                CREATE TABLE IF NOT EXISTS task_deadline_alerts (
                    task_id INTEGER NOT NULL,
                    warning_days INTEGER NOT NULL CHECK(warning_days >= 0),
                    notified_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, warning_days)
                );
                CREATE INDEX IF NOT EXISTS idx_task_deadline_alerts_task
                    ON task_deadline_alerts(task_id);
                CREATE TABLE IF NOT EXISTS meetings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    source_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    repeat_type TEXT NOT NULL DEFAULT 'once',
                    repeat_until TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    last_notified_at TEXT,
                    last_warning_sent_for_due_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(user_id, active, due_at);
                CREATE INDEX IF NOT EXISTS idx_meetings_user_start
                    ON meetings(user_id, starts_at);
                CREATE TABLE IF NOT EXISTS meeting_recurrence_series (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    source_text TEXT NOT NULL DEFAULT '',
                    anchor_start TEXT NOT NULL,
                    anchor_end TEXT NOT NULL,
                    interval INTEGER NOT NULL DEFAULT 1,
                    weekdays TEXT,
                    frequency TEXT NOT NULL,
                    jalali_day INTEGER,
                    ends_on TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS meeting_recurrence_occurrences (
                    series_id INTEGER NOT NULL,
                    meeting_id INTEGER NOT NULL UNIQUE,
                    occurrence_at TEXT NOT NULL,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(series_id, occurrence_at)
                );
                CREATE INDEX IF NOT EXISTS idx_meeting_recurrence_user
                    ON meeting_recurrence_series(user_id, active);
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('owner', 'manager')),
                    display_name TEXT NOT NULL DEFAULT '',
                    access_granted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS access_codes (
                    code TEXT PRIMARY KEY COLLATE NOCASE,
                    active INTEGER NOT NULL DEFAULT 1,
                    usage_type TEXT NOT NULL DEFAULT 'single'
                        CHECK(usage_type IN ('single', 'multi')),
                    max_uses INTEGER NOT NULL DEFAULT 1 CHECK(max_uses > 0),
                    redeemed_by INTEGER,
                    redeemed_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS access_code_usages (
                    code TEXT NOT NULL COLLATE NOCASE,
                    user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    redeemed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(code, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_access_code_usages_code
                    ON access_code_usages(code, redeemed_at);
                CREATE TABLE IF NOT EXISTS team_members (
                    manager_id INTEGER NOT NULL,
                    member_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    profile_photo_file_id TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(manager_id, member_id)
                );
                CREATE INDEX IF NOT EXISTS idx_team_member ON team_members(member_id, active);
                CREATE TABLE IF NOT EXISTS manager_profiles (
                    user_id INTEGER PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS event_shares (
                    manager_id INTEGER NOT NULL,
                    member_id INTEGER NOT NULL,
                    permission TEXT NOT NULL CHECK(permission IN ('view', 'edit')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(manager_id, member_id)
                );
                CREATE INDEX IF NOT EXISTS idx_event_shares_member
                    ON event_shares(member_id, permission);
                CREATE TABLE IF NOT EXISTS task_shares (
                    manager_id INTEGER NOT NULL,
                    member_id INTEGER NOT NULL,
                    can_view INTEGER NOT NULL DEFAULT 0,
                    can_edit INTEGER NOT NULL DEFAULT 0,
                    can_add INTEGER NOT NULL DEFAULT 0,
                    can_delete INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(manager_id, member_id)
                );
                CREATE INDEX IF NOT EXISTS idx_task_shares_member
                    ON task_shares(member_id);
                CREATE TABLE IF NOT EXISTS shared_alert_settings (
                    member_id INTEGER NOT NULL,
                    manager_id INTEGER NOT NULL,
                    task_warning_days TEXT NOT NULL DEFAULT '1,3,7',
                    event_warning_minutes TEXT NOT NULL DEFAULT '60,1440',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(member_id, manager_id)
                );
                CREATE TABLE IF NOT EXISTS shared_alert_log (
                    member_id INTEGER NOT NULL,
                    manager_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    threshold INTEGER NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY(member_id,manager_id,kind,item_id,threshold)
                );
                CREATE TABLE IF NOT EXISTS task_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    manager_id INTEGER NOT NULL,
                    reporter_id INTEGER NOT NULL,
                    progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100),
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_task_reports_task ON task_reports(task_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS telegram_accounts (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT COLLATE NOCASE,
                    display_name TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS platform_accounts (
                    platform TEXT NOT NULL,
                    platform_user_id INTEGER NOT NULL,
                    canonical_user_id INTEGER NOT NULL,
                    username TEXT,
                    display_name TEXT NOT NULL DEFAULT '',
                    linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(platform, platform_user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_platform_accounts_canonical
                    ON platform_accounts(canonical_user_id, platform);
                CREATE TABLE IF NOT EXISTS platform_link_codes (
                    code TEXT PRIMARY KEY,
                    canonical_user_id INTEGER NOT NULL,
                    source_platform TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_platform_link_codes_expiry
                    ON platform_link_codes(expires_at, used_at);
                INSERT OR IGNORE INTO platform_accounts(
                    platform,platform_user_id,canonical_user_id,username,display_name
                )
                SELECT 'telegram',user_id,user_id,username,display_name FROM telegram_accounts;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_username
                    ON telegram_accounts(username) WHERE username IS NOT NULL;
                CREATE TABLE IF NOT EXISTS task_progress_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    manager_id INTEGER NOT NULL,
                    changed_by INTEGER NOT NULL,
                    progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100),
                    changed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_progress_history_task
                    ON task_progress_history(task_id, changed_at, id);
            """)
            task_columns = [row[1] for row in await (await db.execute("PRAGMA table_info(tasks)")).fetchall()]
            if "assignee_user_id" not in task_columns:
                await db.execute("ALTER TABLE tasks ADD COLUMN assignee_user_id INTEGER")
            if "cc_manager_id" not in task_columns:
                await db.execute("ALTER TABLE tasks ADD COLUMN cc_manager_id INTEGER")
            if "completed_at" not in task_columns:
                await db.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT")
            team_columns = [row[1] for row in await (await db.execute("PRAGMA table_info(team_members)")).fetchall()]
            if "profile_photo_file_id" not in team_columns:
                await db.execute("ALTER TABLE team_members ADD COLUMN profile_photo_file_id TEXT")
            if "report_warning_sent_at" not in task_columns:
                await db.execute("ALTER TABLE tasks ADD COLUMN report_warning_sent_at TEXT")
            if "last_changed_by" not in task_columns:
                await db.execute("ALTER TABLE tasks ADD COLUMN last_changed_by INTEGER")
            await db.execute(
                "UPDATE tasks SET last_changed_by=user_id WHERE last_changed_by IS NULL"
            )
            access_code_columns = {
                row[1] for row in await (await db.execute("PRAGMA table_info(access_codes)")).fetchall()
            }
            if "usage_type" not in access_code_columns:
                await db.execute(
                    "ALTER TABLE access_codes ADD COLUMN usage_type TEXT NOT NULL DEFAULT 'single'"
                )
            if "max_uses" not in access_code_columns:
                await db.execute(
                    "ALTER TABLE access_codes ADD COLUMN max_uses INTEGER NOT NULL DEFAULT 1"
                )
            await db.execute("""
                INSERT OR IGNORE INTO access_code_usages(code,user_id,redeemed_at)
                SELECT code,redeemed_by,COALESCE(redeemed_at,CURRENT_TIMESTAMP)
                FROM access_codes
                WHERE redeemed_by IS NOT NULL
            """)
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    digest_hour INTEGER,
                    digest_minute INTEGER,
                    morning_hour INTEGER,
                    morning_minute INTEGER,
                    digest_enabled INTEGER NOT NULL DEFAULT 1,
                    morning_enabled INTEGER NOT NULL DEFAULT 1,
                    deadline_warning_days INTEGER,
                    report_warning_hours INTEGER,
                    deadline_warning_days_list TEXT,
                    reminder_warning_days_list TEXT,
                    meeting_default_duration INTEGER NOT NULL DEFAULT 60,
                    meeting_window_start INTEGER NOT NULL DEFAULT 480,
                    meeting_window_end INTEGER NOT NULL DEFAULT 1140,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS meeting_quiet_hours (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                    start_minute INTEGER NOT NULL CHECK(start_minute BETWEEN 0 AND 1439),
                    end_minute INTEGER NOT NULL CHECK(end_minute BETWEEN 1 AND 1440),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, CHECK(start_minute < end_minute)
                );
                CREATE INDEX IF NOT EXISTS idx_meeting_quiet_hours_user ON meeting_quiet_hours(user_id,start_minute);
                CREATE TABLE IF NOT EXISTS notification_log (
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    sent_date TEXT NOT NULL,
                    PRIMARY KEY(user_id, kind, sent_date)
                );
                CREATE TABLE IF NOT EXISTS app_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS recurrence_series (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL, title TEXT NOT NULL,
                    anchor TEXT NOT NULL, priority INTEGER NOT NULL,
                    assignee TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
                    interval INTEGER NOT NULL DEFAULT 1, weekdays TEXT,
                    frequency TEXT NOT NULL, jalali_day INTEGER, ends_on TEXT,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS recurrence_occurrences (
                    series_id INTEGER NOT NULL, task_id INTEGER NOT NULL,
                    occurrence_at TEXT NOT NULL, skipped INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(series_id, occurrence_at)
                );
            """)
            user_setting_columns = [
                row[1] for row in await (await db.execute("PRAGMA table_info(user_settings)")).fetchall()
            ]
            if "digest_enabled" not in user_setting_columns:
                await db.execute("ALTER TABLE user_settings ADD COLUMN digest_enabled INTEGER NOT NULL DEFAULT 1")
            if "morning_enabled" not in user_setting_columns:
                await db.execute("ALTER TABLE user_settings ADD COLUMN morning_enabled INTEGER NOT NULL DEFAULT 1")
            if "deadline_warning_days_list" not in user_setting_columns:
                await db.execute("ALTER TABLE user_settings ADD COLUMN deadline_warning_days_list TEXT")
            if "reminder_warning_days_list" not in user_setting_columns:
                await db.execute("ALTER TABLE user_settings ADD COLUMN reminder_warning_days_list TEXT")
            if "reminder_warning_enabled" not in user_setting_columns:
                await db.execute("ALTER TABLE user_settings ADD COLUMN reminder_warning_enabled INTEGER NOT NULL DEFAULT 0")
            if "reminder_warning_hour" not in user_setting_columns:
                await db.execute("ALTER TABLE user_settings ADD COLUMN reminder_warning_hour INTEGER")
            if "reminder_warning_minute" not in user_setting_columns:
                await db.execute("ALTER TABLE user_settings ADD COLUMN reminder_warning_minute INTEGER")
            if "meeting_default_duration" not in user_setting_columns:
                await db.execute(
                    "ALTER TABLE user_settings ADD COLUMN meeting_default_duration INTEGER NOT NULL DEFAULT 60"
                )
            if "meeting_window_start" not in user_setting_columns:
                await db.execute(
                    "ALTER TABLE user_settings ADD COLUMN meeting_window_start INTEGER NOT NULL DEFAULT 480"
                )
            if "meeting_window_end" not in user_setting_columns:
                await db.execute(
                    "ALTER TABLE user_settings ADD COLUMN meeting_window_end INTEGER NOT NULL DEFAULT 1140"
                )
            reminder_columns = [row[1] for row in await (await db.execute("PRAGMA table_info(reminders)")).fetchall()]
            if "last_warning_sent_for_due_at" not in reminder_columns:
                await db.execute("ALTER TABLE reminders ADD COLUMN last_warning_sent_for_due_at TEXT")
            # Older versions accidentally mirrored every task into reminders.
            # Remove those legacy one-shot mirrors exactly once; independent
            # reminders created afterwards must never be touched.
            cleanup_marker = await (await db.execute(
                "SELECT 1 FROM app_migrations WHERE name=?",
                ("remove_task_reminder_mirrors",),
            )).fetchone()
            if not cleanup_marker:
                await db.execute("""
                    DELETE FROM reminders
                    WHERE repeat_type='once'
                      AND EXISTS (
                          SELECT 1 FROM tasks t
                          WHERE t.user_id=reminders.user_id
                            AND t.title=reminders.title
                            AND t.deadline=reminders.due_at
                      )
                """)
                await db.execute(
                    "INSERT INTO app_migrations(name) VALUES(?)",
                    ("remove_task_reminder_mirrors",),
                )
            # Existing databases predate progress history and completion timestamps.
            # Seed one baseline point per old task so its chart is immediately useful.
            await db.execute("""
                INSERT INTO task_progress_history(task_id,manager_id,changed_by,progress,changed_at)
                SELECT t.id,t.user_id,t.user_id,t.progress,COALESCE(t.updated_at,t.created_at)
                FROM tasks AS t
                WHERE NOT EXISTS (
                    SELECT 1 FROM task_progress_history AS h WHERE h.task_id=t.id
                )
            """)
            await db.execute("""
                UPDATE tasks SET completed_at=COALESCE(completed_at,updated_at,created_at)
                WHERE status='done' AND completed_at IS NULL
            """)
            # Google Calendar integration is deliberately kept in separate tables:
            # meeting rows remain provider-agnostic and OAuth secrets can be removed
            # without touching the user's schedule.
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS google_connections (
                    user_id INTEGER PRIMARY KEY,
                    encrypted_refresh_token TEXT NOT NULL,
                    account_email TEXT NOT NULL DEFAULT '',
                    calendar_id TEXT NOT NULL DEFAULT 'primary',
                    calendar_name TEXT NOT NULL DEFAULT 'تقویم اصلی',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    connected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS google_oauth_states (
                    state TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS google_meeting_links (
                    meeting_id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    calendar_id TEXT NOT NULL,
                    google_event_id TEXT NOT NULL,
                    synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_google_links_user
                    ON google_meeting_links(user_id,calendar_id);
                CREATE TABLE IF NOT EXISTS google_sync_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    meeting_id INTEGER NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('upsert','delete')),
                    calendar_id TEXT NOT NULL DEFAULT '',
                    google_event_id TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id,meeting_id,action,calendar_id)
                );
                CREATE INDEX IF NOT EXISTS idx_google_sync_ready
                    ON google_sync_queue(available_at,id);
            """)
            await db.commit()

    async def seed_owners(self, user_ids: frozenset[int]) -> None:
        async with self._connect() as db:
            await db.executemany(
                "INSERT OR IGNORE INTO users(user_id, role) VALUES(?, 'owner')",
                [(user_id,) for user_id in user_ids],
            )
            await db.commit()

    async def user_role(self, user_id: int) -> str | None:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT role FROM users WHERE user_id=?", (user_id,)
            )).fetchone()
        return row[0] if row else None

    async def user_display_name(self, user_id: int) -> str:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT display_name FROM manager_profiles WHERE user_id=?", (user_id,)
            )).fetchone()
        return (row[0] or "").strip() if row else ""

    async def set_user_display_name(self, user_id: int, display_name: str) -> bool:
        name = " ".join(display_name.split()).strip()[:80]
        if not name:
            return False
        async with self._connect() as db:
            user = await (await db.execute(
                "SELECT 1 FROM users WHERE user_id=?", (user_id,)
            )).fetchone()
            if not user:
                return False
            await db.execute("""
                INSERT INTO manager_profiles(user_id,display_name) VALUES(?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name=excluded.display_name,updated_at=CURRENT_TIMESTAMP
            """, (user_id, name))
            await db.commit()
        return True
    async def is_authorized(self, user_id: int, now: datetime) -> bool:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT role,access_granted FROM users WHERE user_id=?", (user_id,)
            )).fetchone()
        return bool(row and (row[0] == "owner" or row[1]))

    async def grant_access(self, user_id: int, display_name: str) -> None:
        async with self._connect() as db:
            await db.execute("""
                INSERT INTO users(user_id,role,display_name,access_granted)
                VALUES(?, 'manager', ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name=excluded.display_name,access_granted=1
            """, (user_id, display_name))
            await db.commit()

    async def add_manager(self, user_id: int, display_name: str = "") -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT OR IGNORE INTO users(user_id, role, display_name) VALUES(?, 'manager', ?)",
                (user_id, display_name),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def managers(self) -> list[tuple[int, str]]:
        async with self._connect() as db:
            rows = await (await db.execute(
                "SELECT user_id, display_name FROM users WHERE role='manager' ORDER BY created_at"
            )).fetchall()
        return [(row[0], row[1]) for row in rows]

    async def remove_manager(self, user_id: int) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM users WHERE user_id=? AND role='manager'", (user_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def add_team_member(self, manager_id: int, member_id: int, display_name: str,
                              profile_photo_file_id: str | None = None) -> None:
        async with self._connect() as db:
            await db.execute("""
                INSERT INTO team_members(manager_id,member_id,display_name,profile_photo_file_id,active)
                VALUES(?,?,?,?,1)
                ON CONFLICT(manager_id,member_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    profile_photo_file_id=COALESCE(excluded.profile_photo_file_id,team_members.profile_photo_file_id),
                    active=1
            """, (manager_id, member_id, display_name, profile_photo_file_id))
            await db.commit()

    async def team_member(self, manager_id: int, member_id: int) -> tuple[int, str, str | None] | None:
        async with self._connect() as db:
            row = await (await db.execute("""
                SELECT member_id,display_name,profile_photo_file_id FROM team_members
                WHERE manager_id=? AND member_id=? AND active=1
            """, (manager_id, member_id))).fetchone()
        return (row[0], row[1], row[2]) if row else None

    async def team_members(self, manager_id: int) -> list[tuple[int, str]]:
        async with self._connect() as db:
            rows = await (await db.execute(
                "SELECT member_id,display_name FROM team_members WHERE manager_id=? AND active=1 ORDER BY display_name,member_id",
                (manager_id,),
            )).fetchall()
        return [(row[0], row[1]) for row in rows]

    async def set_event_share(self, manager_id: int, member_id: int,
                              permission: str | None) -> bool:
        if permission not in {None, "view", "edit"}:
            raise ValueError("سطح دسترسی ایونت نامعتبر است")
        async with self._connect() as db:
            member = await (await db.execute(
                "SELECT 1 FROM team_members WHERE manager_id=? AND member_id=? AND active=1",
                (manager_id, member_id),
            )).fetchone()
            if not member:
                return False
            if permission is None:
                await db.execute(
                    "DELETE FROM event_shares WHERE manager_id=? AND member_id=?",
                    (manager_id, member_id),
                )
            else:
                await db.execute("""
                    INSERT INTO event_shares(manager_id,member_id,permission)
                    VALUES(?,?,?)
                    ON CONFLICT(manager_id,member_id) DO UPDATE SET
                        permission=excluded.permission,updated_at=CURRENT_TIMESTAMP
                """, (manager_id, member_id, permission))
            await db.commit()
        return True

    async def event_share_permission(self, manager_id: int, member_id: int) -> str | None:
        async with self._connect() as db:
            row = await (await db.execute("""
                SELECT es.permission FROM event_shares es
                INNER JOIN team_members tm ON tm.manager_id=es.manager_id
                    AND tm.member_id=es.member_id AND tm.active=1
                WHERE es.manager_id=? AND es.member_id=?
            """, (manager_id, member_id))).fetchone()
        return row[0] if row else None

    async def event_share_members(self, manager_id: int) -> list[tuple[int, str, str | None]]:
        async with self._connect() as db:
            rows = await (await db.execute("""
                SELECT tm.member_id,tm.display_name,es.permission
                FROM team_members tm LEFT JOIN event_shares es
                  ON es.manager_id=tm.manager_id AND es.member_id=tm.member_id
                WHERE tm.manager_id=? AND tm.active=1
                ORDER BY tm.display_name,tm.member_id
            """, (manager_id,))).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    async def shared_event_managers(self, member_id: int) -> list[tuple[int, str, str]]:
        async with self._connect() as db:
            rows = await (await db.execute("""
                SELECT es.manager_id,COALESCE(mp.display_name,u.display_name,''),es.permission
                FROM event_shares es
                INNER JOIN team_members tm ON tm.manager_id=es.manager_id
                    AND tm.member_id=es.member_id AND tm.active=1
                LEFT JOIN users u ON u.user_id=es.manager_id
                LEFT JOIN manager_profiles mp ON mp.user_id=es.manager_id
                WHERE es.member_id=? ORDER BY es.created_at
            """, (member_id,))).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    async def set_task_share(self, manager_id: int, member_id: int, **permissions: bool) -> bool:
        allowed = {"can_view", "can_edit", "can_add", "can_delete"}
        if set(permissions) - allowed:
            raise ValueError("invalid task share permission")
        async with self._connect() as db:
            member = await (await db.execute(
                "SELECT 1 FROM team_members WHERE manager_id=? AND member_id=? AND active=1",
                (manager_id, member_id),
            )).fetchone()
            if not member:
                return False
            current = await (await db.execute(
                "SELECT can_view,can_edit,can_add,can_delete FROM task_shares WHERE manager_id=? AND member_id=?",
                (manager_id, member_id),
            )).fetchone()
            values = list(current or (0, 0, 0, 0))
            names = ["can_view", "can_edit", "can_add", "can_delete"]
            for key, value in permissions.items():
                values[names.index(key)] = int(bool(value))
            if not any(values):
                await db.execute("DELETE FROM task_shares WHERE manager_id=? AND member_id=?", (manager_id, member_id))
            else:
                await db.execute("""
                    INSERT INTO task_shares(manager_id,member_id,can_view,can_edit,can_add,can_delete)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(manager_id,member_id) DO UPDATE SET
                      can_view=excluded.can_view,can_edit=excluded.can_edit,
                      can_add=excluded.can_add,can_delete=excluded.can_delete,
                      updated_at=CURRENT_TIMESTAMP
                """, (manager_id, member_id, *values))
            await db.commit()
        return True

    async def task_share_permissions(self, manager_id: int, member_id: int) -> dict[str, bool] | None:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT can_view,can_edit,can_add,can_delete FROM task_shares "
                "WHERE manager_id=? AND member_id=? AND EXISTS "
                "(SELECT 1 FROM team_members tm WHERE tm.manager_id=task_shares.manager_id "
                "AND tm.member_id=task_shares.member_id AND tm.active=1)",
                (manager_id, member_id),
            )).fetchone()
        if not row:
            return None
        return dict(zip(("can_view", "can_edit", "can_add", "can_delete"), map(bool, row)))

    async def task_share_members(self, manager_id: int) -> list[tuple[int, str, dict[str, bool] | None]]:
        async with self._connect() as db:
            rows = await (await db.execute("""
                SELECT tm.member_id,tm.display_name,ts.can_view,ts.can_edit,ts.can_add,ts.can_delete
                FROM team_members tm LEFT JOIN task_shares ts
                  ON ts.manager_id=tm.manager_id AND ts.member_id=tm.member_id
                WHERE tm.manager_id=? AND tm.active=1 ORDER BY tm.display_name,tm.member_id
            """, (manager_id,))).fetchall()
        result = []
        for row in rows:
            perms = None if row[2] is None else dict(zip(
                ("can_view", "can_edit", "can_add", "can_delete"), map(bool, row[2:6])
            ))
            result.append((row[0], row[1], perms))
        return result

    async def shared_task_managers(self, member_id: int) -> list[tuple[int, str, bool, bool, bool, bool]]:
        async with self._connect() as db:
            rows = await (await db.execute("""
                SELECT ts.manager_id,COALESCE(mp.display_name,u.display_name,''),
                       ts.can_view,ts.can_edit,ts.can_add,ts.can_delete
                FROM task_shares ts
                INNER JOIN team_members tm ON tm.manager_id=ts.manager_id
                    AND tm.member_id=ts.member_id AND tm.active=1
                LEFT JOIN users u ON u.user_id=ts.manager_id
                LEFT JOIN manager_profiles mp ON mp.user_id=ts.manager_id
                WHERE ts.member_id=? ORDER BY ts.created_at
            """, (member_id,))).fetchall()
        return [(r[0], r[1], bool(r[2]), bool(r[3]), bool(r[4]), bool(r[5])) for r in rows]

    async def shared_alert_settings(self, member_id: int, manager_id: int) -> dict[str, object]:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT task_warning_days,event_warning_minutes,enabled FROM shared_alert_settings "
                "WHERE member_id=? AND manager_id=?", (member_id, manager_id)
            )).fetchone()
        if not row:
            return {"task_warning_days": [1, 3, 7], "event_warning_minutes": [60, 1440], "enabled": True}
        parse = lambda value: sorted({int(x) for x in str(value).split(",") if x != ""}, reverse=True)
        return {"task_warning_days": parse(row[0]), "event_warning_minutes": parse(row[1]), "enabled": bool(row[2])}

    async def update_shared_alert_settings(self, member_id: int, manager_id: int,
                                           task_days: list[int], event_minutes: list[int],
                                           enabled: bool = True) -> None:
        clean_days = sorted({max(0, min(365, int(x))) for x in task_days}, reverse=True)
        clean_minutes = sorted({max(5, min(10080, int(x))) for x in event_minutes}, reverse=True)
        async with self._connect() as db:
            await db.execute("""
                INSERT INTO shared_alert_settings(member_id,manager_id,task_warning_days,event_warning_minutes,enabled)
                VALUES(?,?,?,?,?)
                ON CONFLICT(member_id,manager_id) DO UPDATE SET
                  task_warning_days=excluded.task_warning_days,
                  event_warning_minutes=excluded.event_warning_minutes,
                  enabled=excluded.enabled,updated_at=CURRENT_TIMESTAMP
            """, (member_id, manager_id, ",".join(map(str, clean_days)),
                  ",".join(map(str, clean_minutes)), int(enabled)))
            await db.commit()

    async def due_shared_alerts(self, now: datetime) -> list[tuple[int, int, str, object, int]]:
        result = []
        async with self._connect() as db:
            rows = await (await db.execute("""
                SELECT s.member_id,s.manager_id,'task',t.id,t.title,t.deadline,s.task_warning_days
                FROM shared_alert_settings s
                JOIN task_shares ts ON ts.manager_id=s.manager_id AND ts.member_id=s.member_id AND ts.can_view=1
                JOIN tasks t ON t.user_id=s.manager_id AND t.status!='done'
                WHERE s.enabled=1
            """)).fetchall()
            for member_id, manager_id, _, item_id, title, due_s, thresholds in rows:
                due = datetime.fromisoformat(due_s)
                for threshold in (int(x) for x in str(thresholds).split(",") if x):
                    if now <= due <= now + timedelta(days=threshold):
                        sent = await (await db.execute(
                            "SELECT 1 FROM shared_alert_log WHERE member_id=? AND manager_id=? AND kind='task' AND item_id=? AND threshold=?",
                            (member_id, manager_id, item_id, threshold),
                        )).fetchone()
                        if not sent:
                            result.append((member_id, manager_id, "task", (item_id, title, due), threshold))
            rows = await (await db.execute("""
                SELECT s.member_id,s.manager_id,'event',m.id,m.title,m.starts_at,m.ends_at,s.event_warning_minutes
                FROM shared_alert_settings s
                JOIN event_shares es ON es.manager_id=s.manager_id AND es.member_id=s.member_id
                JOIN meetings m ON m.user_id=s.manager_id
                WHERE s.enabled=1
            """)).fetchall()
            for member_id, manager_id, _, item_id, title, start_s, end_s, thresholds in rows:
                starts = datetime.fromisoformat(start_s)
                for threshold in (int(x) for x in str(thresholds).split(",") if x):
                    if now <= starts <= now + timedelta(minutes=threshold):
                        sent = await (await db.execute(
                            "SELECT 1 FROM shared_alert_log WHERE member_id=? AND manager_id=? AND kind='event' AND item_id=? AND threshold=?",
                            (member_id, manager_id, item_id, threshold),
                        )).fetchone()
                        if not sent:
                            result.append((member_id, manager_id, "event", (item_id, title, starts, datetime.fromisoformat(end_s)), threshold))
        return result

    async def mark_shared_alert_sent(self, member_id: int, manager_id: int, kind: str,
                                     item_id: int, threshold: int, now: datetime) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO shared_alert_log(member_id,manager_id,kind,item_id,threshold,sent_at) VALUES(?,?,?,?,?,?)",
                (member_id, manager_id, kind, item_id, threshold, now.isoformat()),
            )
            await db.commit()
    async def update_team_member_photo(self, manager_id: int, member_id: int,
                                       profile_photo_file_id: str | None) -> None:
        async with self._connect() as db:
            await db.execute("""UPDATE team_members SET profile_photo_file_id=?
                WHERE manager_id=? AND member_id=? AND active=1""",
                (profile_photo_file_id, manager_id, member_id))
            await db.commit()

    async def remember_telegram_account(self, user_id: int, username: str | None, display_name: str) -> None:
        async with self._connect() as db:
            await db.execute("""INSERT INTO telegram_accounts(user_id,username,display_name)
                VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                display_name=excluded.display_name,updated_at=CURRENT_TIMESTAMP""",
                (user_id, username.lower() if username else None, display_name))
            await db.commit()

    async def canonical_platform_user_id(self, platform: str, platform_user_id: int) -> int:
        """Return the shared account id; unlinked non-Telegram users get a safe namespace."""
        platform = platform.strip().lower()
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT canonical_user_id FROM platform_accounts WHERE platform=? AND platform_user_id=?",
                (platform, platform_user_id),
            )).fetchone()
            if row:
                return int(row[0])
            canonical = platform_user_id if platform == "telegram" else 9_000_000_000_000_000 + platform_user_id
            await db.execute(
                "INSERT OR IGNORE INTO platform_accounts(platform,platform_user_id,canonical_user_id) VALUES(?,?,?)",
                (platform, platform_user_id, canonical),
            )
            await db.commit()
            return canonical

    async def remember_platform_account(self, platform: str, platform_user_id: int,
                                        username: str | None, display_name: str) -> int:
        canonical = await self.canonical_platform_user_id(platform, platform_user_id)
        async with self._connect() as db:
            await db.execute("""
                UPDATE platform_accounts SET username=?,display_name=?,updated_at=CURRENT_TIMESTAMP
                WHERE platform=? AND platform_user_id=?
            """, (username.lower() if username else None, display_name, platform, platform_user_id))
            await db.commit()
        return canonical

    async def platform_recipient(self, platform: str, canonical_user_id: int) -> int | None:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT platform_user_id FROM platform_accounts WHERE platform=? AND canonical_user_id=?",
                (platform.strip().lower(), canonical_user_id),
            )).fetchone()
        return int(row[0]) if row else None

    async def platform_link_status(self, canonical_user_id: int) -> list[str]:
        async with self._connect() as db:
            rows = await (await db.execute(
                "SELECT platform FROM platform_accounts WHERE canonical_user_id=? ORDER BY platform",
                (canonical_user_id,),
            )).fetchall()
        return [str(row[0]) for row in rows]

    async def create_platform_link_code(self, canonical_user_id: int, source_platform: str,
                                        code: str, expires_at: datetime) -> None:
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM platform_link_codes WHERE canonical_user_id=? OR expires_at<=? OR used_at IS NOT NULL",
                (canonical_user_id, datetime.now(timezone.utc).isoformat()),
            )
            await db.execute("""
                INSERT INTO platform_link_codes(code,canonical_user_id,source_platform,expires_at)
                VALUES(?,?,?,?)
            """, (code, canonical_user_id, source_platform, expires_at.isoformat()))
            await db.commit()

    async def consume_platform_link_code(self, code: str, target_platform: str,
                                         target_user_id: int, username: str | None,
                                         display_name: str) -> int | None:
        """Atomically consume a valid cross-platform code and return the shared account id."""
        now = datetime.now(timezone.utc).isoformat()
        target_platform = target_platform.strip().lower()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("""
                SELECT canonical_user_id,source_platform FROM platform_link_codes
                WHERE code=? AND used_at IS NULL AND expires_at>?
            """, (code, now))).fetchone()
            if not row or row[1] == target_platform:
                await db.rollback()
                return None
            canonical = int(row[0])
            await db.execute("""
                INSERT INTO platform_accounts(platform,platform_user_id,canonical_user_id,username,display_name)
                VALUES(?,?,?,?,?)
                ON CONFLICT(platform,platform_user_id) DO UPDATE SET
                    canonical_user_id=excluded.canonical_user_id,username=excluded.username,
                    display_name=excluded.display_name,linked_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
            """, (target_platform, target_user_id, canonical,
                  username.lower() if username else None, display_name))
            await db.execute("UPDATE platform_link_codes SET used_at=? WHERE code=?", (now, code))
            await db.commit()
            return canonical

    async def unlink_platform(self, canonical_user_id: int, platform: str) -> bool:
        """Detach a non-Telegram endpoint while preserving the canonical Telegram data."""
        if platform == "telegram":
            return False
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM platform_accounts WHERE canonical_user_id=? AND platform=?",
                (canonical_user_id, platform),
            )
            await db.commit()
            return bool(cursor.rowcount)

    async def telegram_account_by_username(self, username: str) -> tuple[int, str] | None:
        clean = username.strip().lstrip("@").lower()
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT user_id,display_name FROM telegram_accounts WHERE username=?", (clean,)
            )).fetchone()
        return (row[0], row[1]) if row else None

    async def user_settings(self, user_id: int, defaults: dict[str, int]) -> dict[str, int]:
        async with self._connect() as db:
            row = await (await db.execute("""
                SELECT digest_hour,digest_minute,morning_hour,morning_minute,
                       deadline_warning_days,report_warning_hours,digest_enabled,morning_enabled,
                       deadline_warning_days_list,reminder_warning_days_list,
                       reminder_warning_enabled,reminder_warning_hour,reminder_warning_minute,
                       meeting_default_duration,meeting_window_start,meeting_window_end
                FROM user_settings WHERE user_id=?
            """, (user_id,))).fetchone()
        if not row:
            return dict(defaults)
        keys = ("digest_hour", "digest_minute", "morning_hour", "morning_minute",
                "deadline_warning_days", "report_warning_hours", "digest_enabled", "morning_enabled",
                "deadline_warning_days_list", "reminder_warning_days_list",
                "reminder_warning_enabled", "reminder_warning_hour", "reminder_warning_minute",
                "meeting_default_duration", "meeting_window_start", "meeting_window_end")
        result = dict(defaults)
        for index, key in enumerate(keys):
            if row[index] is not None:
                result[key] = row[index]
        return result

    async def update_user_setting(self, user_id: int, key: str, value: int) -> None:
        allowed = {
            "digest_hour", "digest_minute", "morning_hour", "morning_minute",
            "deadline_warning_days", "report_warning_hours",
            "digest_enabled", "morning_enabled", "reminder_warning_enabled",
            "reminder_warning_hour", "reminder_warning_minute", "meeting_default_duration",
            "meeting_window_start", "meeting_window_end",
        }
        if key not in allowed:
            raise ValueError("تنظیم نامعتبر")
        async with self._connect() as db:
            await db.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)", (user_id,))
            await db.execute(
                f"UPDATE user_settings SET {key}=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                (value, user_id),
            )
            await db.commit()

    async def disable_meeting_notifications(self, user_id: int) -> None:
        """Stop morning/digest retries after Telegram reports a blocked bot."""
        async with self._connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)", (user_id,)
            )
            await db.execute(
                """UPDATE user_settings
                   SET digest_enabled=0, morning_enabled=0,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE user_id=?""",
                (user_id,),
            )
            await db.commit()

    async def update_meeting_window(self, user_id: int, start_minute: int, end_minute: int) -> None:
        start_minute = int(start_minute)
        end_minute = int(end_minute)
        if not (0 <= start_minute < end_minute <= 1440):
            raise ValueError("بازهٔ زمانی جلسه نامعتبر است")
        if start_minute % 60 or end_minute % 60:
            raise ValueError("بازهٔ پیشنهاد جلسه باید روی ساعت کامل باشد")
        async with self._connect() as db:
            await db.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)", (user_id,))
            await db.execute(
                """UPDATE user_settings
                   SET meeting_window_start=?, meeting_window_end=?, updated_at=CURRENT_TIMESTAMP
                   WHERE user_id=?""",
                (start_minute, end_minute, user_id),
            )
            await db.commit()

    async def update_warning_days(self, user_id: int, kind: str, days: list[int]) -> None:
        if kind not in {"deadline", "reminder"}:
            raise ValueError("نوع هشدار نامعتبر است")
        clean = sorted({int(x) for x in days if 0 <= int(x) <= 365}, reverse=True)
        async with self._connect() as db:
            await db.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)", (user_id,))
            await db.execute(f"UPDATE user_settings SET {kind}_warning_days_list=? WHERE user_id=?", (','.join(map(str, clean)), user_id))
            await db.commit()

    async def meeting_quiet_hours(self, user_id: int) -> list[tuple[int, int, int]]:
        async with self._connect() as db:
            rows = await (await db.execute("SELECT id,start_minute,end_minute FROM meeting_quiet_hours WHERE user_id=? ORDER BY start_minute", (user_id,))).fetchall()
        return [(int(r[0]), int(r[1]), int(r[2])) for r in rows]

    async def add_meeting_quiet_hour(self, user_id: int, start_minute: int, end_minute: int) -> int:
        if not (0 <= start_minute < end_minute <= 1440): raise ValueError("invalid quiet range")
        async with self._connect() as db:
            cursor = await db.execute("INSERT INTO meeting_quiet_hours(user_id,start_minute,end_minute) VALUES(?,?,?)", (user_id,start_minute,end_minute))
            await db.commit(); return int(cursor.lastrowid)

    async def delete_meeting_quiet_hour(self, quiet_id: int, user_id: int) -> bool:
        async with self._connect() as db:
            cursor = await db.execute("DELETE FROM meeting_quiet_hours WHERE id=? AND user_id=?", (quiet_id,user_id))
            await db.commit(); return cursor.rowcount > 0

    async def notification_sent_today(self, user_id: int, kind: str, day: date) -> bool:
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT 1 FROM notification_log WHERE user_id=? AND kind=? AND sent_date=?",
                (user_id, kind, day.isoformat()),
            )).fetchone()
        return row is not None

    async def mark_notification_sent(self, user_id: int, kind: str, day: date) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO notification_log(user_id,kind,sent_date) VALUES(?,?,?)",
                (user_id, kind, day.isoformat()),
            )
            await db.commit()

    async def users_for_schedule(
        self, kind: str, hour: int, minute: int,
        default_hour: int, default_minute: int,
    ) -> list[int]:
        if kind not in {"digest", "morning"}:
            raise ValueError("نوع اعلان نامعتبر است")
        hour_column = "digest_hour" if kind == "digest" else "morning_hour"
        minute_column = "digest_minute" if kind == "digest" else "morning_minute"
        enabled_column = "digest_enabled" if kind == "digest" else "morning_enabled"
        async with self._connect() as db:
            rows = await (await db.execute(f"""
                SELECT u.user_id FROM users u
                LEFT JOIN user_settings s ON s.user_id=u.user_id
                WHERE (u.role='owner' OR u.access_granted=1)
                AND COALESCE(s.{enabled_column},1)=1
                AND COALESCE(s.{hour_column},?)=?
                AND COALESCE(s.{minute_column},?)=?
            """, (
                default_hour, hour, default_minute, minute,
            ))).fetchall()
        return [row[0] for row in rows]
    async def stale_report_tasks(self, now: datetime, hours: int) -> list[tuple[Task, datetime]]:
        cutoff = (now - timedelta(hours=hours)).isoformat()
        warn_cutoff = (now - timedelta(hours=24)).isoformat()
        async with self._connect() as db:
            rows = await (await db.execute("""
                SELECT {cols},
                       COALESCE((SELECT MAX(tr.created_at) FROM task_reports tr WHERE tr.task_id=t.id),
                                 t.created_at) AS last_report_at
                FROM tasks t
                {joins}
                INNER JOIN users owner_user ON owner_user.user_id=t.user_id
                WHERE t.status='open' AND t.assignee_user_id IS NOT NULL
                  AND COALESCE((SELECT MAX(tr.created_at) FROM task_reports tr WHERE tr.task_id=t.id),
                               t.created_at) <= ?
                  AND (t.report_warning_sent_at IS NULL OR t.report_warning_sent_at <= ?)
                   AND (owner_user.role='owner' OR owner_user.access_granted=1)
            """.format(cols=_TASK_COLUMNS, joins=_TASK_ACTOR_JOINS), (cutoff, warn_cutoff))).fetchall()
        result = []
        for row in rows:
            task = self._task_from_row(row)
            result.append((task, datetime.fromisoformat(row[15])))
        return result

    async def mark_report_warned(self, task_id: int, now: datetime) -> None:
        async with self._connect() as db:
            await db.execute("UPDATE tasks SET report_warning_sent_at=? WHERE id=?",
                             (now.isoformat(), task_id))
            await db.commit()

        async with self._connect() as db:
            rows = await (await db.execute(
                "SELECT member_id,display_name FROM team_members WHERE manager_id=? AND active=1 ORDER BY display_name,member_id",
                (manager_id,),
            )).fetchall()
        return [(row[0], row[1]) for row in rows]

    async def remove_team_member(self, manager_id: int, member_id: int) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE team_members SET active=0 WHERE manager_id=? AND member_id=? AND active=1",
                (manager_id, member_id),
            )
            if cursor.rowcount:
                await db.execute(
                    "DELETE FROM event_shares WHERE manager_id=? AND member_id=?",
                    (manager_id, member_id),
                )
            await db.commit()
        return cursor.rowcount > 0

    async def member_managers(self, member_id: int) -> list[tuple[int, str]]:
        async with self._connect() as db:
            rows = await (await db.execute("""
                SELECT tm.manager_id,COALESCE(u.display_name,'') FROM team_members tm
                LEFT JOIN users u ON u.user_id=tm.manager_id
                WHERE tm.member_id=? AND tm.active=1 ORDER BY tm.created_at
            """, (member_id,))).fetchall()
        return [(row[0], row[1]) for row in rows]

    async def assigned_tasks(self, member_id: int, include_done: bool = False) -> list[Task]:
        query = f"""SELECT {_TASK_COLUMNS}
                    FROM tasks t {_TASK_ACTOR_JOINS}
                    WHERE t.assignee_user_id=?"""
        if not include_done:
            query += " AND t.status!='done'"
        query += " ORDER BY t.priority,t.deadline"
        async with self._connect() as db:
            rows = await (await db.execute(query, (member_id,))).fetchall()
        return [self._task_from_row(row) for row in rows]

    async def assigned_task(self, task_id: int, member_id: int) -> Task | None:
        tasks = await self.assigned_tasks(member_id, include_done=True)
        return next((task for task in tasks if task.id == task_id), None)

    async def add_task_report(self, task_id: int, manager_id: int, reporter_id: int,
                              progress: int, note: str) -> int:
        now = datetime.now().astimezone().isoformat()
        async with self._connect() as db:
            cursor = await db.execute("""
                INSERT INTO task_reports(task_id,manager_id,reporter_id,progress,note)
                SELECT id,user_id,?,?,? FROM tasks
                WHERE id=? AND user_id=? AND assignee_user_id=?
            """, (reporter_id, progress, note, task_id, manager_id, reporter_id))
            if cursor.rowcount:
                await db.execute("""UPDATE tasks SET progress=?,last_note=?,status=?,
                                      updated_at=?,last_changed_by=?
                                   WHERE id=? AND user_id=?""",
                                 (progress, note, "done" if progress == 100 else "open",
                                  now, reporter_id, task_id, manager_id))
                await db.execute("""INSERT INTO task_progress_history
                    (task_id,manager_id,changed_by,progress,changed_at) VALUES(?,?,?,?,?)""",
                    (task_id, manager_id, reporter_id, progress, now))
                if progress == 100:
                    await db.execute("UPDATE tasks SET completed_at=? WHERE id=?", (now, task_id))
            await db.commit()
        return cursor.lastrowid if cursor.rowcount else 0

    async def task_reports(self, task_id: int, manager_id: int, limit: int = 10) -> list[tuple[int, int, str, datetime]]:
        async with self._connect() as db:
            rows = await (await db.execute("""
                SELECT reporter_id,progress,note,created_at FROM task_reports
                WHERE task_id=? AND manager_id=? ORDER BY id DESC LIMIT ?
            """, (task_id, manager_id, limit))).fetchall()
        return [(r[0], r[1], r[2], datetime.fromisoformat(r[3])) for r in rows]

    async def authorized_user_ids(self) -> list[int]:
        async with self._connect() as db:
            rows = await (await db.execute(
                "SELECT user_id FROM users WHERE role='owner' OR access_granted=1"
            )).fetchall()
        return [row[0] for row in rows]

    async def create_access_code(
        self, code: str, usage_type: str = "single", max_uses: int = 1
    ) -> None:
        usage_type = usage_type.lower().strip()
        if usage_type not in {"single", "multi"}:
            raise ValueError("usage_type must be 'single' or 'multi'")
        max_uses = int(max_uses)
        if max_uses < 1:
            raise ValueError("max_uses must be positive")
        if usage_type == "single":
            max_uses = 1
        code = code.upper()
        async with self._connect() as db:
            await db.execute("""INSERT INTO access_codes(code,active,usage_type,max_uses) VALUES(?,1,?,?)
                ON CONFLICT(code) DO UPDATE SET
                    active=1, usage_type=excluded.usage_type,
                    max_uses=excluded.max_uses,
                    redeemed_by=NULL, redeemed_at=NULL""", (code, usage_type, max_uses))
            await db.execute("DELETE FROM access_code_usages WHERE code=?", (code,))
            await db.commit()

    async def redeem_access_code(self, code: str, user_id: int, display_name: str = "") -> bool:
        code = code.upper()
        async with self._connect() as db:
            row = await (await db.execute(
                "SELECT usage_type,max_uses,active FROM access_codes WHERE code=?", (code,)
            )).fetchone()
            if row is None:
                await db.rollback()
                return False
            usage_type, max_uses, active = row
            if usage_type == "single":
                if not active:
                    await db.rollback()
                    return False
                cursor = await db.execute("""UPDATE access_codes
                    SET active=0,redeemed_by=?,redeemed_at=CURRENT_TIMESTAMP
                    WHERE code=? AND active=1""", (user_id, code))
                if cursor.rowcount == 0:
                    await db.rollback()
                    return False
            else:
                existing_usage = await (await db.execute("""
                    SELECT 1 FROM access_code_usages WHERE code=? AND user_id=?
                """, (code, user_id))).fetchone()
                if existing_usage:
                    await db.execute("""INSERT INTO users(user_id,role,display_name,access_granted)
                        VALUES(?, 'manager', ?, 1)
                        ON CONFLICT(user_id) DO UPDATE SET
                            display_name=excluded.display_name,access_granted=1""",
                        (user_id, display_name))
                    await db.commit()
                    return True
                if not active:
                    await db.rollback()
                    return False
                cursor = await db.execute("""UPDATE access_codes
                    SET redeemed_by=COALESCE(redeemed_by,?),
                        redeemed_at=COALESCE(redeemed_at,CURRENT_TIMESTAMP)
                    WHERE code=? AND active=1
                      AND (
                          SELECT COUNT(*) FROM access_code_usages
                          WHERE access_code_usages.code=access_codes.code
                      ) < max_uses""", (user_id, code))
                if cursor.rowcount == 0:
                    await db.rollback()
                    return False
            await db.execute("""INSERT INTO access_code_usages(code,user_id,display_name)
                VALUES(?,?,?)
                ON CONFLICT(code,user_id) DO UPDATE SET display_name=excluded.display_name""",
                (code, user_id, display_name))
            await db.execute("""INSERT INTO users(user_id,role,display_name,access_granted)
                VALUES(?, 'manager', ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name=excluded.display_name,access_granted=1""", (user_id, display_name))
            if usage_type == "multi":
                await db.execute("""UPDATE access_codes SET active=0
                    WHERE code=? AND usage_type='multi'
                      AND (SELECT COUNT(*) FROM access_code_usages
                           WHERE access_code_usages.code=access_codes.code) >= max_uses""", (code,))
            await db.commit()
            return True

    async def access_codes(self) -> list[tuple[str, bool]]:
        async with self._connect() as db:
            rows = await (await db.execute(
                "SELECT code,active FROM access_codes ORDER BY created_at DESC"
            )).fetchall()
        return [(row[0], bool(row[1])) for row in rows]

    async def access_code_details(self) -> list[tuple[str, bool, str, int, list[int]]]:
        """Return every code with capacity and unique Telegram IDs."""
        async with self._connect() as db:
            codes = await (await db.execute(
                "SELECT code,active,usage_type,max_uses FROM access_codes ORDER BY created_at DESC"
            )).fetchall()
            usages = await (await db.execute(
                "SELECT code,user_id FROM access_code_usages ORDER BY redeemed_at ASC"
            )).fetchall()
        users_by_code: dict[str, list[int]] = {}
        for code, user_id in usages:
            users_by_code.setdefault(code.upper(), []).append(user_id)
        return [
            (code, bool(active), usage_type, max_uses, users_by_code.get(code.upper(), []))
            for code, active, usage_type, max_uses in codes
        ]

    async def extend_access_code_capacity(self, code: str, additional_uses: int) -> int | None:
        additional_uses = int(additional_uses)
        if additional_uses < 1:
            raise ValueError("additional_uses must be positive")
        async with self._connect() as db:
            cursor = await db.execute("""UPDATE access_codes
                SET max_uses=max_uses+?, active=1
                WHERE code=? AND usage_type='multi'""", (additional_uses, code.upper()))
            if cursor.rowcount == 0:
                await db.rollback()
                return None
            row = await (await db.execute(
                "SELECT max_uses FROM access_codes WHERE code=?", (code.upper(),)
            )).fetchone()
            await db.commit()
        return row[0] if row else None

    async def deactivate_access_code(self, code: str) -> None:
        async with self._connect() as db:
            await db.execute("UPDATE access_codes SET active=0 WHERE code=?", (code.upper(),))
            await db.commit()

    async def add_task(
        self, user_id: int, title: str, deadline: datetime, priority: int,
        assignee: str, note: str = "", assignee_user_id: int | None = None,
        cc_manager_id: int | None = None, changed_by: int | None = None,
    ) -> int:
        if int(priority) not in (1, 2, 3, 4):
            raise ValueError("priority must be between 1 and 4")
        created_at = datetime.now(deadline.tzinfo).isoformat() if deadline.tzinfo else datetime.now().astimezone().isoformat()
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT INTO tasks(
                       user_id,title,deadline,priority,assignee,last_note,status,
                       created_at,updated_at,assignee_user_id,cc_manager_id,last_changed_by
                   ) VALUES(?,?,?,?,?,?,'open',?,?,?,?,?)""",
                (user_id, title.strip()[:200], deadline.isoformat(), int(priority),
                  assignee.strip()[:200], note.strip(), created_at, created_at,
                  assignee_user_id, cc_manager_id, changed_by or user_id),
            )
            task_id = int(cursor.lastrowid)
            await db.execute(
                """INSERT INTO task_progress_history
                   (task_id,manager_id,changed_by,progress,changed_at)
                   VALUES(?,?,?,?,?)""", (task_id, user_id, user_id, 0, created_at),
            )
            await db.commit()
            return task_id

    async def create_recurrence_series(self, user_id, title, anchor, interval, assignee,
                                       note="", priority=1, weekdays=None, frequency="daily",
                                       jalali_day=None, ends_on=None):
        import json
        tid = await self.add_task(user_id, title, anchor, priority, assignee, note)
        async with self._connect() as db:
            cur = await db.execute("""INSERT INTO recurrence_series
                (user_id,title,anchor,priority,assignee,note,interval,weekdays,frequency,jalali_day,ends_on)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (user_id,title,anchor.isoformat(),priority,assignee,note,interval,
                 json.dumps(weekdays or []),frequency,jalali_day,ends_on))
            sid = cur.lastrowid
            await db.execute("INSERT INTO recurrence_occurrences VALUES(?,?,?,0)", (sid,tid,anchor.isoformat()))
            await db.commit()
        return sid, tid

    async def materialize_recurrences(self, horizon):
        from app.recurrence import RecurrenceRule, next_occurrence
        import json
        count = 0
        async with self._connect() as db:
            rows = await (await db.execute("""SELECT s.id,s.user_id,s.title,s.anchor,
                s.priority,s.assignee,s.note,s.interval,s.weekdays,s.frequency,s.jalali_day,s.ends_on,
                COALESCE((SELECT MAX(o.occurrence_at) FROM recurrence_occurrences o
                          WHERE o.series_id=s.id),s.anchor)
                FROM recurrence_series s WHERE s.active=1""")).fetchall()
            for r in rows:
                sid,uid,title,anchor_s,priority,assignee,note,interval,weekdays,frequency,jday,ends,cursor_s = r
                anchor = datetime.fromisoformat(anchor_s); cursor = datetime.fromisoformat(cursor_s)
                while True:
                    effective_interval = 1 if frequency == "daily" else interval
                    nxt = next_occurrence(anchor,cursor,RecurrenceRule(frequency,effective_interval,tuple(json.loads(weekdays or "[]")),jday,date.fromisoformat(ends) if ends else None))
                    if not nxt or nxt > horizon: break
                    exists = await (await db.execute("SELECT 1 FROM recurrence_occurrences WHERE series_id=? AND occurrence_at=?",(sid,nxt.isoformat()))).fetchone()
                    if not exists:
                        cur = await db.execute("INSERT INTO tasks(user_id,title,deadline,priority,assignee,last_note,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'open',?,?)",(uid,title,nxt.isoformat(),priority,assignee,note,nxt.isoformat(),nxt.isoformat()))
                        await db.execute("INSERT INTO recurrence_occurrences VALUES(?,?,?,0)",(sid,cur.lastrowid,nxt.isoformat())); count += 1
                    cursor = nxt
            await db.commit()
        return count

    async def skip_occurrence(self, task_id, user_id):
        async with self._connect() as db:
            await db.execute("UPDATE tasks SET status='skipped' WHERE id=? AND user_id=?", (task_id,user_id))
            await db.execute("UPDATE recurrence_occurrences SET skipped=1 WHERE task_id=?", (task_id,))
            await db.commit()

    async def stop_recurrence(self, series_id, user_id):
        async with self._connect() as db:
            cur = await db.execute("UPDATE recurrence_series SET active=0 WHERE id=? AND user_id=?", (series_id,user_id))
            await db.commit()
        return cur.rowcount > 0

    async def _queue_google_upsert(self, conn, user_id: int, meeting_id: int) -> None:
        """Coalesce repeated edits while keeping the enqueue in the meeting transaction."""
        await conn.execute(
            """INSERT INTO google_sync_queue(user_id,meeting_id,action)
               VALUES(?,?,'upsert')
               ON CONFLICT(user_id,meeting_id,action,calendar_id) DO UPDATE SET
                 attempts=0,available_at=CURRENT_TIMESTAMP,last_error=''""",
            (user_id, meeting_id),
        )

    async def _queue_google_delete(self, conn, user_id: int, meeting_id: int) -> None:
        link = await (await conn.execute(
            """SELECT calendar_id,google_event_id FROM google_meeting_links
               WHERE meeting_id=? AND user_id=?""",
            (meeting_id, user_id),
        )).fetchone()
        if link:
            calendar_id, event_id = str(link[0]), str(link[1])
        else:
            connection = await (await conn.execute(
                "SELECT calendar_id FROM google_connections WHERE user_id=? AND enabled=1",
                (user_id,),
            )).fetchone()
            calendar_id, event_id = (str(connection[0]), "") if connection else ("", "")
        await conn.execute(
            "DELETE FROM google_sync_queue WHERE user_id=? AND meeting_id=? AND action='upsert'",
            (user_id, meeting_id),
        )
        if calendar_id:
            await conn.execute(
                """INSERT INTO google_sync_queue
                   (user_id,meeting_id,action,calendar_id,google_event_id)
                   VALUES(?,?,'delete',?,?)
                   ON CONFLICT(user_id,meeting_id,action,calendar_id) DO UPDATE SET
                     google_event_id=excluded.google_event_id,attempts=0,
                     available_at=CURRENT_TIMESTAMP,last_error=''""",
                (user_id, meeting_id, calendar_id, event_id),
            )

    async def add_meeting(self, user_id: int, title: str, starts_at: datetime,
                          ends_at: datetime, source_text: str) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO meetings(user_id,title,starts_at,ends_at,source_text) VALUES(?,?,?,?,?)",
                (user_id, title, starts_at.isoformat(), ends_at.isoformat(), source_text),
            )
            meeting_id = int(cursor.lastrowid)
            await self._queue_google_upsert(db, user_id, meeting_id)
            await db.commit()
            return meeting_id

    async def create_meeting_recurrence(
        self, user_id: int, title: str, starts_at: datetime, ends_at: datetime,
        source_text: str, recurrence: dict,
    ) -> tuple[int, int]:
        """Create the anchor event and an independent recurring-event series."""
        import json
        from app.recurrence import RecurrenceRule
        rule = RecurrenceRule(
            recurrence["frequency"], int(recurrence.get("interval", 1)),
            tuple(int(x) for x in recurrence.get("weekdays", [])),
            recurrence.get("jalali_day"),
            date.fromisoformat(recurrence["ends_on"]) if recurrence.get("ends_on") else None,
        ).validate()
        async with self._connect() as db:
            meeting_cur = await db.execute(
                "INSERT INTO meetings(user_id,title,starts_at,ends_at,source_text) VALUES(?,?,?,?,?)",
                (user_id, title, starts_at.isoformat(), ends_at.isoformat(), source_text),
            )
            meeting_id = int(meeting_cur.lastrowid)
            series_cur = await db.execute(
                """INSERT INTO meeting_recurrence_series
                   (user_id,title,source_text,anchor_start,anchor_end,interval,weekdays,
                    frequency,jalali_day,ends_on,active)
                   VALUES(?,?,?,?,?,?,?,?,?,?,1)""",
                (user_id, title, source_text, starts_at.isoformat(), ends_at.isoformat(),
                 rule.interval, json.dumps(list(rule.weekdays)), rule.frequency,
                 rule.jalali_day, rule.ends_on.isoformat() if rule.ends_on else None),
            )
            series_id = int(series_cur.lastrowid)
            await db.execute(
                "INSERT INTO meeting_recurrence_occurrences(series_id,meeting_id,occurrence_at) VALUES(?,?,?)",
                (series_id, meeting_id, starts_at.isoformat()),
            )
            await self._queue_google_upsert(db, user_id, meeting_id)
            await db.commit()
        return series_id, meeting_id

    async def materialize_meeting_recurrences(self, horizon: datetime) -> int:
        import json
        from app.recurrence import RecurrenceRule, next_occurrence
        created = 0
        async with self._connect() as db:
            rows = await (await db.execute(
                """SELECT s.id,s.user_id,s.title,s.source_text,s.anchor_start,s.anchor_end,
                          s.interval,s.weekdays,s.frequency,s.jalali_day,s.ends_on,
                          COALESCE((SELECT MAX(o.occurrence_at)
                                    FROM meeting_recurrence_occurrences o
                                    WHERE o.series_id=s.id),s.anchor_start)
                   FROM meeting_recurrence_series s WHERE s.active=1"""
            )).fetchall()
            for row in rows:
                sid, uid, title, source, anchor_s, end_s, interval, weekdays_s, frequency, jday, ends_s, cursor_s = row
                anchor = datetime.fromisoformat(anchor_s)
                cursor = datetime.fromisoformat(cursor_s)
                rule = RecurrenceRule(
                    frequency, interval, tuple(json.loads(weekdays_s or "[]")),
                    jday, date.fromisoformat(ends_s) if ends_s else None,
                )
                while True:
                    nxt = next_occurrence(anchor, cursor, rule)
                    if not nxt or nxt > horizon:
                        break
                    exists = await (await db.execute(
                        "SELECT 1 FROM meeting_recurrence_occurrences WHERE series_id=? AND occurrence_at=?",
                        (sid, nxt.isoformat()),
                    )).fetchone()
                    if not exists:
                        duration = datetime.fromisoformat(end_s) - anchor
                        cur = await db.execute(
                            "INSERT INTO meetings(user_id,title,starts_at,ends_at,source_text) VALUES(?,?,?,?,?)",
                            (uid, title, nxt.isoformat(), (nxt + duration).isoformat(), source),
                        )
                        await db.execute(
                            "INSERT INTO meeting_recurrence_occurrences(series_id,meeting_id,occurrence_at) VALUES(?,?,?)",
                            (sid, int(cur.lastrowid), nxt.isoformat()),
                        )
                        await self._queue_google_upsert(db, int(uid), int(cur.lastrowid))
                        created += 1
                    cursor = nxt
            await db.commit()
        return created

    async def meeting_recurrence(self, meeting_id: int, user_id: int) -> dict | None:
        async with self._connect() as db:
            row = await (await db.execute(
                """SELECT s.id,s.frequency,s.interval,s.weekdays,s.jalali_day,s.ends_on,s.active
                   FROM meeting_recurrence_occurrences o
                   JOIN meeting_recurrence_series s ON s.id=o.series_id
                   JOIN meetings m ON m.id=o.meeting_id
                   WHERE o.meeting_id=? AND m.user_id=?""", (meeting_id, user_id)
            )).fetchone()
        if not row:
            return None
        import json
        return {"series_id": row[0], "frequency": row[1], "interval": row[2],
                "weekdays": json.loads(row[3] or "[]"), "jalali_day": row[4],
                "ends_on": row[5], "active": bool(row[6])}

    async def meeting_recurrence_series(self, series_id: int, user_id: int) -> dict | None:
        async with self._connect() as db:
            row = await (await db.execute(
                """SELECT id,frequency,interval,weekdays,jalali_day,ends_on,active
                   FROM meeting_recurrence_series WHERE id=? AND user_id=?""",
                (series_id, user_id),
            )).fetchone()
        if not row:
            return None
        import json
        return {"series_id": row[0], "frequency": row[1], "interval": row[2],
                "weekdays": json.loads(row[3] or "[]"), "jalali_day": row[4],
                "ends_on": row[5], "active": bool(row[6])}

    async def active_meeting_recurrence_ids_by_title(self, user_id: int, title: str) -> list[int]:
        """Find active series for the same logical title (DBA == جلسات DBA)."""
        wanted = _canonical_meeting_title(title)
        if not wanted:
            return []
        async with self._connect() as db:
            rows = await (await db.execute(
                "SELECT id,title FROM meeting_recurrence_series WHERE user_id=? AND active=1",
                (user_id,),
            )).fetchall()
        return [int(row[0]) for row in rows if _canonical_meeting_title(str(row[1])) == wanted]

    async def set_meeting_recurrence_active(
        self, series_id: int, user_id: int, active: bool,
    ) -> bool:
        async with self._connect() as db:
            cur = await db.execute(
                "UPDATE meeting_recurrence_series SET active=? WHERE id=? AND user_id=?",
                (int(active), series_id, user_id),
            )
            if cur.rowcount and not active:
                cutoff = datetime.now().astimezone().isoformat()
                future = await (await db.execute(
                    """SELECT meeting_id FROM meeting_recurrence_occurrences
                       WHERE series_id=? AND occurrence_at>?""",
                    (series_id, cutoff),
                )).fetchall()
                for row in future:
                    await self._queue_google_delete(db, user_id, int(row[0]))
                await db.executemany(
                    "DELETE FROM meetings WHERE id=?",
                    [(int(row[0]),) for row in future],
                )
                await db.execute(
                    "DELETE FROM meeting_recurrence_occurrences WHERE series_id=? AND occurrence_at>?",
                    (series_id, cutoff),
                )
            await db.commit()
        return cur.rowcount > 0

    async def delete_meeting_recurrence(self, series_id: int, user_id: int) -> bool:
        return await self.set_meeting_recurrence_active(series_id, user_id, False)

    async def update_meeting_recurrence(
        self, series_id: int, user_id: int, recurrence: dict,
    ) -> bool:
        import json
        from app.recurrence import RecurrenceRule
        rule = RecurrenceRule(
            recurrence["frequency"], int(recurrence.get("interval", 1)),
            tuple(int(x) for x in recurrence.get("weekdays", [])),
            recurrence.get("jalali_day"),
            date.fromisoformat(recurrence["ends_on"]) if recurrence.get("ends_on") else None,
        ).validate()
        async with self._connect() as db:
            cutoff = datetime.now().astimezone().isoformat()
            future = await (await db.execute(
                """SELECT meeting_id FROM meeting_recurrence_occurrences
                   WHERE series_id=? AND occurrence_at>?""",
                (series_id, cutoff),
            )).fetchall()
            for row in future:
                await self._queue_google_delete(db, user_id, int(row[0]))
            await db.executemany(
                "DELETE FROM meetings WHERE id=?",
                [(int(row[0]),) for row in future],
            )
            await db.execute(
                "DELETE FROM meeting_recurrence_occurrences WHERE series_id=? AND occurrence_at>?",
                (series_id, cutoff),
            )
            cur = await db.execute(
                """UPDATE meeting_recurrence_series SET frequency=?,interval=?,weekdays=?,
                   jalali_day=?,ends_on=?,active=1 WHERE id=? AND user_id=?""",
                (rule.frequency, rule.interval, json.dumps(list(rule.weekdays)), rule.jalali_day,
                 rule.ends_on.isoformat() if rule.ends_on else None, series_id, user_id),
            )
            await db.commit()
        return cur.rowcount > 0

    async def tasks(self, user_id: int, include_done: bool = False, limit: int = 50,
                    include_unassigned: bool = False) -> list[Task]:
        query = f"SELECT {_TASK_COLUMNS} FROM tasks t {_TASK_ACTOR_JOINS} WHERE t.user_id=?"
        params: list[object] = [user_id]
        if not include_unassigned:
            query += " AND NOT (t.assignee='' AND t.assignee_user_id IS NULL)"
        if not include_done:
            query += " AND t.status!='done'"
        query += " ORDER BY t.priority ASC, t.deadline ASC LIMIT ?"
        params.append(limit)
        async with self._connect() as db:
            rows = await (await db.execute(query, params)).fetchall()
        return [self._task_from_row(row) for row in rows]

    async def query_tasks(self, user_id: int, filters: dict, sort: str = "deadline_asc",
                          limit: int = 20, now: datetime | None = None,
                          include_unassigned: bool = True,
                          include_done: bool = True) -> list[Task]:
        query = f"SELECT {_TASK_COLUMNS} FROM tasks t {_TASK_ACTOR_JOINS} WHERE t.user_id=?"
        params: list[object] = [user_id]
        if not include_unassigned:
            query += " AND NOT (t.assignee='' AND t.assignee_user_id IS NULL)"
        if not include_done:
            query += " AND t.status!='done'"
        comparisons = {
            "progress_eq": "progress=?",
            "progress_lt": "progress<?", "progress_lte": "progress<=?",
            "progress_gt": "progress>?", "progress_gte": "progress>=?",
        }
        for key, clause in comparisons.items():
            if key in filters:
                query += f" AND {clause}"
                params.append(int(filters[key]))
        status = filters.get("status")
        if status and status != "all":
            query += " AND status=?"
            params.append(status)
        if filters.get("priority"):
            query += " AND priority=?"
            params.append(int(filters["priority"]))
        if filters.get("assignee_contains"):
            query += " AND assignee LIKE ?"
            params.append(f"%{filters['assignee_contains']}%")
        if filters.get("title_contains"):
            query += " AND title LIKE ?"
            params.append(f"%{filters['title_contains']}%")
        reference = now or datetime.now().astimezone()
        if filters.get("overdue") is True:
            query += " AND deadline<? AND status!='done'"
            params.append(reference.isoformat())
        elif filters.get("overdue") is False:
            query += " AND deadline>=?"
            params.append(reference.isoformat())
        if "due_within_days" in filters:
            query += " AND deadline>=? AND deadline<=?"
            params.extend((reference.isoformat(), (reference + timedelta(days=int(filters["due_within_days"]))).isoformat()))
        if "unchanged_days" in filters:
            query += " AND datetime(t.updated_at)<=datetime(?)"
            params.append((reference - timedelta(days=int(filters["unchanged_days"]))).isoformat())
        order_by = {
            "progress_asc": "progress ASC, deadline ASC",
            "progress_desc": "progress DESC, deadline ASC",
            "deadline_asc": "deadline ASC",
            "deadline_desc": "deadline DESC",
            "priority_asc": "priority ASC, deadline ASC",
        }.get(sort, "deadline ASC")
        query += f" ORDER BY {order_by} LIMIT ?"
        params.append(max(1, min(50, int(limit))))
        async with self._connect() as db:
            rows = await (await db.execute(query, params)).fetchall()
        return [self._task_from_row(row) for row in rows]

    async def delete_task(self, task_id: int, user_id: int) -> bool:
        async with self._connect() as db:
            cursor = await db.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (task_id, user_id))
            await db.commit()
        return cursor.rowcount > 0
    @staticmethod
    def _reminder_from_row(row) -> Reminder:
        return Reminder(
            row[0], row[1], row[2], datetime.fromisoformat(row[3]), row[4],
            datetime.fromisoformat(row[5]) if row[5] else None, bool(row[6]),
        )

    async def add_reminder(
        self, user_id: int, title: str, due_at: datetime, repeat_type: str = "once",
        repeat_until: datetime | None = None,
    ) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT INTO reminders(user_id,title,due_at,repeat_type,repeat_until,active,
                   last_notified_at,last_warning_sent_for_due_at)
                   VALUES(?,?,?,?,?,1,NULL,NULL)""",
                (user_id, title.strip()[:200], due_at.isoformat(), repeat_type,
                 repeat_until.isoformat() if repeat_until else None),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def reminder(self, reminder_id: int, user_id: int) -> Reminder | None:
        async with self._connect() as db:
            row = await (await db.execute(
                """SELECT id,user_id,title,due_at,repeat_type,repeat_until,active
                   FROM reminders WHERE id=? AND user_id=?""", (reminder_id, user_id)
            )).fetchone()
        return self._reminder_from_row(row) if row else None

    async def reminders(
        self, user_id: int, limit: int = 100, now: datetime | None = None,
        within_days: int | None = None,
    ) -> list[Reminder]:
        query = """SELECT id,user_id,title,due_at,repeat_type,repeat_until,active
                   FROM reminders WHERE user_id=? AND active=1"""
        params: list[object] = [user_id]
        if within_days is not None:
            if within_days <= 0:
                raise ValueError("بازه زمانی یادآور نامعتبر است")
            reference = now or datetime.now().astimezone()
            query += " AND due_at>=? AND due_at<=?"
            params.extend((reference.isoformat(), (reference + timedelta(days=within_days)).isoformat()))
        query += " ORDER BY due_at,id LIMIT ?"
        params.append(limit)
        async with self._connect() as db:
            rows = await (await db.execute(query, params)).fetchall()
        return [self._reminder_from_row(row) for row in rows]

    async def update_reminder(
        self, reminder_id: int, user_id: int, title: str, due_at: datetime, repeat_type: str,
        repeat_until: datetime | None = None,
    ) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                """UPDATE reminders SET title=?,due_at=?,repeat_type=?,repeat_until=?,
                   active=1,last_notified_at=NULL,last_warning_sent_for_due_at=NULL
                   WHERE id=? AND user_id=?""",
                (title.strip()[:200], due_at.isoformat(), repeat_type,
                 repeat_until.isoformat() if repeat_until else None, reminder_id, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_reminder(self, reminder_id: int, user_id: int) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM reminders WHERE id=? AND user_id=?", (reminder_id, user_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    def _next_reminder_due(due_at: datetime, repeat_type: str) -> datetime | None:
        if repeat_type == "daily":
            return due_at + timedelta(days=1)
        if repeat_type == "every_other_day":
            return due_at + timedelta(days=2)
        if repeat_type in {"even_days", "odd_days"}:
            wanted = 0 if repeat_type == "even_days" else 1
            candidate = due_at + timedelta(days=1)
            while candidate.day % 2 != wanted:
                candidate += timedelta(days=1)
            return candidate
        if repeat_type.startswith("weekly:"):
            try:
                ui_weekday = int(repeat_type.split(":", 1)[1])
            except (TypeError, ValueError):
                return None
            target_weekday = (ui_weekday + 5) % 7
            days = (target_weekday - due_at.weekday()) % 7 or 7
            return due_at + timedelta(days=days)
        if repeat_type == "monthly":
            import jdatetime
            local = jdatetime.datetime.fromgregorian(datetime=due_at)
            year, month = local.year, local.month + 1
            if month == 13:
                year, month = year + 1, 1
            max_day = 31 if month <= 6 else (30 if month <= 11 else (30 if jdatetime.date(year, 1, 1).isleap() else 29))
            next_local = jdatetime.datetime(
                year, month, min(local.day, max_day), local.hour, local.minute,
                local.second, local.microsecond,
            ).togregorian()
            return next_local.replace(tzinfo=due_at.tzinfo)
        return None

    async def complete_reminder(
        self, reminder_id: int, user_id: int, completed_at: datetime,
    ) -> Reminder | None:
        current = await self.reminder(reminder_id, user_id)
        if not current or not current.active:
            return None
        next_due = self._next_reminder_due(current.due_at, current.repeat_type)
        active = bool(next_due and (current.repeat_until is None or next_due <= current.repeat_until))
        async with self._connect() as db:
            cursor = await db.execute(
                """UPDATE reminders SET due_at=?,active=?,last_notified_at=NULL,
                   last_warning_sent_for_due_at=NULL
                   WHERE id=? AND user_id=? AND active=1""",
                ((next_due or current.due_at).isoformat(), int(active), reminder_id, user_id),
            )
            await db.commit()
        return current if cursor.rowcount else None

    async def due_reminders(self, now: datetime) -> list[Reminder]:
        async with self._connect() as db:
            rows = await (await db.execute(
                """SELECT id,user_id,title,due_at,repeat_type,repeat_until,active
                   FROM reminders WHERE active=1 AND due_at<=?
                   AND last_notified_at IS NULL
                   ORDER BY due_at,id""", (now.isoformat(),)
            )).fetchall()
        return [self._reminder_from_row(row) for row in rows]

    async def due_reminder_warnings(
        self, now: datetime, default_hour: int, default_minute: int,
    ) -> list[Reminder]:
        async with self._connect() as db:
            rows = await (await db.execute(
                """SELECT id,user_id,title,due_at,repeat_type,repeat_until,active,
                          last_warning_sent_for_due_at
                   FROM reminders WHERE active=1 AND due_at>? ORDER BY due_at,id""",
                (now.isoformat(),),
            )).fetchall()
        result: list[Reminder] = []
        config_cache: dict[int, dict[str, int]] = {}
        for row in rows:
            reminder = self._reminder_from_row(row)
            if reminder.user_id not in config_cache:
                config_cache[reminder.user_id] = await self.user_settings(
                    reminder.user_id,
                    {
                        "reminder_warning_enabled": 0,
                        "reminder_warning_hour": default_hour,
                        "reminder_warning_minute": default_minute,
                    },
                )
            config = config_cache[reminder.user_id]
            if not config.get("reminder_warning_enabled"):
                continue
            try:
                warning_at = datetime.combine(
                    reminder.due_at.astimezone(now.tzinfo).date() - timedelta(days=1),
                    time(int(config.get("reminder_warning_hour", default_hour)),
                         int(config.get("reminder_warning_minute", default_minute))),
                    tzinfo=now.tzinfo,
                )
            except (TypeError, ValueError):
                continue
            if warning_at <= now < reminder.due_at and row[7] != reminder.due_at.isoformat():
                result.append(reminder)
        return result

    async def mark_reminder_warning_sent(
        self, reminder_id: int, due_at: datetime, now: datetime,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE reminders SET last_warning_sent_for_due_at=? WHERE id=? AND due_at=?",
                (due_at.isoformat(), reminder_id, due_at.isoformat()),
            )
            await db.commit()

    async def deadline_warning_days(self, user_id: int, default: int) -> list[int]:
        cfg = await self.user_settings(
            user_id, {"deadline_warning_days_list": str(default)}
        )
        try:
            return sorted({
                int(x) for x in str(
                    cfg.get("deadline_warning_days_list") or default
                ).split(",") if x.strip() != ""
            }, reverse=True)
        except (TypeError, ValueError):
            return [default]

    async def due_task_deadline_alerts(
        self, now: datetime, default_warning_days: int,
    ) -> list[tuple[Task, int]]:
        async with self._connect() as db:
            rows = await (await db.execute(
                f"""SELECT {_TASK_COLUMNS}
                   FROM tasks t {_TASK_ACTOR_JOINS}
                   WHERE t.status='open' AND t.deadline>=?
                   ORDER BY t.deadline,t.id""",
                ((now - timedelta(days=1)).isoformat(),),
            )).fetchall()
            sent_rows = await (await db.execute(
                "SELECT task_id,warning_days FROM task_deadline_alerts"
            )).fetchall()
        sent = {(int(task_id), int(days)) for task_id, days in sent_rows}
        result: list[tuple[Task, int]] = []
        warning_days_cache: dict[int, list[int]] = {}
        for row in rows:
            task = self._task_from_row(row)
            deadline_local = task.deadline.astimezone(now.tzinfo)
            remaining_days = (deadline_local.date() - now.date()).days
            if task.user_id not in warning_days_cache:
                warning_days_cache[task.user_id] = await self.deadline_warning_days(
                    task.user_id, default_warning_days
                )
            warning_days = warning_days_cache[task.user_id]
            for days in warning_days:
                if days == remaining_days and (
                    task.id, days
                ) not in sent:
                    result.append((task, days))
        return result

    async def mark_task_deadline_alert(
        self, task_id: int, warning_days: int, now: datetime,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """INSERT OR IGNORE INTO task_deadline_alerts
                   (task_id,warning_days,notified_at) VALUES(?,?,?)""",
                (task_id, warning_days, now.isoformat()),
            )
            await db.commit()

    async def reminder_warning_days(self, user_id: int, default: int) -> list[int]:
        cfg = await self.user_settings(user_id, {"reminder_warning_days_list": str(default)})
        try: return sorted({int(x) for x in str(cfg.get("reminder_warning_days_list") or default).split(",") if x != ""}, reverse=True)
        except ValueError: return [default]

    async def mark_reminder_notified(self, reminder_id: int, now: datetime) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE reminders SET last_notified_at=? WHERE id=?", (now.isoformat(), reminder_id)
            )
            await db.commit()
    async def task_reminders(self, user_id: int, limit: int = 100) -> list[Task]:
        async with self._connect() as db:
            rows = await (await db.execute(
                f"""SELECT {_TASK_COLUMNS}
                   FROM tasks t {_TASK_ACTOR_JOINS}
                   WHERE t.user_id=? AND t.status!='done'
                     AND t.assignee='' AND t.assignee_user_id IS NULL
                   ORDER BY t.deadline LIMIT ?""",
                (user_id, limit),
            )).fetchall()
        return [self._task_from_row(row) for row in rows]

    async def task(self, task_id: int, user_id: int) -> Task | None:
        async with self._connect() as db:
            row = await (await db.execute(
                f"""SELECT {_TASK_COLUMNS}
                    FROM tasks t {_TASK_ACTOR_JOINS}
                    WHERE t.id=? AND t.user_id=?""",
                (task_id, user_id),
            )).fetchone()
        return self._task_from_row(row) if row else None

    async def update_task(
        self, task_id: int, user_id: int, field: str, value: object,
        changed_by: int | None = None,
    ) -> bool:
        allowed = {"progress", "last_note", "status", "deadline", "priority", "assignee", "cc_manager_id"}
        if field not in allowed:
            raise ValueError("فیلد نامعتبر")
        actor_id = changed_by or user_id
        now = datetime.now().astimezone().isoformat()
        async with self._connect() as db:
            cursor = await db.execute(
                f"""UPDATE tasks SET {field}=?, updated_at=?, last_changed_by=?
                    WHERE id=? AND user_id=?""",
                (value, now, actor_id, task_id, user_id),
            )
            if cursor.rowcount and field == "deadline":
                await db.execute(
                    "DELETE FROM task_deadline_alerts WHERE task_id=?",
                    (task_id,),
                )
            if cursor.rowcount and field == "progress":
                await db.execute("""INSERT INTO task_progress_history
                    (task_id,manager_id,changed_by,progress,changed_at) VALUES(?,?,?,?,?)""",
                    (task_id, user_id, actor_id, int(value), now))
                if int(value) == 100:
                    await db.execute("UPDATE tasks SET completed_at=? WHERE id=? AND user_id=?", (now, task_id, user_id))
            elif cursor.rowcount and field == "status" and value == "done":
                await db.execute("UPDATE tasks SET completed_at=COALESCE(completed_at,?) WHERE id=? AND user_id=?",
                                 (datetime.now().astimezone().isoformat(), task_id, user_id))
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    def _task_from_row(row) -> Task:
        updated_at = datetime.fromisoformat(row[12]) if len(row) > 12 and row[12] else None
        if updated_at is not None and updated_at.tzinfo is None:
            # SQLite CURRENT_TIMESTAMP values from older releases are UTC.
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return Task(
            row[0], row[1], row[2], datetime.fromisoformat(row[3]), Priority(row[4]),
            row[5], row[6], row[7], row[8], datetime.fromisoformat(row[9]),
            row[10], row[11], updated_at,
            row[13] if len(row) > 13 else None,
            (row[14] or "") if len(row) > 14 else "",
        )

    async def task_progress_history(self, task_id: int, manager_id: int) -> list[tuple[datetime, int, int]]:
        async with self._connect() as db:
            rows = await (await db.execute("""SELECT changed_at,progress,changed_by
                FROM task_progress_history WHERE task_id=? AND manager_id=? ORDER BY changed_at,id""",
                (task_id, manager_id))).fetchall()
        return [(datetime.fromisoformat(r[0]), r[1], r[2]) for r in rows]

    async def member_performance(self, manager_id: int, member_id: int, now: datetime) -> dict:
        async with self._connect() as db:
            row = await (await db.execute("""
                SELECT COUNT(*),
                  SUM(CASE WHEN status='open' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN status='paused' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN status='suspended' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN status='done' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN status='open' AND deadline<? THEN 1 ELSE 0 END),
                  COALESCE(AVG(CASE WHEN status!='done' THEN progress END),0)
                FROM tasks WHERE user_id=? AND assignee_user_id=?
            """, (now.isoformat(), manager_id, member_id))).fetchone()
            completed = await (await db.execute("""SELECT created_at,deadline,completed_at
                FROM tasks WHERE user_id=? AND assignee_user_id=? AND status='done' AND completed_at IS NOT NULL""",
                (manager_id, member_id))).fetchall()
            report_row = await (await db.execute("""
                SELECT COUNT(*),MAX(created_at),COUNT(DISTINCT task_id)
                FROM task_reports WHERE manager_id=? AND reporter_id=?
            """, (manager_id, member_id))).fetchone()
        timing = []
        early = on_time = late = 0
        total_delta_days = 0.0
        for created, deadline, completed_at in completed:
            start, due, done = map(datetime.fromisoformat, (created, deadline, completed_at))
            duration = max((due - start).total_seconds(), 1)
            delta_seconds = (due - done).total_seconds()
            timing.append((delta_seconds / duration) * 100)
            total_delta_days += delta_seconds / 86400
            if delta_seconds > 60:
                early += 1
            elif delta_seconds < -60:
                late += 1
            else:
                on_time += 1
        average_timing = round(sum(timing) / len(timing)) if timing else None
        average_delta_days = round(total_delta_days / len(completed), 1) if completed else None
        completed_count = len(completed)
        completion_rate = round((row[4] or 0) * 100 / (row[0] or 1))
        early_rate = round(early * 100 / completed_count) if completed_count else 0
        on_time_rate = round(on_time * 100 / completed_count) if completed_count else 0
        late_rate = round(late * 100 / completed_count) if completed_count else 0
        return {"total": row[0] or 0, "open": row[1] or 0,
                "paused": row[2] or 0, "suspended": row[3] or 0,
                "done": row[4] or 0, "overdue": row[5] or 0,
                "avg_progress": round(row[6] or 0),
                "average_timing_percent": average_timing,
                "average_delta_days": average_delta_days,
                "early": early, "on_time": on_time, "late": late,
                "early_rate": early_rate, "on_time_rate": on_time_rate, "late_rate": late_rate,
                "completion_rate": completion_rate,
                "reports": report_row[0] or 0,
                "last_report_at": datetime.fromisoformat(report_row[1]) if report_row[1] else None,
                "reported_tasks": report_row[2] or 0}

    async def meetings_between(self, user_id: int, starts_at: datetime, ends_at: datetime) -> list[Meeting]:
        await self.materialize_meeting_recurrences(ends_at)
        async with self._connect() as db:
            rows = await (await db.execute(
                "SELECT id,user_id,title,starts_at,ends_at,source_text FROM meetings WHERE user_id=? AND starts_at>=? AND starts_at<? ORDER BY starts_at",
                (user_id, starts_at.isoformat(), ends_at.isoformat()),
            )).fetchall()
        return [Meeting(r[0], r[1], r[2], datetime.fromisoformat(r[3]), datetime.fromisoformat(r[4]), r[5]) for r in rows]

    async def meetings_on(self, user_id: int, day: date) -> list[Meeting]:
        start = datetime.combine(day, datetime.min.time())
        end = start + timedelta(days=1)
        async with self._connect() as db:
            rows = await (await db.execute(
                "SELECT id,user_id,title,starts_at,ends_at,source_text FROM meetings WHERE user_id=? AND starts_at>=? AND starts_at<? ORDER BY starts_at",
                (user_id, start.isoformat(), end.isoformat()),
            )).fetchall()
        return [Meeting(r[0], r[1], r[2], datetime.fromisoformat(r[3]), datetime.fromisoformat(r[4]), r[5]) for r in rows]

    async def upcoming_meetings(self, user_id: int, now: datetime, limit: int = 20) -> list[Meeting]:
        await self.materialize_meeting_recurrences(now + timedelta(days=365))
        async with self._connect() as db:
            rows = await (await db.execute(
                """SELECT m.id,m.user_id,m.title,m.starts_at,m.ends_at,m.source_text,
                          o.series_id,s.active
                   FROM meetings m
                   LEFT JOIN meeting_recurrence_occurrences o ON o.meeting_id=m.id
                   LEFT JOIN meeting_recurrence_series s ON s.id=o.series_id
                   WHERE m.user_id=? AND m.starts_at>=?
                     AND (
                       o.series_id IS NULL OR m.id=(
                         SELECT m2.id
                         FROM meetings m2
                         JOIN meeting_recurrence_occurrences o2 ON o2.meeting_id=m2.id
                         WHERE m2.user_id=m.user_id
                           AND o2.series_id=o.series_id
                           AND m2.starts_at>=?
                         ORDER BY m2.starts_at,m2.id LIMIT 1
                       )
                     )
                   ORDER BY m.starts_at,m.id LIMIT ?""",
                (user_id, now.isoformat(), now.isoformat(), limit),
            )).fetchall()
        return [
            Meeting(
                r[0], r[1], r[2], datetime.fromisoformat(r[3]),
                datetime.fromisoformat(r[4]), r[5], r[6], bool(r[7]) if r[6] else False,
            )
            for r in rows
        ]

    async def meetings_in_range(self, user_id: int, starts_at: datetime,
                                ends_at: datetime, limit: int = 100) -> list[Meeting]:
        """Return meetings in a window, showing one occurrence per series."""
        await self.materialize_meeting_recurrences(ends_at)
        async with self._connect() as db:
            rows = await (await db.execute(
                """SELECT m.id,m.user_id,m.title,m.starts_at,m.ends_at,m.source_text,
                          o.series_id,s.active
                   FROM meetings m
                   LEFT JOIN meeting_recurrence_occurrences o ON o.meeting_id=m.id
                   LEFT JOIN meeting_recurrence_series s ON s.id=o.series_id
                   WHERE m.user_id=? AND m.starts_at>=? AND m.starts_at<?
                     AND (
                       o.series_id IS NULL OR m.id=(
                         SELECT m2.id
                         FROM meetings m2
                         JOIN meeting_recurrence_occurrences o2 ON o2.meeting_id=m2.id
                         WHERE m2.user_id=m.user_id AND o2.series_id=o.series_id
                           AND m2.starts_at>=? AND m2.starts_at<?
                         ORDER BY m2.starts_at,m2.id LIMIT 1
                       )
                     )
                   ORDER BY m.starts_at,m.id LIMIT ?""",
                (user_id, starts_at.isoformat(), ends_at.isoformat(),
                 starts_at.isoformat(), ends_at.isoformat(), limit),
            )).fetchall()
        return [
            Meeting(
                r[0], r[1], r[2], datetime.fromisoformat(r[3]),
                datetime.fromisoformat(r[4]), r[5], r[6], bool(r[7]) if r[6] else False,
            )
            for r in rows
        ]

    async def meeting(self, meeting_id: int, user_id: int) -> Meeting | None:
        async with self._connect() as db:
            row = await (await db.execute(
                """SELECT id,user_id,title,starts_at,ends_at,source_text
                   FROM meetings WHERE id=? AND user_id=?""", (meeting_id, user_id)
            )).fetchone()
        return Meeting(row[0], row[1], row[2], datetime.fromisoformat(row[3]), datetime.fromisoformat(row[4]), row[5]) if row else None

    async def overlapping_meetings(self, user_id: int, starts_at: datetime, ends_at: datetime,
                                   exclude_id: int | None = None) -> list[Meeting]:
        query = """SELECT id,user_id,title,starts_at,ends_at,source_text
                   FROM meetings WHERE user_id=? AND starts_at<? AND ends_at>?"""
        params: list[object] = [user_id, ends_at.isoformat(), starts_at.isoformat()]
        if exclude_id is not None:
            query += " AND id<>?"
            params.append(exclude_id)
        query += " ORDER BY starts_at"
        async with self._connect() as db:
            rows = await (await db.execute(query, params)).fetchall()
        return [Meeting(r[0], r[1], r[2], datetime.fromisoformat(r[3]), datetime.fromisoformat(r[4]), r[5]) for r in rows]

    async def move_meeting_segments(self, meeting_id: int, user_id: int, segments: list) -> bool:
        """Move related sessions together and queue every Google update atomically."""
        async with self._connect() as connection:
            row = await (await connection.execute(
                "SELECT title,source_text,starts_at FROM meetings WHERE id=? AND user_id=?",
                (meeting_id, user_id),
            )).fetchone()
            if not row:
                return False
            rows = [(meeting_id,)]
            if row[1]:
                rows = await (await connection.execute(
                    "SELECT id FROM meetings WHERE user_id=? AND title=? AND source_text=? AND substr(starts_at,1,10)=? ORDER BY starts_at,id",
                    (user_id, row[0], row[1], row[2][:10]),
                )).fetchall()
            if len(rows) > len(segments):
                return False
            for index, (start, end) in enumerate(segments):
                if index < len(rows):
                    item_id = rows[index][0]
                    await connection.execute("UPDATE meetings SET starts_at=?,ends_at=? WHERE id=? AND user_id=?", (start, end, item_id, user_id))
                else:
                    cursor = await connection.execute("INSERT INTO meetings(user_id,title,starts_at,ends_at,source_text) VALUES(?,?,?,?,?)", (user_id, row[0], start, end, row[1]))
                    item_id = cursor.lastrowid
                await self._queue_google_upsert(connection, user_id, item_id)
            await connection.commit()
            return True

    async def move_meeting(self, meeting_id: int, user_id: int, starts_at: datetime, ends_at: datetime) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE meetings SET starts_at=?, ends_at=? WHERE id=? AND user_id=?",
                (starts_at.isoformat(), ends_at.isoformat(), meeting_id, user_id),
            )
            if cursor.rowcount:
                await self._queue_google_upsert(db, user_id, meeting_id)
            await db.commit()
            return cursor.rowcount > 0

    async def delete_meeting(self, meeting_id: int, user_id: int) -> bool:
        async with self._connect() as db:
            exists = await (await db.execute(
                "SELECT 1 FROM meetings WHERE id=? AND user_id=?", (meeting_id, user_id)
            )).fetchone()
            if not exists:
                return False
            await self._queue_google_delete(db, user_id, meeting_id)
            await db.execute(
                "UPDATE meeting_recurrence_occurrences SET skipped=1 WHERE meeting_id=?",
                (meeting_id,),
            )
            cursor = await db.execute(
                "DELETE FROM meetings WHERE id=? AND user_id=?", (meeting_id, user_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def create_google_oauth_state(
        self, state: str, user_id: int, expires_at: datetime,
    ) -> None:
        async with self._connect() as conn:
            await conn.execute("DELETE FROM google_oauth_states WHERE expires_at<=CURRENT_TIMESTAMP")
            await conn.execute(
                "INSERT INTO google_oauth_states(state,user_id,expires_at) VALUES(?,?,?)",
                (state, user_id, expires_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
            )
            await conn.commit()

    async def consume_google_oauth_state(self, state: str) -> int | None:
        async with self._connect() as conn:
            row = await (await conn.execute(
                "SELECT user_id FROM google_oauth_states WHERE state=? AND expires_at>CURRENT_TIMESTAMP",
                (state,),
            )).fetchone()
            await conn.execute("DELETE FROM google_oauth_states WHERE state=?", (state,))
            await conn.commit()
        return int(row[0]) if row else None

    async def google_connection(self, user_id: int) -> dict | None:
        async with self._connect() as conn:
            row = await (await conn.execute(
                """SELECT encrypted_refresh_token,account_email,calendar_id,calendar_name,enabled
                   FROM google_connections WHERE user_id=?""",
                (user_id,),
            )).fetchone()
        if not row:
            return None
        return {
            "encrypted_refresh_token": row[0], "account_email": row[1],
            "calendar_id": row[2], "calendar_name": row[3], "enabled": bool(row[4]),
        }

    async def save_google_connection(
        self, user_id: int, encrypted_refresh_token: str, account_email: str = "",
    ) -> None:
        async with self._connect() as conn:
            await conn.execute(
                """INSERT INTO google_connections
                   (user_id,encrypted_refresh_token,account_email,enabled)
                   VALUES(?,?,?,1)
                   ON CONFLICT(user_id) DO UPDATE SET
                     encrypted_refresh_token=excluded.encrypted_refresh_token,
                     account_email=excluded.account_email,enabled=1,
                     updated_at=CURRENT_TIMESTAMP""",
                (user_id, encrypted_refresh_token, account_email[:320]),
            )
            await conn.commit()

    async def disconnect_google_calendar(self, user_id: int) -> bool:
        async with self._connect() as conn:
            cur = await conn.execute("DELETE FROM google_connections WHERE user_id=?", (user_id,))
            await conn.execute("DELETE FROM google_sync_queue WHERE user_id=?", (user_id,))
            await conn.execute("DELETE FROM google_meeting_links WHERE user_id=?", (user_id,))
            await conn.commit()
        return cur.rowcount > 0

    async def select_google_calendar(
        self, user_id: int, calendar_id: str, calendar_name: str,
    ) -> bool:
        async with self._connect() as conn:
            connection = await (await conn.execute(
                "SELECT calendar_id FROM google_connections WHERE user_id=? AND enabled=1",
                (user_id,),
            )).fetchone()
            if not connection:
                return False
            old_calendar = str(connection[0])
            if old_calendar != calendar_id:
                links = await (await conn.execute(
                    "SELECT meeting_id,calendar_id,google_event_id FROM google_meeting_links WHERE user_id=?",
                    (user_id,),
                )).fetchall()
                for meeting_id, old_id, event_id in links:
                    await conn.execute(
                        """INSERT INTO google_sync_queue
                           (user_id,meeting_id,action,calendar_id,google_event_id)
                           VALUES(?,?,'delete',?,?)
                           ON CONFLICT(user_id,meeting_id,action,calendar_id) DO UPDATE SET
                             google_event_id=excluded.google_event_id,attempts=0,
                             available_at=CURRENT_TIMESTAMP,last_error=''""",
                        (user_id, int(meeting_id), str(old_id), str(event_id)),
                    )
                await conn.execute("DELETE FROM google_meeting_links WHERE user_id=?", (user_id,))
            await conn.execute(
                """UPDATE google_connections SET calendar_id=?,calendar_name=?,
                   updated_at=CURRENT_TIMESTAMP WHERE user_id=?""",
                (calendar_id, calendar_name[:200], user_id),
            )
            await conn.commit()
        await self.queue_google_resync(user_id)
        return True

    async def queue_google_resync(self, user_id: int) -> int:
        async with self._connect() as conn:
            rows = await (await conn.execute(
                """SELECT id FROM meetings
                   WHERE user_id=?
                     AND source_text NOT LIKE 'واردشده از Google Calendar%'""",
                (user_id,),
            )).fetchall()
            for row in rows:
                await self._queue_google_upsert(conn, user_id, int(row[0]))
            await conn.commit()
        return len(rows)

    async def queue_unlinked_google_meetings(self, user_id: int) -> int:
        """Queue only bot-originated meetings that were not matched during import."""
        async with self._connect() as conn:
            rows = await (await conn.execute(
                """SELECT m.id FROM meetings m
                   LEFT JOIN google_meeting_links l ON l.meeting_id=m.id
                   WHERE m.user_id=?
                     AND m.source_text NOT LIKE 'واردشده از Google Calendar%'
                     AND l.meeting_id IS NULL""",
                (user_id,),
            )).fetchall()
            for row in rows:
                await self._queue_google_upsert(conn, user_id, int(row[0]))
            await conn.commit()
        return len(rows)

    async def pending_google_syncs(self, limit: int = 50) -> list[dict]:
        async with self._connect() as conn:
            rows = await (await conn.execute(
                """SELECT q.id,q.user_id,q.meeting_id,q.action,q.calendar_id,
                          q.google_event_id,q.attempts
                   FROM google_sync_queue q
                   JOIN google_connections c ON c.user_id=q.user_id AND c.enabled=1
                   WHERE q.available_at<=CURRENT_TIMESTAMP
                   ORDER BY q.id LIMIT ?""",
                (limit,),
            )).fetchall()
        return [
            {"id": int(r[0]), "user_id": int(r[1]), "meeting_id": int(r[2]),
             "action": r[3], "calendar_id": r[4], "google_event_id": r[5],
             "attempts": int(r[6])}
            for r in rows
        ]

    async def google_meeting_link(self, meeting_id: int, user_id: int) -> dict | None:
        async with self._connect() as conn:
            row = await (await conn.execute(
                """SELECT calendar_id,google_event_id FROM google_meeting_links
                   WHERE meeting_id=? AND user_id=?""",
                (meeting_id, user_id),
            )).fetchone()
        return {"calendar_id": row[0], "google_event_id": row[1]} if row else None

    async def import_google_event(
        self, user_id: int, calendar_id: str, google_event_id: str,
        title: str, starts_at: datetime, ends_at: datetime, source_text: str,
    ) -> tuple[int, bool]:
        """Create or refresh a local meeting for a Google event without echoing it back."""
        async with self._connect() as conn:
            row = await (await conn.execute(
                """SELECT l.meeting_id,m.source_text FROM google_meeting_links l
                   JOIN meetings m ON m.id=l.meeting_id
                   WHERE l.user_id=? AND l.calendar_id=? AND l.google_event_id=?""",
                (user_id, calendar_id, google_event_id),
            )).fetchone()
            if row:
                meeting_id = int(row[0])
                # Older imports may have created a second local row for an event
                # that already existed in the bot. Prefer the original bot row,
                # move the Google link to it, and remove only the imported copy.
                if str(row[1]).startswith("واردشده از Google Calendar"):
                    original = await (await conn.execute(
                        """SELECT m.id FROM meetings m
                           LEFT JOIN google_meeting_links l ON l.meeting_id=m.id
                           WHERE m.user_id=? AND m.id<>? AND m.title=?
                             AND m.starts_at=? AND m.ends_at=?
                             AND m.source_text NOT LIKE 'واردشده از Google Calendar%'
                             AND l.meeting_id IS NULL
                           ORDER BY m.id LIMIT 1""",
                        (user_id, meeting_id, title, starts_at.isoformat(), ends_at.isoformat()),
                    )).fetchone()
                    if original:
                        original_id = int(original[0])
                        await conn.execute(
                            "DELETE FROM google_meeting_links WHERE meeting_id=?", (meeting_id,)
                        )
                        await conn.execute(
                            """INSERT INTO google_meeting_links
                               (meeting_id,user_id,calendar_id,google_event_id,synced_at)
                               VALUES(?,?,?,?,CURRENT_TIMESTAMP)""",
                            (original_id, user_id, calendar_id, google_event_id),
                        )
                        await conn.execute(
                            "DELETE FROM google_sync_queue WHERE user_id=? AND meeting_id=?",
                            (user_id, original_id),
                        )
                        await conn.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))
                        await conn.commit()
                        return original_id, False
                await conn.execute(
                    """UPDATE meetings SET title=?,starts_at=?,ends_at=?,source_text=?
                       WHERE id=? AND user_id=?""",
                    (title, starts_at.isoformat(), ends_at.isoformat(), source_text,
                     meeting_id, user_id),
                )
                await conn.execute(
                    "DELETE FROM google_sync_queue WHERE user_id=? AND meeting_id=? AND action='upsert'",
                    (user_id, meeting_id),
                )
                await conn.commit()
                return meeting_id, False

            # If the event is already represented by an unlinked local meeting,
            # attach the Google id instead of creating or uploading a duplicate.
            existing = await (await conn.execute(
                """SELECT m.id FROM meetings m
                   LEFT JOIN google_meeting_links l ON l.meeting_id=m.id
                   WHERE m.user_id=? AND m.title=? AND m.starts_at=? AND m.ends_at=?
                     AND l.meeting_id IS NULL
                   ORDER BY CASE WHEN m.source_text LIKE 'واردشده از Google Calendar%'
                                 THEN 1 ELSE 0 END, m.id
                   LIMIT 1""",
                (user_id, title, starts_at.isoformat(), ends_at.isoformat()),
            )).fetchone()
            if existing:
                meeting_id = int(existing[0])
                await conn.execute(
                    """INSERT INTO google_meeting_links
                       (meeting_id,user_id,calendar_id,google_event_id,synced_at)
                       VALUES(?,?,?,?,CURRENT_TIMESTAMP)""",
                    (meeting_id, user_id, calendar_id, google_event_id),
                )
                await conn.execute(
                    "DELETE FROM google_sync_queue WHERE user_id=? AND meeting_id=?",
                    (user_id, meeting_id),
                )
                await conn.commit()
                return meeting_id, False

            cursor = await conn.execute(
                """INSERT INTO meetings(user_id,title,starts_at,ends_at,source_text)
                   VALUES(?,?,?,?,?)""",
                (user_id, title, starts_at.isoformat(), ends_at.isoformat(), source_text),
            )
            meeting_id = int(cursor.lastrowid)
            await conn.execute(
                """INSERT INTO google_meeting_links
                   (meeting_id,user_id,calendar_id,google_event_id,synced_at)
                   VALUES(?,?,?,?,CURRENT_TIMESTAMP)""",
                (meeting_id, user_id, calendar_id, google_event_id),
            )
            await conn.commit()
            return meeting_id, True

    async def complete_google_sync(
        self, queue_id: int, meeting_id: int, user_id: int,
        calendar_id: str, google_event_id: str, action: str,
    ) -> None:
        async with self._connect() as conn:
            if action == "upsert":
                await conn.execute(
                    """INSERT INTO google_meeting_links
                       (meeting_id,user_id,calendar_id,google_event_id,synced_at)
                       VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                       ON CONFLICT(meeting_id) DO UPDATE SET
                         user_id=excluded.user_id,calendar_id=excluded.calendar_id,
                         google_event_id=excluded.google_event_id,synced_at=CURRENT_TIMESTAMP""",
                    (meeting_id, user_id, calendar_id, google_event_id),
                )
            else:
                await conn.execute(
                    "DELETE FROM google_meeting_links WHERE meeting_id=? AND user_id=? AND calendar_id=?",
                    (meeting_id, user_id, calendar_id),
                )
            await conn.execute("DELETE FROM google_sync_queue WHERE id=?", (queue_id,))
            await conn.commit()

    async def retry_google_sync(self, queue_id: int, error: str, attempts: int) -> None:
        delay = min(3600, 30 * (2 ** min(attempts, 7)))
        async with self._connect() as conn:
            await conn.execute(
                """UPDATE google_sync_queue SET attempts=attempts+1,last_error=?,
                   available_at=datetime('now', ?) WHERE id=?""",
                (error[:1000], f"+{delay} seconds", queue_id),
            )
            await conn.commit()

    async def stats(self, user_id: int) -> dict[str, int]:
        async with self._connect() as db:
            row = await (await db.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN status='open' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status='done' THEN 1 ELSE 0 END),
                       COALESCE(AVG(CASE WHEN status='open' THEN progress END),0),
                       SUM(CASE WHEN status='open' AND deadline<CURRENT_TIMESTAMP THEN 1 ELSE 0 END)
                FROM tasks WHERE user_id=?
            """, (user_id,))).fetchone()
        return {"all": row[0] or 0, "open": row[1] or 0, "done": row[2] or 0,
                "avg": round(row[3] or 0), "overdue": row[4] or 0}

    async def dashboard_stats(self, user_id: int, now: datetime) -> dict:
        week_end = now + timedelta(days=7)
        async with self._connect() as db:
            summary = await (await db.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN status='open' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status='done' THEN 1 ELSE 0 END),
                       COALESCE(AVG(CASE WHEN status='open' THEN progress END),0),
                       SUM(CASE WHEN status='open' AND deadline<? THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status='open' AND deadline>=? AND deadline<=? THEN 1 ELSE 0 END)
                FROM tasks WHERE user_id=?
            """, (now.isoformat(), now.isoformat(), week_end.isoformat(), user_id))).fetchone()
            priorities = await (await db.execute("""
                SELECT priority,COUNT(*) FROM tasks
                WHERE user_id=? AND status='open' GROUP BY priority
            """, (user_id,))).fetchall()
            urgent = await (await db.execute("""
                SELECT id,title,deadline,progress FROM tasks
                WHERE user_id=? AND status='open'
                ORDER BY CASE WHEN deadline<? THEN 0 ELSE 1 END,priority,deadline LIMIT 3
            """, (user_id, now.isoformat()))).fetchall()
        all_count, open_count, done_count = summary[0] or 0, summary[1] or 0, summary[2] or 0
        completion_rate = round(done_count * 100 / all_count) if all_count else 0
        return {
            "all": all_count, "open": open_count, "done": done_count,
            "avg": round(summary[3] or 0), "overdue": summary[4] or 0,
            "due_week": summary[5] or 0, "completion_rate": completion_rate,
            "priorities": {row[0]: row[1] for row in priorities},
            "attention": [(row[0], row[1], datetime.fromisoformat(row[2]), row[3]) for row in urgent],
        }

    async def weekly_summary(self, user_id: int, starts_at: datetime, ends_at: datetime) -> dict:
        """Return task movement and weekly management metrics."""
        start, end = starts_at.isoformat(), ends_at.isoformat()
        async with self._connect() as db:
            metrics = await (await db.execute("""
                SELECT SUM(created_at>=? AND created_at<?),
                       SUM(completed_at>=? AND completed_at<?),
                       SUM(status!='done' AND deadline<?)
                FROM tasks WHERE user_id=? AND created_at<?
            """, (start, end, start, end, end, user_id, end))).fetchone()
            reports = await (await db.execute("""SELECT COUNT(*) FROM task_reports
                WHERE manager_id=? AND created_at>=? AND created_at<?""", (user_id, start, end))).fetchone()
            rows = await (await db.execute("""
                SELECT t.id,t.title,t.progress,t.status,t.assignee,
                       COALESCE(MAX(CASE WHEN h.changed_at<? THEN h.progress END),0),
                       MAX(CASE WHEN h.changed_at>=? AND h.changed_at<? THEN h.progress END)
                FROM tasks t LEFT JOIN task_progress_history h ON h.task_id=t.id AND h.manager_id=t.user_id
                WHERE t.user_id=? AND t.created_at<? AND (t.completed_at IS NULL OR t.completed_at>=?)
                GROUP BY t.id ORDER BY t.priority,t.deadline,t.id
            """, (start, start, end, user_id, end, start))).fetchall()
        progressed, stagnant = [], []
        for task_id, title, current, status, assignee, before, during in rows:
            value = current if during is None else during
            item = {"id": task_id, "title": title, "progress": int(value or 0),
                    "gain": max(0, int(value or 0) - int(before or 0)), "status": status, "assignee": assignee}
            (progressed if item["gain"] else stagnant).append(item)
        return {"created": metrics[0] or 0, "completed": metrics[1] or 0, "overdue": metrics[2] or 0,
                "reports": reports[0] or 0, "progressed": progressed, "stagnant": stagnant}
