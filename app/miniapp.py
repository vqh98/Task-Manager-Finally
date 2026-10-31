from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from app.db import Database


class MiniAppAPI:
    def __init__(self, settings, db: Database):
        self.settings = settings
        self.db = db
        self.assets = Path(__file__).with_name("miniapp_assets")

    async def user_id(self, request: web.Request) -> int:
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        platform = request.headers.get("X-Mini-App-Platform", "telegram").strip().lower()
        if platform not in {"telegram", "bale"}:
            raise web.HTTPUnauthorized(text="Unsupported Mini App platform")
        token = self.settings.bot_token if platform == "telegram" else self.settings.bale_bot_token
        if not token:
            raise web.HTTPServiceUnavailable(text=f"{platform} Mini App is not configured")
        platform_user_id = self.validate_init_data(init_data, token)
        canonical = await self.db.canonical_platform_user_id(platform, platform_user_id)
        if not await self.db.is_authorized(canonical, datetime.now(self.settings.timezone)):
            raise web.HTTPForbidden(text="Bot access is not active")
        return canonical

    @staticmethod
    def validate_init_data(init_data: str, bot_token: str, now: float | None = None) -> int:
        values = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = values.pop("hash", "")
        if not received_hash or not values.get("user"):
            raise web.HTTPUnauthorized(text="Telegram authentication required")
        check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received_hash, expected):
            raise web.HTTPUnauthorized(text="Invalid Telegram signature")
        try:
            auth_date = int(values.get("auth_date", "0"))
            telegram_id = int(json.loads(values["user"])["id"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise web.HTTPUnauthorized(text="Invalid Telegram identity")
        if abs((time.time() if now is None else now) - auth_date) > 86400:
            raise web.HTTPUnauthorized(text="Telegram authentication expired")
        return telegram_id

    @staticmethod
    def task_json(item) -> dict:
        return {
            "id": item.id, "title": item.title, "deadline": item.deadline.isoformat(),
            "priority": int(item.priority), "priority_label": item.priority.label,
            "assignee": item.assignee, "progress": item.progress, "status": item.status,
            "note": item.last_note,
        }

    @staticmethod
    def meeting_json(item) -> dict:
        return {
            "id": item.id, "title": item.title, "starts_at": item.starts_at.isoformat(),
            "ends_at": item.ends_at.isoformat(), "recurring": bool(item.recurrence_series_id),
        }

    async def index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(self.assets / "index.html")

    async def overview(self, request: web.Request) -> web.Response:
        user_id = await self.user_id(request)
        now = datetime.now(self.settings.timezone)
        stats = await self.db.dashboard_stats(user_id, now)
        meetings = await self.db.meetings_between(
            user_id, now.replace(hour=0, minute=0, second=0, microsecond=0),
            now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1),
        )
        reminders = await self.db.reminders(user_id, limit=5, now=now, within_days=7)
        google = await self.db.google_connection(user_id)
        attention = []
        for row in stats["attention"]:
            task = await self.db.task(row[0], user_id)
            if task:
                attention.append(self.task_json(task))
        return web.json_response({
            "stats": {key: value for key, value in stats.items() if key not in {"attention"}},
            "attention": attention,
            "today_meetings": [self.meeting_json(item) for item in meetings],
            "reminders_week": len(reminders),
            "google_connected": bool(google),
            "now": now.isoformat(),
        })

    async def tasks(self, request: web.Request) -> web.Response:
        user_id = await self.user_id(request)
        scope = request.query.get("scope", "open")
        filters = {} if scope == "all" else {"status": "done" if scope == "done" else "open"}
        rows = await self.db.query_tasks(user_id, filters, limit=50, now=datetime.now(self.settings.timezone))
        return web.json_response([self.task_json(item) for item in rows])

    async def create_task(self, request: web.Request) -> web.Response:
        user_id = await self.user_id(request); body = await request.json()
        try:
            deadline = datetime.fromisoformat(str(body["deadline"]))
            if deadline.tzinfo is None: deadline = deadline.replace(tzinfo=self.settings.timezone)
            task_id = await self.db.add_task(
                user_id, str(body["title"]), deadline, int(body.get("priority", 2)),
                str(body.get("assignee", "")), str(body.get("note", "")), changed_by=user_id,
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response({"id": task_id}, status=201)

    async def update_task(self, request: web.Request) -> web.Response:
        user_id = await self.user_id(request); task_id = int(request.match_info["task_id"])
        body = await request.json()
        if "progress" not in body:
            raise web.HTTPBadRequest(text="progress is required")
        progress = max(0, min(100, int(body["progress"])))
        ok = await self.db.update_task(task_id, user_id, "progress", progress, changed_by=user_id)
        if progress == 100 and ok:
            await self.db.update_task(task_id, user_id, "status", "done", changed_by=user_id)
        if not ok: raise web.HTTPNotFound()
        return web.json_response({"ok": True, "progress": progress})

    async def meetings(self, request: web.Request) -> web.Response:
        user_id = await self.user_id(request); now = datetime.now(self.settings.timezone)
        rows = await self.db.meetings_between(user_id, now, now + timedelta(days=90))
        return web.json_response([self.meeting_json(item) for item in rows])

    async def create_meeting(self, request: web.Request) -> web.Response:
        user_id = await self.user_id(request); body = await request.json()
        try:
            start = datetime.fromisoformat(str(body["starts_at"])); end = datetime.fromisoformat(str(body["ends_at"]))
            if start.tzinfo is None: start = start.replace(tzinfo=self.settings.timezone)
            if end.tzinfo is None: end = end.replace(tzinfo=self.settings.timezone)
            if end <= start: raise ValueError("زمان پایان باید بعد از شروع باشد")
            meeting_id = await self.db.add_meeting(user_id, str(body["title"]), start, end, "miniapp")
        except (KeyError, ValueError, TypeError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response({"id": meeting_id}, status=201)

    async def reminders(self, request: web.Request) -> web.Response:
        user_id = await self.user_id(request)
        rows = await self.db.reminders(user_id, limit=100)
        return web.json_response([{
            "id": item.id, "title": item.title, "due_at": item.due_at.isoformat(),
            "repeat_type": item.repeat_type,
        } for item in rows])

    async def create_reminder(self, request: web.Request) -> web.Response:
        user_id = await self.user_id(request); body = await request.json()
        try:
            due = datetime.fromisoformat(str(body["due_at"]))
            if due.tzinfo is None: due = due.replace(tzinfo=self.settings.timezone)
            reminder_id = await self.db.add_reminder(user_id, str(body["title"]), due)
        except (KeyError, ValueError, TypeError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response({"id": reminder_id}, status=201)

    def register(self, app: web.Application) -> None:
        app.router.add_get("/miniapp", self.index)
        app.router.add_get("/miniapp/", self.index)
        app.router.add_static("/miniapp/assets/", self.assets)
        app.router.add_get("/miniapp/api/overview", self.overview)
        app.router.add_get("/miniapp/api/tasks", self.tasks)
        app.router.add_post("/miniapp/api/tasks", self.create_task)
        app.router.add_patch("/miniapp/api/tasks/{task_id}", self.update_task)
        app.router.add_get("/miniapp/api/meetings", self.meetings)
        app.router.add_post("/miniapp/api/meetings", self.create_meeting)
        app.router.add_get("/miniapp/api/reminders", self.reminders)
        app.router.add_post("/miniapp/api/reminders", self.create_reminder)
