import asyncio

from dotenv import load_dotenv

# Load Bale transport settings. Its DATABASE_PATH should point to the same
# database as Telegram so linked accounts share one canonical data store.
load_dotenv(".env.bale", override=True)

from app.bot import run
from main import configure_logging


if __name__ == "__main__":
    configure_logging()
    asyncio.run(run())
