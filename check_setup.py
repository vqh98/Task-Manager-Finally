import asyncio

from aiogram import Bot

from app.config import load_settings


async def main() -> None:
    settings = load_settings()
    bot = Bot(settings.bot_token)
    try:
        info = await bot.get_me()
        print(f"Telegram connection: OK (@{info.username})")
        print(f"Owner ID configured: {next(iter(settings.allowed_user_ids))}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
