import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from app.bot import run


def configure_logging() -> None:
    load_dotenv()
    log_path = Path(os.getenv("LOG_PATH", "logs/taskmanager.log"))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(module)s:%(lineno)d | %(message)s"
    )
    max_bytes = max(1024 * 1024, int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024))))
    backup_count = max(1, int(os.getenv("LOG_BACKUP_COUNT", "3")))
    file_handler = RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    log_to_console = os.getenv("LOG_TO_CONSOLE", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }
    handlers = [file_handler]
    if log_to_console:
        handlers.append(console_handler)
    logging.basicConfig(level=level, handlers=handlers, force=True)
    # APScheduler emits two INFO records for every one-minute job. Keep its
    # warnings/errors while avoiding thousands of duplicate lines per day.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.captureWarnings(True)
    logging.getLogger(__name__).info("Logging initialized: path=%s level=%s", log_path, level_name)


if __name__ == "__main__":
    configure_logging()
    try:
        asyncio.run(run())
    except Exception:
        logging.getLogger(__name__).exception("Fatal application error")
        raise
