"""One-off/backfill helper for existing enabled Google Calendar connections."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_settings
from app.db import Database
from app.google_calendar import GoogleCalendarService


async def main() -> None:
    settings = load_settings()
    db = Database(settings.database_path)
    await db.init()
    service = GoogleCalendarService(settings, db)
    async with db._connect() as conn:
        rows = await (await conn.execute(
            "SELECT user_id,calendar_id FROM google_connections WHERE enabled=1"
        )).fetchall()
    total = {"created": 0, "updated": 0, "skipped": 0}
    queued = 0
    for user_id, calendar_id in rows:
        result = await service.import_events(int(user_id), str(calendar_id))
        for key in total:
            total[key] += result[key]
        queued += await db.queue_google_resync(int(user_id))
    # The running bot drains the durable queue in paced batches, which avoids
    # bursting into Google's per-user rate limit during a large first sync.
    sent = 0
    print(
        f"connections={len(rows)} created={total['created']} "
        f"updated={total['updated']} skipped={total['skipped']} "
        f"queued={queued} sent={sent}"
    )


if __name__ == "__main__":
    asyncio.run(main())
