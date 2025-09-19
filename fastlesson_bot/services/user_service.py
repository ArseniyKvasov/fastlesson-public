from django.utils import timezone
from asgiref.sync import sync_to_async
from core.models import User, Lesson, UserRole
from metrics.models import UserMetrics


@sync_to_async
def get_user_by_tg(telegram_id: int):
    try:
        return User.objects.get(telegram_id=telegram_id)
    except User.DoesNotExist:
        return None

@sync_to_async
def get_or_create_user(telegram_id: int, role: UserRole, telegram_username: str = None):
    """
    Получает или создаёт пользователя.
    Если пользователь уже существует и изменился telegram_username или роль — обновляем их.
    Также создаёт тариф по умолчанию (Бесплатный, 15 генераций), если его ещё нет.
    """
    user, created = User.objects.get_or_create(
        telegram_id=str(telegram_id),
        defaults={
            "role": role,
            "telegram_username": telegram_username,
            "remaining_generations": 15,
        },
    )

    # Проверяем изменения
    changed = False
    if not created:
        if user.role != role:
            user.role = role
            changed = True
        if not telegram_username or user.telegram_username != telegram_username:
            user.telegram_username = telegram_username
            changed = True
        if changed:
            user.save(update_fields=["role", "telegram_username"])

    return user

@sync_to_async
def set_user_subject(telegram_id: int, subject: str):
    user = User.objects.get(telegram_id=telegram_id)
    user.subject = subject
    user.save()
    return user

@sync_to_async
def set_user_level(telegram_id: int, level: str):
    user = User.objects.get(telegram_id=telegram_id)
    user.level = level
    user.save()
    return user

async def create_lesson_for_user(telegram_id: int, title: str, subject: str, level: str):
    user = await get_user_by_tg(telegram_id)

    # создаём урок
    lesson = Lesson(title=title, subject=subject, level=level, creator=user, is_discovered=False)
    await sync_to_async(lesson.save)()

    return lesson

def track_user_activity(user):
    """
    Обновляет last_active_at и retention_days для пользователя.
    Ошибки игнорируются, но выводятся подробно для отладки.
    """
    try:
        print(f"[track_user_activity] user: {user} (type: {type(user)})")
        print(f"[track_user_activity] user.id: {getattr(user, 'id', None)}")
        print(f"[track_user_activity] user.username: {getattr(user, 'telegram_username', None)}")

        metrics, created = UserMetrics.objects.get_or_create(
            user=user,
            defaults={
                "registered_at": user.created_at,  # берём из User
                "last_active_at": timezone.now(),
                "retention_days": 0,
            }
        )

        if not created:
            metrics.update_last_active()

        print(f"[track_user_activity] metrics: {metrics} (created={created})")
        return metrics

    except Exception as e:
        print(f"[track_user_activity] Ошибка: {e}")
        return None

def can_generate_lesson(tg_id: str, lesson: Lesson) -> bool:
    """
    Проверяет, может ли пользователь генерировать данный урок.
    Возвращает True/False.
    """
    try:
        user = User.objects.get(telegram_id=tg_id)
    except User.DoesNotExist:
        return False

    # обычный пользователь — только свои уроки
    return lesson.creator_id == user.id