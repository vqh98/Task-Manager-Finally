from datetime import datetime, timedelta, timezone

import pytest

from app.db import Database
from app.platform_linking import PlatformRecipientMiddleware


@pytest.mark.asyncio
async def test_platform_link_is_cross_platform_one_time_and_shared(tmp_path):
    db = Database(tmp_path / "link.db")
    await db.init()
    telegram_id = 111
    bale_id = 222
    assert await db.canonical_platform_user_id("telegram", telegram_id) == telegram_id
    unlinked = await db.canonical_platform_user_id("bale", bale_id)
    assert unlinked != telegram_id

    await db.create_platform_link_code(
        telegram_id, "telegram", "12345678",
        datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    assert await db.consume_platform_link_code(
        "12345678", "telegram", 333, None, "same platform"
    ) is None
    assert await db.consume_platform_link_code(
        "12345678", "bale", bale_id, "User", "Bale User"
    ) == telegram_id
    assert await db.canonical_platform_user_id("bale", bale_id) == telegram_id
    assert await db.platform_recipient("bale", telegram_id) == bale_id
    assert await db.consume_platform_link_code(
        "12345678", "bale", 444, None, "replay"
    ) is None


@pytest.mark.asyncio
async def test_platform_link_expiry_and_disconnect(tmp_path):
    db = Database(tmp_path / "link.db")
    await db.init()
    await db.canonical_platform_user_id("telegram", 111)
    await db.create_platform_link_code(
        111, "telegram", "87654321",
        datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert await db.consume_platform_link_code(
        "87654321", "bale", 222, None, "expired"
    ) is None

    await db.create_platform_link_code(
        111, "telegram", "11112222",
        datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    assert await db.consume_platform_link_code(
        "11112222", "bale", 222, None, "linked"
    ) == 111
    assert await db.unlink_platform(111, "telegram") is False
    assert await db.unlink_platform(111, "bale") is True
    assert await db.platform_recipient("bale", 111) is None


@pytest.mark.asyncio
async def test_bale_outbound_rejects_unlinked_canonical_recipient(tmp_path):
    db = Database(tmp_path / "link.db")
    await db.init()
    middleware = PlatformRecipientMiddleware(db, "bale")

    class Method:
        chat_id = 111

    async def should_not_run(bot, method):  # pragma: no cover
        raise AssertionError("unlinked recipient reached transport")

    with pytest.raises(RuntimeError, match="no linked bale recipient"):
        await middleware(should_not_run, None, Method())
