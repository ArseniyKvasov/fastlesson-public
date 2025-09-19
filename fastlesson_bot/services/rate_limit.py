import redis
from django.conf import settings
from django.core.exceptions import PermissionDenied

# лучше вынести в отдельный модуль (например, utils/rate_limit.py)
r = redis.Redis.from_url(getattr(settings, "REDIS_URL", "redis://localhost:6379/0"))


def check_rate_limit(user_id: int, key: str, limit: int = 5, window: int = 60):
    """
    Проверяет лимит запросов.
    user_id - ID пользователя (Telegram)
    key - название операции (например 'lesson_generate')
    limit - сколько раз можно вызвать
    window - окно времени в секундах
    """
    redis_key = f"ratelimit:{key}:{user_id}"
    current = r.incr(redis_key)

    if current == 1:
        # первый запрос → TTL
        r.expire(redis_key, window)

    if current > limit:
        raise PermissionDenied(f"Не более {limit} запросов за {window} секунд")
