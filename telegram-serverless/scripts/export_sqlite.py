"""Export the production SQLite database as tgcloud import batches.

Output is JSON Lines. Each line is a complete argument for
`tgcloud run lib/import-data`. OAuth refresh tokens remain encrypted exactly as
stored; this script never decrypts or logs them separately.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


TABLES = (
    "tasks", "task_deadline_alerts", "meetings", "reminders",
    "meeting_recurrence_series", "meeting_recurrence_occurrences", "users",
    "access_codes", "access_code_usages", "team_members", "manager_profiles",
    "event_shares", "task_shares", "shared_alert_settings", "shared_alert_log",
    "task_reports", "telegram_accounts", "task_progress_history", "user_settings",
    "meeting_quiet_hours", "notification_log", "app_migrations",
    "recurrence_series", "recurrence_occurrences", "google_connections",
    "google_oauth_states", "google_meeting_links", "google_sync_queue",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100:
        parser.error("--batch-size must be between 1 and 100")

    source = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        existing = {
            row[0] for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in TABLES:
            if table not in existing:
                continue
            batch: list[dict[str, object]] = []
            for row in source.execute(f'SELECT * FROM "{table}"'):
                batch.append(dict(row))
                if len(batch) == args.batch_size:
                    print(json.dumps({"table": table, "rows": batch}, ensure_ascii=False))
                    batch = []
            if batch:
                print(json.dumps({"table": table, "rows": batch}, ensure_ascii=False))
    finally:
        source.close()


if __name__ == "__main__":
    main()
