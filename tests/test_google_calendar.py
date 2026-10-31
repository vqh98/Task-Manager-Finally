import tempfile
import unittest
import json
import httpx
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from app.db import Database
from app.google_calendar import GoogleCalendarService, OAUTH_STATE_TTL


class GoogleCalendarIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        await self.db.init()
        await self.db.seed_owners(frozenset({1}))
        self.settings = SimpleNamespace(
            bot_token="123:test-secret",
            timezone=ZoneInfo("Asia/Tehran"),
            google_client_id="client-id",
            google_client_secret="client-secret",
            google_redirect_uri="https://calendar.example.test/google/oauth/callback",
            google_oauth_host="127.0.0.1",
            google_oauth_port=8080,
            google_token_encryption_key="",
        )
        self.service = GoogleCalendarService(self.settings, self.db)
        await self.db.save_google_connection(
            1, self.service.encrypt_refresh_token("refresh-token"), "user@example.com"
        )

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_oauth_state_is_short_lived_and_single_use(self):
        expiry = datetime.now().astimezone() + timedelta(minutes=10)
        await self.db.create_google_oauth_state("safe-state", 1, expiry)
        self.assertEqual(await self.db.consume_google_oauth_state("safe-state"), 1)
        self.assertIsNone(await self.db.consume_google_oauth_state("safe-state"))

    async def test_oauth_link_allows_slow_mobile_consent_flow(self):
        self.assertEqual(OAUTH_STATE_TTL, timedelta(minutes=30))
        url = await self.service.authorization_url(1)
        self.assertIn("state=", url)

    async def test_refresh_token_is_encrypted_at_rest(self):
        encrypted = self.service.encrypt_refresh_token("very-secret-token")
        self.assertNotIn("very-secret-token", encrypted)
        self.assertEqual(self.service.decrypt_refresh_token(encrypted), "very-secret-token")

    async def test_installed_client_file_enables_manual_oauth_without_a_domain(self):
        client_file = Path(self.temp_dir.name) / "credentials.json"
        client_file.write_text(json.dumps({
            "installed": {
                "client_id": "desktop-client",
                "client_secret": "desktop-secret",
                "redirect_uris": ["http://localhost"],
            }
        }), encoding="utf-8")
        settings = SimpleNamespace(
            bot_token="123:test-secret", timezone=ZoneInfo("Asia/Tehran"),
            google_client_id="", google_client_secret="", google_redirect_uri="",
            google_oauth_client_file=str(client_file), google_token_encryption_key="",
        )
        service = GoogleCalendarService(settings, self.db)
        self.assertTrue(service.configured)
        self.assertTrue(service.manual_authorization)
        self.assertEqual(service.redirect_uri, "http://localhost")

    async def test_public_oauth_pages_are_complete_and_escape_support_email(self):
        self.settings.google_public_base_url = "https://calendar.example.test"
        self.settings.google_support_email = 'support+calendar@example.test<script>'
        service = GoogleCalendarService(self.settings, self.db)

        homepage = service.homepage_html()
        privacy = service.privacy_html()
        terms = service.terms_html()

        self.assertIn('lang="fa" dir="rtl"', homepage)
        self.assertIn("https://calendar.example.test/privacy", homepage)
        self.assertIn("Google API Services User Data Policy", privacy)
        self.assertIn("support+calendar@example.test&lt;script&gt;", privacy)
        self.assertNotIn("<script>", privacy)
        self.assertIn("Google Calendar", terms)

    async def test_public_server_routes_and_incomplete_callback_response(self):
        self.settings.google_oauth_port = 0
        service = GoogleCalendarService(self.settings, self.db)
        bot = AsyncMock()
        await service.start_callback_server(bot)
        try:
            site = next(iter(service._runner.sites))
            port = site._server.sockets[0].getsockname()[1]
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                for path in ("/", "/privacy", "/terms"):
                    response = await client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("text/html", response.headers["content-type"])
                callback = await client.get("/google/oauth/callback")
                self.assertEqual(callback.status_code, 400)
                self.assertIn("text/html", callback.headers["content-type"])
        finally:
            await service.stop_callback_server()

    async def test_manual_oauth_still_serves_pages_but_disables_web_callback(self):
        settings = SimpleNamespace(
            bot_token="123:test-secret", timezone=ZoneInfo("Asia/Tehran"),
            google_client_id="desktop-client", google_client_secret="desktop-secret",
            google_redirect_uri="http://localhost", google_oauth_client_file="",
            google_token_encryption_key="", google_oauth_host="127.0.0.1",
            google_oauth_port=0, google_public_base_url="",
            google_support_email="support@example.test",
        )
        service = GoogleCalendarService(settings, self.db)
        await service.start_callback_server(AsyncMock())
        try:
            site = next(iter(service._runner.sites))
            port = site._server.sockets[0].getsockname()[1]
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                self.assertEqual((await client.get("/")).status_code, 200)
                response = await client.get("/google/oauth/callback?state=x&code=y")
                self.assertEqual(response.status_code, 503)
                self.assertIn("موقتاً فعال نیست", response.text)
        finally:
            await service.stop_callback_server()

    async def test_create_move_and_delete_are_durable_and_idempotent(self):
        start = (datetime.now(ZoneInfo("Asia/Tehran")) + timedelta(days=1)).replace(
            hour=13, minute=0, second=0, microsecond=0
        )
        meeting_id = await self.db.add_meeting(
            1, "جلسه فروش", start, start + timedelta(hours=1), ""
        )
        event_id = self.service.deterministic_event_id(1, meeting_id)
        self.service._upsert_event = AsyncMock(return_value=event_id)

        self.assertEqual(await self.service.process_queue(), 1)
        link = await self.db.google_meeting_link(meeting_id, 1)
        self.assertEqual(link["google_event_id"], event_id)
        self.assertEqual(await self.service.process_queue(), 0)

        moved = start + timedelta(hours=2)
        await self.db.move_meeting(meeting_id, 1, moved, moved + timedelta(hours=1))
        await self.db.move_meeting(meeting_id, 1, moved + timedelta(hours=1), moved + timedelta(hours=2))
        pending = await self.db.pending_google_syncs()
        self.assertEqual(len(pending), 1, "repeated edits must coalesce into one upsert")
        self.assertEqual(await self.service.process_queue(), 1)
        self.assertEqual(self.service._upsert_event.await_count, 2)

        self.service._delete_event = AsyncMock(return_value=None)
        self.assertTrue(await self.db.delete_meeting(meeting_id, 1))
        pending = await self.db.pending_google_syncs()
        self.assertEqual([(item["action"], item["google_event_id"]) for item in pending],
                         [("delete", event_id)])
        self.assertEqual(await self.service.process_queue(), 1)
        self.service._delete_event.assert_awaited_once_with(1, "primary", event_id)
        self.assertIsNone(await self.db.google_meeting_link(meeting_id, 1))

    async def test_deleting_before_first_sync_does_not_create_an_event(self):
        start = datetime.now(ZoneInfo("Asia/Tehran")) + timedelta(days=1)
        meeting_id = await self.db.add_meeting(1, "جلسه کوتاه", start, start + timedelta(hours=1), "")
        await self.db.delete_meeting(meeting_id, 1)
        pending = await self.db.pending_google_syncs()
        self.assertEqual([item["action"] for item in pending], ["delete"])
        self.assertEqual(
            pending[0]["google_event_id"], "",
            "the worker derives the deterministic event id when no link exists",
        )

    async def test_calendar_change_deletes_old_copy_and_queues_new_copy(self):
        start = datetime.now(ZoneInfo("Asia/Tehran")) + timedelta(days=1)
        meeting_id = await self.db.add_meeting(1, "جلسه انتقالی", start, start + timedelta(hours=1), "")
        event_id = self.service.deterministic_event_id(1, meeting_id)
        self.service._upsert_event = AsyncMock(return_value=event_id)
        await self.service.process_queue()

        self.assertTrue(await self.db.select_google_calendar(1, "team@example.com", "تقویم تیم"))
        jobs = await self.db.pending_google_syncs()
        self.assertEqual({job["action"] for job in jobs}, {"delete", "upsert"})
        deletion = next(job for job in jobs if job["action"] == "delete")
        self.assertEqual(deletion["calendar_id"], "primary")
        connection = await self.db.google_connection(1)
        self.assertEqual(connection["calendar_id"], "team@example.com")

    async def test_import_existing_google_events_is_idempotent(self):
        response = SimpleNamespace(status_code=200, json=lambda: {"items": [{
            "id": "existing-google-event",
            "summary": "جلسه از قبل ثبت‌شده",
            "description": "توضیحات گوگل",
            "location": "اتاق جلسه",
            "status": "confirmed",
            "start": {"dateTime": "2026-10-02T10:00:00+03:30"},
            "end": {"dateTime": "2026-10-02T11:00:00+03:30"},
        }]})
        self.service._request = AsyncMock(return_value=response)

        first = await self.service.import_events(1, "primary")
        second = await self.service.import_events(1, "primary")

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 1)
        meetings = await self.db.meetings_between(
            1,
            datetime(2026, 10, 2, 0, 0, tzinfo=ZoneInfo("Asia/Tehran")),
            datetime(2026, 10, 3, 0, 0, tzinfo=ZoneInfo("Asia/Tehran")),
        )
        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0].title, "جلسه از قبل ثبت‌شده")
        self.assertIn("اتاق جلسه", meetings[0].source_text)

    async def test_import_links_equivalent_bot_meeting_without_duplicate(self):
        start = datetime(2026, 10, 4, 9, 0, tzinfo=ZoneInfo("Asia/Tehran"))
        meeting_id = await self.db.add_meeting(
            1, "جلسه مشترک", start, start + timedelta(hours=1), "ثبت‌شده در ربات"
        )
        response = SimpleNamespace(status_code=200, json=lambda: {"items": [{
            "id": "already-in-google", "summary": "جلسه مشترک", "status": "confirmed",
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
        }]})
        self.service._request = AsyncMock(return_value=response)

        result = await self.service.import_events(1, "primary")

        self.assertEqual(result, {"created": 0, "updated": 1, "skipped": 0})
        self.assertEqual(
            await self.db.google_meeting_link(meeting_id, 1),
            {"calendar_id": "primary", "google_event_id": "already-in-google"},
        )
        self.assertEqual(await self.db.pending_google_syncs(), [])

    async def test_resync_queues_all_bot_meetings_but_not_google_imports(self):
        past = datetime(2025, 1, 2, 9, 0, tzinfo=ZoneInfo("Asia/Tehran"))
        bot_meeting = await self.db.add_meeting(
            1, "جلسه قدیمی ربات", past, past + timedelta(hours=1), "ثبت‌شده در ربات"
        )
        await self.db.import_google_event(
            1, "primary", "google-only", "رویداد گوگل", past,
            past + timedelta(hours=1), "واردشده از Google Calendar",
        )
        async with self.db._connect() as conn:
            await conn.execute("DELETE FROM google_sync_queue")
            await conn.commit()

        count = await self.db.queue_google_resync(1)
        jobs = await self.db.pending_google_syncs()

        self.assertEqual(count, 1)
        self.assertEqual([job["meeting_id"] for job in jobs], [bot_meeting])

    async def test_initial_bidirectional_sync_imports_before_upload(self):
        start = datetime(2026, 10, 6, 14, 0, tzinfo=ZoneInfo("Asia/Tehran"))
        meeting_id = await self.db.add_meeting(
            1, "جلسه مشترک اولیه", start, start + timedelta(hours=1), "از ربات"
        )
        response = SimpleNamespace(status_code=200, json=lambda: {"items": [{
            "id": "preexisting-event", "summary": "جلسه مشترک اولیه",
            "status": "confirmed", "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
        }]})
        self.service._request = AsyncMock(return_value=response)

        result = await self.service.initial_bidirectional_sync(1)

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["queued"], 0)
        self.assertEqual(result["processed"], 0)
        self.assertEqual(
            await self.db.google_meeting_link(meeting_id, 1),
            {"calendar_id": "primary", "google_event_id": "preexisting-event"},
        )
        # Matching happened before upload, so no POST/PATCH request was needed.
        self.assertEqual(self.service._request.await_count, 1)
        self.assertEqual(self.service._request.await_args.args[1], "GET")


if __name__ == "__main__":
    unittest.main()
