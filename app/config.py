from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_user_ids: frozenset[int]
    timezone: ZoneInfo
    digest_hour: int
    digest_minute: int
    morning_hour: int
    morning_minute: int
    deadline_warning_days: int
    report_warning_hours: int
    database_path: Path
    openai_api_key: str | None
    openai_base_url: str
    openai_model: str
    openai_whisper_model: str
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    google_oauth_host: str = "127.0.0.1"
    google_oauth_port: int = 8080
    google_token_encryption_key: str = ""
    google_oauth_client_file: str = ""
    google_public_base_url: str = ""
    google_support_email: str = ""
    bot_api_base_url: str = ""
    enable_google_calendar: bool = True
    platform_label: str = "تلگرام"
    platform_key: str = "telegram"
    mini_app_url: str = ""
    bale_bot_token: str = ""


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN در فایل .env تنظیم نشده است.")
    raw_ids = os.getenv("ALLOWED_USER_IDS", "").strip()
    if not raw_ids:
        raise RuntimeError("ALLOWED_USER_IDS در فایل .env تنظیم نشده است.")
    try:
        ids = frozenset(int(value.strip()) for value in raw_ids.split(",") if value.strip())
    except ValueError as exc:
        raise RuntimeError("ALLOWED_USER_IDS باید فقط شناسه‌های عددی و با کاما جداشده باشد.") from exc
    if not ids or any(user_id <= 0 for user_id in ids):
        raise RuntimeError("ALLOWED_USER_IDS شامل شناسه نامعتبر است.")
    timezone = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tehran"))
    def bounded_int(name: str, default: str, minimum: int, maximum: int) -> int:
        try:
            value = int(os.getenv(name, default))
        except ValueError as exc:
            raise RuntimeError(f"{name} باید عدد باشد.") from exc
        if not minimum <= value <= maximum:
            raise RuntimeError(f"{name} باید بین {minimum} و {maximum} باشد.")
        return value

    enable_google = os.getenv("ENABLE_GOOGLE_CALENDAR", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }
    return Settings(
        bot_token=token,
        allowed_user_ids=ids,
        timezone=timezone,
        digest_hour=bounded_int("DAILY_DIGEST_HOUR", "22", 0, 23),
        digest_minute=bounded_int("DAILY_DIGEST_MINUTE", "0", 0, 59),
        morning_hour=bounded_int("MORNING_SCHEDULE_HOUR", "9", 0, 23),
        morning_minute=bounded_int("MORNING_SCHEDULE_MINUTE", "0", 0, 59),
        deadline_warning_days=bounded_int("DEADLINE_WARNING_DAYS", "2", 0, 30),
        report_warning_hours=bounded_int("REPORT_WARNING_HOURS", "48", 1, 720),
        database_path=Path(os.getenv("DATABASE_PATH", "task_manager.db")),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip() or None,
        openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
        openai_whisper_model=os.getenv("OPENAI_WHISPER_MODEL", "whisper-1").strip(),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
        google_redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", "").strip(),
        google_oauth_host=os.getenv("GOOGLE_OAUTH_HOST", "127.0.0.1").strip(),
        google_oauth_port=bounded_int("GOOGLE_OAUTH_PORT", "8080", 1, 65535),
        google_token_encryption_key=os.getenv("GOOGLE_TOKEN_ENCRYPTION_KEY", "").strip(),
        google_oauth_client_file=os.getenv("GOOGLE_OAUTH_CLIENT_FILE", "").strip(),
        google_public_base_url=os.getenv("GOOGLE_PUBLIC_BASE_URL", "").strip().rstrip("/"),
        google_support_email=os.getenv("GOOGLE_SUPPORT_EMAIL", "").strip(),
        bot_api_base_url=os.getenv("BOT_API_BASE_URL", "").strip().rstrip("/"),
        enable_google_calendar=enable_google,
        platform_label=os.getenv("PLATFORM_LABEL", "تلگرام").strip() or "تلگرام",
        platform_key=os.getenv("PLATFORM_KEY", "telegram").strip().lower() or "telegram",
        mini_app_url=os.getenv("MINI_APP_URL", "").strip(),
        bale_bot_token=os.getenv("BALE_BOT_TOKEN", "").strip(),
    )
