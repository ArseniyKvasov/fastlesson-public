import os
import django
import asyncio

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fastlesson.settings")
django.setup()

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder
from fastlesson_bot.handlers.init import all_handlers
from fastlesson_bot.config import BOT_TOKEN, REDIS_HOST, REDIS_PORT, REDIS_DB

async def main():
    bot = Bot(token=BOT_TOKEN)

    REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    storage = RedisStorage.from_url(
        REDIS_URL,
        key_builder=DefaultKeyBuilder(with_destiny=True, prefix="fastlesson_fsm")
    )

    dp = Dispatcher(storage=storage)

    for router in all_handlers:
        dp.include_router(router)

    print("🤖 Bot is running with RedisStorage...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
