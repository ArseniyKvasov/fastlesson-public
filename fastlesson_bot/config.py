import os
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
YOOMONEY_PROVIDER_TOKEN = os.getenv("YOOMONEY_PROVIDER_TOKEN")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
DEBUG = os.getenv("DEBUG", "False") == "True"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GENAI_API_KEY = os.getenv("GENAI_API_KEY")

# Redis параметры
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
