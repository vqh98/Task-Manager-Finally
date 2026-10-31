from __future__ import annotations

import base64
import hashlib
import html
import logging
import secrets
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
from aiohttp import web
from app.miniapp import MiniAppAPI
from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    from aiogram import Bot

    from app.config import Settings
    from app.db import Database
    from app.models import Meeting


logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
)
OAUTH_STATE_TTL = timedelta(minutes=30)


class GoogleCalendarError(RuntimeError):
    pass


class GoogleCalendarService:
    """OAuth and durable one-way synchronization with Google Calendar."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.client_id = getattr(settings, "google_client_id", "")
        self.client_secret = getattr(settings, "google_client_secret", "")
        self.redirect_uri = getattr(settings, "google_redirect_uri", "")
        self.oauth_mode = "web"
        client_file = getattr(settings, "google_oauth_client_file", "")
        if (not self.client_id or not self.client_secret) and client_file:
            path = Path(client_file)
            if not path.is_absolute():
                path = Path.cwd() / path
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                kind = "web" if payload.get("web") else "installed"
                block = payload.get(kind) or {}
                self.client_id = str(block.get("client_id") or "")
                self.client_secret = str(block.get("client_secret") or "")
                redirects = block.get("redirect_uris") or []
                if not self.redirect_uri and redirects:
                    self.redirect_uri = str(redirects[0])
                if kind == "installed":
                    self.oauth_mode = "manual"
            except (OSError, ValueError, TypeError):
                logger.exception("Could not load Google OAuth client file: %s", path)
        parsed_redirect = urlparse(self.redirect_uri) if self.redirect_uri else None
        if parsed_redirect and parsed_redirect.hostname in {"localhost", "127.0.0.1", "::1"}:
            self.oauth_mode = "manual"
        raw_key = getattr(settings, "google_token_encryption_key", "")
        if raw_key:
            key = raw_key.encode("ascii")
        else:
            # The bot token is already a server-side secret. An explicit stable
            # key is still recommended so rotating the Telegram token is safe.
            digest = hashlib.sha256(settings.bot_token.encode("utf-8")).digest()
            key = base64.urlsafe_b64encode(digest)
        self.cipher = Fernet(key)
        self._access_tokens: dict[int, tuple[str, datetime]] = {}
        self._runner: web.AppRunner | None = None

    def _public_url(self, path: str) -> str:
        base = getattr(self.settings, "google_public_base_url", "").rstrip("/")
        if not base and self.redirect_uri:
            parsed = urlparse(self.redirect_uri)
            if parsed.scheme == "https" and parsed.netloc:
                base = f"{parsed.scheme}://{parsed.netloc}"
        return f"{base}{path}" if base else path

    def _page(self, title: str, body: str) -> str:
        privacy_url = html.escape(self._public_url("/privacy"), quote=True)
        terms_url = html.escape(self._public_url("/terms"), quote=True)
        home_url = html.escape(self._public_url("/"), quote=True)
        return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)} | TaskAdviser</title>
  <style>
    :root {{ color-scheme:light; --ink:#182230; --muted:#52606d; --brand:#176b5b; --paper:#fff; --bg:#f2f6f5 }}
    * {{ box-sizing:border-box }}
    body {{ margin:0; font-family:Tahoma,Arial,sans-serif; color:var(--ink); background:var(--bg); line-height:1.9 }}
    main {{ width:min(860px,calc(100% - 32px)); margin:48px auto; background:var(--paper); border-radius:18px; padding:clamp(24px,5vw,52px); box-shadow:0 12px 40px #173b3420 }}
    h1,h2 {{ line-height:1.45; color:#124f45 }} h1 {{ margin-top:0 }} a {{ color:var(--brand) }}
    .lead {{ font-size:1.08rem; color:var(--muted) }} .notice {{ padding:14px 18px; border-right:4px solid var(--brand); background:#edf8f5 }}
    nav {{ display:flex; gap:18px; flex-wrap:wrap; margin-top:38px; padding-top:20px; border-top:1px solid #dce7e4 }}
  </style>
</head>
<body><main>{body}<nav><a href="{home_url}">صفحه اصلی</a><a href="{privacy_url}">حریم خصوصی</a><a href="{terms_url}">شرایط استفاده</a></nav></main></body>
</html>"""

    def homepage_html(self) -> str:
        return self._page("دستیار مدیریت جلسه", """
<h1>TaskAdviser؛ دستیار مدیریت جلسه</h1>
<p class="lead">TaskAdviser یک ربات تلگرامی برای ثبت، زمان‌بندی و یادآوری جلسات است. اتصال اختیاری Google Calendar به کاربر اجازه می‌دهد جلسه‌های ثبت‌شده در ربات را در تقویم انتخابی خودش همگام کند.</p>
<h2>اتصال به Google Calendar چگونه کار می‌کند؟</h2>
<ol><li>کاربر در ربات تلگرام TaskAdviser وارد بخش «تنظیمات» می‌شود.</li><li>دکمه «اتصال Google Calendar» را انتخاب می‌کند و اجازه‌های نمایش‌داده‌شده در صفحه Google را می‌پذیرد.</li><li>پس از بازگشت به ربات، یکی از تقویم‌های قابل نوشتن را انتخاب و همگام‌سازی را اجرا می‌کند.</li></ol>
<p>کاربر حساب Google و تقویم مقصد را خودش انتخاب می‌کند و هر زمان بخواهد می‌تواند از همان بخش اتصال را قطع کند. ربات فقط برای ارائه قابلیت همگام‌سازی تقویم از مجوزهای درخواستی استفاده می‌کند.</p>
<p class="notice">اتصال Google Calendar اختیاری است و امکانات اصلی مدیریت جلسه بدون آن نیز قابل استفاده‌اند.</p>
""")

    def privacy_html(self) -> str:
        support = html.escape(
            getattr(self.settings, "google_support_email", "") or "پشتیبانی ربات"
        )
        return self._page("سیاست حریم خصوصی", f"""
<h1>سیاست حریم خصوصی TaskAdviser</h1>
<p class="lead">آخرین به‌روزرسانی: ۱۰ اکتبر ۲۰۲۶</p>
<h2>داده‌هایی که دریافت می‌شوند</h2>
<p>در صورت اتصال Google Calendar، نشانی ایمیل حساب متصل، شناسه تقویم انتخابی، توکن مجوز رمزنگاری‌شده و شناسه رویدادهای همگام‌شده نگهداری می‌شود. عنوان و زمان جلسه‌هایی که کاربر در ربات ثبت می‌کند برای ساخت یا به‌روزرسانی همان رویدادها به Google Calendar ارسال می‌شود.</p>
<h2>نحوه استفاده</h2>
<p>داده‌های Google فقط برای نمایش تقویم‌های قابل انتخاب و ایجاد، به‌روزرسانی، حذف و همگام‌سازی رویدادهای مربوط به جلسه‌های خود کاربر استفاده می‌شوند. این داده‌ها فروخته نمی‌شوند و برای تبلیغات، بازاریابی یا پروفایل‌سازی استفاده نمی‌شوند.</p>
<h2>اشتراک‌گذاری، انتقال و افشای داده‌های Google</h2>
<p>TaskAdviser داده‌های کاربران Google را فقط در موارد و با دریافت‌کنندگان زیر به‌اشتراک می‌گذارد، منتقل یا افشا می‌کند:</p>
<ul>
<li><strong>Google:</strong> عنوان، زمان و شناسه رویداد برای اجرای درخواست کاربر و همگام‌سازی با Google Calendar از طریق APIهای رسمی Google منتقل می‌شود.</li>
<li><strong>Telegram:</strong> چون رابط TaskAdviser یک ربات تلگرام است، اطلاعاتی که ربات در پاسخ به خود کاربر نمایش می‌دهد، مانند وضعیت اتصال، نام تقویم یا جزئیات جلسه، از زیرساخت Telegram عبور می‌کند.</li>
<li><strong>ارائه‌دهنده میزبانی TaskAdviser (MobinHost):</strong> داده‌های لازم برای ارائه سرویس، از جمله توکن رمزنگاری‌شده و شناسه‌های همگام‌سازی، روی سرور خصوصی میزبانی‌شده این ارائه‌دهنده پردازش و نگهداری می‌شوند. ارائه‌دهنده فقط نقش زیرساخت فنی دارد و اجازه استفاده مستقل از داده‌ها را ندارد.</li>
<li><strong>مدیران مجاز TaskAdviser:</strong> فقط در حد لازم برای پشتیبانی درخواستی کاربر، رفع خطا، پیشگیری از سوءاستفاده یا حفاظت از امنیت سرویس ممکن است دسترسی محدود داشته باشند.</li>
<li><strong>مراجع قانونی:</strong> فقط اگر افشا طبق قانون لازم باشد یا برای پاسخ به فرایند قانونی معتبر، حفاظت از کاربران یا امنیت سرویس ضرورت داشته باشد.</li>
</ul>
<p>TaskAdviser داده‌های Google را به کارگزاران داده، شبکه‌های تبلیغاتی، اشخاص ثالث برای بازاریابی یا هر شخص ثالثی برای اهداف مستقل خودش نمی‌فروشد، اجاره نمی‌دهد یا منتقل نمی‌کند. خارج از موارد بالا، داده فقط با رضایت صریح کاربر افشا می‌شود.</p>
<h2>نگهداری و امنیت</h2>
<p>توکن تمدید دسترسی به‌صورت رمزنگاری‌شده روی سرور نگهداری می‌شود. شناسه حساب، تقویم و رویدادها فقط تا زمانی نگهداری می‌شوند که اتصال Google Calendar فعال باشد یا برای ارائه همگام‌سازی لازم باشند. دسترسی عملیاتی محدود است و ارتباط با Google از HTTPS استفاده می‌کند.</p>
<h2>قطع اتصال و حذف داده</h2>
<p>کاربر می‌تواند از تنظیمات ربات اتصال Google Calendar را قطع کند. با تأیید قطع اتصال، توکن رمزنگاری‌شده، نشانی ایمیل حساب متصل، شناسه تقویم، صف همگام‌سازی و پیوندهای رویدادهای Google از پایگاه داده TaskAdviser حذف می‌شوند. کاربر همچنین می‌تواند دسترسی برنامه را از تنظیمات امنیتی حساب Google لغو کند یا برای حذف داده‌های اتصال با {support} تماس بگیرد. رویدادهایی که پیش‌تر در تقویم ساخته شده‌اند با قطع اتصال خودکار حذف نمی‌شوند و کاربر می‌تواند آن‌ها را از تقویمش حذف کند.</p>
<h2>سیاست داده‌های کاربران Google</h2>
<p>استفاده TaskAdviser از داده‌های خام یا مشتق‌شده دریافت‌شده از Google Workspace APIs مطابق Google API Services User Data Policy، از جمله الزامات Limited Use، انجام می‌شود.</p>
<p>داده‌های Google Calendar برای آموزش، بهبود یا ارزیابی مدل‌های هوش مصنوعی یا یادگیری ماشین استفاده نمی‌شوند و به سرویس‌های هوش مصنوعی یا یادگیری ماشین شخص ثالث انتقال داده نمی‌شوند.</p>
""")

    def terms_html(self) -> str:
        support = html.escape(
            getattr(self.settings, "google_support_email", "") or "پشتیبانی ربات"
        )
        return self._page("شرایط استفاده", f"""
<h1>شرایط استفاده از TaskAdviser</h1>
<p class="lead">آخرین به‌روزرسانی: ۲۲ سپتامبر ۲۰۲۶</p>
<p>TaskAdviser برای کمک به ثبت و مدیریت جلسه‌ها ارائه می‌شود. مسئولیت بررسی نهایی عنوان، تاریخ، ساعت، شرکت‌کنندگان و تقویم مقصد با کاربر است.</p>
<h2>Google Calendar</h2>
<p>همگام‌سازی تقویم قابلیتی اختیاری است. کاربر اجازه دسترسی را مستقیماً در Google صادر می‌کند و می‌تواند هر زمان آن را لغو کند. اختلال اینترنت، Google Calendar، Telegram یا نگهداری سرور ممکن است باعث تأخیر موقت در همگام‌سازی شود.</p>
<h2>استفاده مجاز</h2>
<p>کاربر نباید از سرویس برای دسترسی غیرمجاز، ایجاد رویدادهای مزاحم، نقض حقوق دیگران یا فعالیت غیرقانونی استفاده کند.</p>
<h2>پشتیبانی</h2>
<p>برای پرسش درباره سرویس یا داده‌ها می‌توان با {support} تماس گرفت.</p>
""")

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    @property
    def manual_authorization(self) -> bool:
        return self.configured and self.oauth_mode == "manual"

    def encrypt_refresh_token(self, value: str) -> str:
        return self.cipher.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt_refresh_token(self, value: str) -> str:
        try:
            return self.cipher.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise GoogleCalendarError("توکن اتصال گوگل قابل خواندن نیست؛ اتصال را دوباره برقرار کنید.") from exc

    async def authorization_url(self, user_id: int) -> str:
        if not self.configured:
            raise GoogleCalendarError("اتصال Google Calendar روی سرور پیکربندی نشده است.")
        state = secrets.token_urlsafe(32)
        await self.db.create_google_oauth_state(
            state, user_id, datetime.now(timezone.utc) + OAUTH_STATE_TTL
        )
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, state: str, code: str) -> int:
        user_id = await self.db.consume_google_oauth_state(state)
        if user_id is None:
            raise GoogleCalendarError(
                "لینک اتصال منقضی یا قبلاً استفاده شده است. "
                "به ربات برگردید و دوباره دکمهٔ اتصال Google Calendar را بزنید."
            )
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(TOKEN_URL, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
                "code": code,
            })
            if response.status_code != 200:
                raise GoogleCalendarError(f"Google OAuth پاسخ {response.status_code} داد.")
            payload = response.json()
            refresh_token = str(payload.get("refresh_token") or "")
            if not refresh_token:
                existing = await self.db.google_connection(user_id)
                if not existing:
                    raise GoogleCalendarError("گوگل refresh token برنگرداند؛ اتصال را دوباره امتحان کنید.")
                encrypted = existing["encrypted_refresh_token"]
            else:
                encrypted = self.encrypt_refresh_token(refresh_token)
            access_token = str(payload.get("access_token") or "")
            account_email = ""
            if access_token:
                info = await client.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
                if info.status_code == 200:
                    account_email = str(info.json().get("email") or "")
                expiry = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 3600)) - 60)
                self._access_tokens[user_id] = (access_token, expiry)
        await self.db.save_google_connection(user_id, encrypted, account_email)
        return user_id

    async def exchange_redirect_url(self, value: str) -> int:
        raw = value.strip()
        if not raw.startswith(("http://", "https://")):
            raw = "http://" + raw
        query = parse_qs(urlparse(raw).query)
        if query.get("error"):
            raise GoogleCalendarError("اتصال از سمت گوگل لغو یا رد شده است.")
        state = str((query.get("state") or [""])[0])
        code = str((query.get("code") or [""])[0])
        if not state or not code:
            raise GoogleCalendarError("آدرس کامل صفحهٔ نهایی گوگل را ارسال کنید.")
        return await self.exchange_code(state, code)

    async def _access_token(self, user_id: int, force: bool = False) -> str:
        cached = self._access_tokens.get(user_id)
        if not force and cached and cached[1] > datetime.now(timezone.utc):
            return cached[0]
        connection = await self.db.google_connection(user_id)
        if not connection or not connection["enabled"]:
            raise GoogleCalendarError("Google Calendar متصل نیست.")
        refresh_token = self.decrypt_refresh_token(connection["encrypted_refresh_token"])
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(TOKEN_URL, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            })
        if response.status_code != 200:
            raise GoogleCalendarError(f"تمدید دسترسی گوگل ناموفق بود ({response.status_code}).")
        payload = response.json()
        token = str(payload["access_token"])
        expiry = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 3600)) - 60)
        self._access_tokens[user_id] = (token, expiry)
        return token

    async def _request(
        self, user_id: int, method: str, url: str, *, json: dict | None = None,
    ) -> httpx.Response:
        token = await self._access_token(user_id)
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.request(
                method, url, json=json, headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code == 401:
                token = await self._access_token(user_id, force=True)
                response = await client.request(
                    method, url, json=json, headers={"Authorization": f"Bearer {token}"}
                )
        return response

    async def calendars(self, user_id: int) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        page_token = ""
        while True:
            query = urlencode({"minAccessRole": "writer", **({"pageToken": page_token} if page_token else {})})
            response = await self._request(user_id, "GET", f"{CALENDAR_API}/users/me/calendarList?{query}")
            if response.status_code != 200:
                raise GoogleCalendarError(f"دریافت فهرست تقویم‌ها ناموفق بود ({response.status_code}).")
            payload = response.json()
            for item in payload.get("items", []):
                if item.get("deleted"):
                    continue
                results.append({
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("summaryOverride") or item.get("summary") or "تقویم"),
                    "primary": bool(item.get("primary")),
                })
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token:
                break
        results.sort(key=lambda item: (not item["primary"], item["name"].casefold()))
        return results

    def _google_event_time(self, value: dict) -> datetime | None:
        raw = str(value.get("dateTime") or "")
        if raw:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=self.settings.timezone)
                return parsed.astimezone(self.settings.timezone)
            except ValueError:
                return None
        raw_date = str(value.get("date") or "")
        if not raw_date:
            return None
        try:
            return datetime.fromisoformat(raw_date).replace(tzinfo=self.settings.timezone)
        except ValueError:
            return None

    async def import_events(self, user_id: int, calendar_id: str) -> dict[str, int]:
        """Import every non-cancelled event from the selected calendar idempotently."""
        counts = {"created": 0, "updated": 0, "skipped": 0}
        page_token = ""
        while True:
            params = {
                "singleEvents": "true", "showDeleted": "false",
                "maxResults": "2500", "timeZone": str(self.settings.timezone),
            }
            if page_token:
                params["pageToken"] = page_token
            url = (
                f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events?"
                f"{urlencode(params)}"
            )
            response = await self._request(user_id, "GET", url)
            if response.status_code != 200:
                raise GoogleCalendarError(
                    f"دریافت رویدادهای تقویم ناموفق بود ({response.status_code})."
                )
            payload = response.json()
            for item in payload.get("items", []):
                event_id = str(item.get("id") or "")
                if not event_id or item.get("status") == "cancelled":
                    counts["skipped"] += 1
                    continue
                starts_at = self._google_event_time(item.get("start") or {})
                ends_at = self._google_event_time(item.get("end") or {})
                if not starts_at or not ends_at or ends_at <= starts_at:
                    counts["skipped"] += 1
                    continue
                title = str(item.get("summary") or "رویداد بدون عنوان").strip()[:500]
                details = ["واردشده از Google Calendar"]
                if item.get("location"):
                    details.append(f"مکان: {item['location']}")
                if item.get("description"):
                    details.append(str(item["description"]))
                _, created = await self.db.import_google_event(
                    user_id, calendar_id, event_id, title, starts_at, ends_at,
                    "\n".join(details)[:4000],
                )
                counts["created" if created else "updated"] += 1
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token:
                return counts

    async def initial_bidirectional_sync(self, user_id: int) -> dict[str, int]:
        """Reconcile Google first, then upload only unmatched bot meetings."""
        connection = await self.db.google_connection(user_id)
        if not connection:
            raise GoogleCalendarError("اتصال Google Calendar پیدا نشد.")
        imported = await self.import_events(user_id, connection["calendar_id"])
        queued = await self.db.queue_unlinked_google_meetings(user_id)
        processed = await self.process_queue()
        return {
            **imported,
            "queued": queued,
            "processed": processed,
        }

    @staticmethod
    def deterministic_event_id(user_id: int, meeting_id: int) -> str:
        # Google accepts base32hex characters (a-v and 0-9); a SHA-256 hex
        # digest is valid and makes retries idempotent.
        return "ta" + hashlib.sha256(f"{user_id}:{meeting_id}".encode()).hexdigest()[:40]

    def _event_body(self, meeting: Meeting, event_id: str) -> dict:
        return {
            "id": event_id,
            "summary": meeting.title,
            "description": f"ثبت‌شده توسط TaskAdviser\nشناسه جلسه در ربات: {meeting.id}",
            "start": {"dateTime": meeting.starts_at.isoformat(), "timeZone": str(self.settings.timezone)},
            "end": {"dateTime": meeting.ends_at.isoformat(), "timeZone": str(self.settings.timezone)},
            "extendedProperties": {"private": {
                "taskAdviserMeetingId": str(meeting.id),
                "taskAdviserUserId": str(meeting.user_id),
            }},
        }

    async def _upsert_event(
        self, user_id: int, calendar_id: str, meeting: Meeting, event_id: str,
        linked: bool,
    ) -> str:
        base = f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events"
        body = self._event_body(meeting, event_id)
        if linked:
            response = await self._request(
                user_id, "PATCH", f"{base}/{quote(event_id, safe='')}", json=body
            )
            if response.status_code == 404:
                linked = False
        if not linked:
            response = await self._request(user_id, "POST", base, json=body)
            if response.status_code == 409:
                response = await self._request(
                    user_id, "PATCH", f"{base}/{quote(event_id, safe='')}", json=body
                )
        if response.status_code not in {200, 201}:
            raise GoogleCalendarError(f"ثبت رویداد گوگل ناموفق بود ({response.status_code}): {response.text[:300]}")
        return str(response.json().get("id") or event_id)

    async def _delete_event(
        self, user_id: int, calendar_id: str, event_id: str,
    ) -> None:
        url = (
            f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events/"
            f"{quote(event_id, safe='')}"
        )
        response = await self._request(user_id, "DELETE", url)
        if response.status_code not in {204, 404, 410}:
            raise GoogleCalendarError(f"حذف رویداد گوگل ناموفق بود ({response.status_code}).")

    async def process_queue(self) -> int:
        if not self.configured:
            return 0
        processed = 0
        for job in await self.db.pending_google_syncs(50):
            try:
                connection = await self.db.google_connection(job["user_id"])
                if not connection:
                    continue
                if job["action"] == "upsert":
                    meeting = await self.db.meeting(job["meeting_id"], job["user_id"])
                    if not meeting:
                        await self.db.complete_google_sync(
                            job["id"], job["meeting_id"], job["user_id"],
                            connection["calendar_id"], "", "delete",
                        )
                        continue
                    link = await self.db.google_meeting_link(job["meeting_id"], job["user_id"])
                    calendar_id = connection["calendar_id"]
                    event_id = (
                        link["google_event_id"] if link and link["calendar_id"] == calendar_id
                        else self.deterministic_event_id(job["user_id"], job["meeting_id"])
                    )
                    event_id = await self._upsert_event(
                        job["user_id"], calendar_id, meeting, event_id,
                        bool(link and link["calendar_id"] == calendar_id),
                    )
                    await self.db.complete_google_sync(
                        job["id"], job["meeting_id"], job["user_id"],
                        calendar_id, event_id, "upsert",
                    )
                else:
                    calendar_id = job["calendar_id"] or connection["calendar_id"]
                    event_id = job["google_event_id"] or self.deterministic_event_id(
                        job["user_id"], job["meeting_id"]
                    )
                    await self._delete_event(job["user_id"], calendar_id, event_id)
                    await self.db.complete_google_sync(
                        job["id"], job["meeting_id"], job["user_id"],
                        calendar_id, event_id, "delete",
                    )
                processed += 1
            except Exception as exc:
                logger.exception("Google Calendar sync failed for queue item %s", job["id"])
                # A malformed legacy delete target will never become valid by retrying.
                # Drop it after a few attempts so one stale row cannot live in the
                # durable queue forever. Other client/server errors keep the normal
                # exponential retry behavior.
                if (
                    job["action"] == "delete"
                    and isinstance(exc, GoogleCalendarError)
                    and "(400)" in str(exc)
                    and job["attempts"] >= 5
                ):
                    logger.warning(
                        "Dropping permanently invalid Google delete queue item %s after %s attempts",
                        job["id"], job["attempts"] + 1,
                    )
                    await self.db.complete_google_sync(
                        job["id"], job["meeting_id"], job["user_id"],
                        job["calendar_id"] or connection["calendar_id"],
                        job["google_event_id"] or "", "delete",
                    )
                    continue
                await self.db.retry_google_sync(job["id"], str(exc), job["attempts"])
        return processed

    async def start_callback_server(self, bot: Bot) -> None:
        callback_enabled = self.configured and not self.manual_authorization
        path = "/google/oauth/callback"
        if callback_enabled:
            path = urlparse(self.redirect_uri).path or path
        if path in {"/", "/privacy", "/terms"}:
            logger.warning("Unsafe Google OAuth callback path %s; using the default path", path)
            path = "/google/oauth/callback"

        async def homepage(request: web.Request) -> web.Response:
            return web.Response(
                text=self.homepage_html(), content_type="text/html", charset="utf-8"
            )

        async def privacy(request: web.Request) -> web.Response:
            return web.Response(
                text=self.privacy_html(), content_type="text/html", charset="utf-8"
            )

        async def terms(request: web.Request) -> web.Response:
            return web.Response(
                text=self.terms_html(), content_type="text/html", charset="utf-8"
            )

        async def google_site_verification(request: web.Request) -> web.Response:
            return web.Response(
                text="google-site-verification: google46498e90de7bc9de.html",
                content_type="text/html", charset="utf-8",
            )

        async def callback(request: web.Request) -> web.Response:
            if not callback_enabled:
                return web.Response(
                    text=self._page(
                        "اتصال موقتاً فعال نیست",
                        "<h1>اتصال Google Calendar موقتاً فعال نیست</h1>"
                        "<p>پیکربندی OAuth وب هنوز کامل نشده است. "
                        "لطفاً بعداً دوباره از داخل ربات تلاش کنید.</p>",
                    ),
                    content_type="text/html", charset="utf-8", status=503,
                )
            if request.query.get("error"):
                return web.Response(
                    text="اتصال Google Calendar لغو شد. می‌توانید این صفحه را ببندید.",
                    content_type="text/html", charset="utf-8", status=400,
                )
            state, code = request.query.get("state", ""), request.query.get("code", "")
            try:
                if not state or not code:
                    raise GoogleCalendarError("پاسخ گوگل ناقص است.")
                user_id = await self.exchange_code(state, code)
                synced = await self.initial_bidirectional_sync(user_id)
                await bot.send_message(
                    user_id,
                    "✅ Google Calendar متصل و همگام‌سازی دوسویهٔ اولیه انجام شد.\n"
                    f"📥 {synced['created']} رویداد گوگل وارد ربات شد.\n"
                    f"📤 {synced['queued']} جلسهٔ ربات برای ثبت یا تطبیق با تقویم بررسی شد.\n"
                    "از بخش تنظیمات می‌توانید تقویم مقصد را تغییر دهید.",
                )
                body, status = "اتصال با موفقیت انجام شد. اکنون می‌توانید به تلگرام برگردید.", 200
            except GoogleCalendarError as exc:
                logger.warning("Google OAuth callback rejected: %s", exc)
                body, status = f"اتصال انجام نشد: {html.escape(str(exc))}", 400
            except Exception as exc:
                logger.exception("Google OAuth callback failed")
                body, status = f"اتصال انجام نشد: {html.escape(str(exc))}", 400
            return web.Response(text=body, content_type="text/html", charset="utf-8", status=status)

        app = web.Application()
        app.router.add_get("/", homepage)
        app.router.add_get("/privacy", privacy)
        app.router.add_get("/terms", terms)
        app.router.add_get(
            "/google46498e90de7bc9de.html", google_site_verification
        )
        app.router.add_get(path, callback)
        if getattr(self.settings, "mini_app_url", ""):
            MiniAppAPI(self.settings, self.db).register(app)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(
            self._runner,
            getattr(self.settings, "google_oauth_host", "127.0.0.1"),
            getattr(self.settings, "google_oauth_port", 8080),
        )
        await site.start()
        logger.info(
            "Google public/OAuth server listening on %s:%s (callback=%s, enabled=%s)",
            self.settings.google_oauth_host, self.settings.google_oauth_port, path,
            callback_enabled,
        )

    async def stop_callback_server(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
