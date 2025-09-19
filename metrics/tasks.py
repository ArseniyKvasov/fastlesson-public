import logging
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from celery import shared_task

from core.models import Lesson
from metrics.models import Message
from metrics.utils import send_message_to_user


logger = logging.getLogger(__name__)

@shared_task
def send_pending_messages():
    """
    Задача Celery: отправляет отложенные сообщения.
    Добавлено детальное логирование и печать для отладки.
    """
    logger.info("send_pending_messages started")

    now = timezone.now()
    fifteen_minutes_ago = now - timedelta(minutes=15)
    logger.debug("Current time: %s, threshold (15 min ago): %s", now, fifteen_minutes_ago)

    # выбираем сообщения pending, с попытками < 3
    pending = Message.objects.filter(
        status="pending",
        send_attempts__lt=5
    ).select_related("recipient__metrics")

    total = pending.count()
    sent_count = 0
    skipped_count = 0
    error_count = 0

    logger.info("Found %d pending messages", total)

    if not pending.exists():
        logger.info("No pending messages found")
        return

    for message in pending:
        try:
            user = message.recipient
            logger.info("Processing message %s for recipient %s", message.id, getattr(user, "id", "unknown"))

            # безопасная проверка last_active_at
            last_active = None
            try:
                last_active = getattr(user.metrics, "last_active_at", None)
            except Exception as ex:
                logger.debug("Recipient %s has no metrics relation or error reading it: %s", getattr(user, "id", "unknown"), ex)
                last_active = None

            logger.debug("Message %s: send_attempts=%s, last_active=%s", message.id, message.send_attempts, last_active)

            # если last_active отсутствует или пользователь был активен недавно — пропускаем
            if last_active is None:
                logger.debug("Skipping message %s: recipient %s has no metrics", message.id, getattr(user, "id", "unknown"))
                # увеличиваем attempts, чтобы избежать вечного цикла
                message.send_attempts += 1
                try:
                    message.save(update_fields=["send_attempts"])
                except Exception as e_save:
                    logger.exception("Failed to save send_attempts for message %s: %s", message.id, e_save)
                skipped_count += 1
                continue

            if last_active > fifteen_minutes_ago:
                logger.debug("Skipping message %s: recipient %s active recently (%s)", message.id, getattr(user, "id", "unknown"), last_active)
                skipped_count += 1
                continue

            # ленивый импорт утилиты, чтобы импорт metrics.tasks не создавал зависимостей на bot
            from .utils import send_message_to_user

            logger.info("Sending message %s to user %s (chat_id=%s)", message.id, getattr(user, "id", "unknown"), getattr(user, "telegram_id", None))

            success = send_message_to_user(
                user=user,
                text=message.text,
                button_text=message.button_text,
                button_command=message.button_command,
                button_url=message.button_url
            )

            # для защиты: явно логируем результат
            logger.info("Result of send_message_to_user for message %s: %s", message.id, success)

            message.send_attempts += 1
            if success:
                message.status = "sent"
                sent_count += 1
                logger.info("Message %s marked as sent", message.id)
            elif message.send_attempts >= 3:
                message.status = "error"
                error_count += 1
                logger.warning("Message %s marked as error after %s attempts", message.id, message.send_attempts)

            # Сохраняем только нужные поля
            try:
                message.save(update_fields=["status", "send_attempts"])
            except Exception as e_save:
                logger.exception("Failed to save message %s: %s", message.id, e_save)

        except Exception as e:
            logger.exception("Error handling message %s: %s", getattr(message, "id", "unknown"), e)
            try:
                message.send_attempts += 1
                message.save(update_fields=["send_attempts"])
            except Exception:
                logger.exception("Failed to increment send_attempts for message %s", getattr(message, "id", "unknown"))
            error_count += 1

    logger.info(
        "send_pending_messages finished: total=%d, sent=%d, skipped=%d, errors=%d",
        total, sent_count, skipped_count, error_count
    )




@shared_task
def notify_unopened_and_undownloaded_lessons():
    """
    Для каждого автора: берём последний созданный урок, у которого есть блоки и который нуждается в уведомлении.
    Отправляем одно сообщение на пользователя с одной кнопкой:
      - "Посмотреть" (lesson_view:{lesson_id}:1) если урок не открыт и не уведомлён,
      - иначе "Скачать" (lesson_download:{lesson_id}) если не скачан и не уведомлён.
    Уведомляем только если last_active_at существует и старше INACTIVITY_MINUTES.
    Если last_active_at отсутствует или отправка неудачна — увеличиваем lesson.notify_attempts.
    """
    MAX_ATTEMPTS = 3
    INACTIVITY_MINUTES = 5

    logger.info("notify_unopened_and_undownloaded_lessons started")
    now = timezone.now()
    threshold = now - timedelta(minutes=INACTIVITY_MINUTES)

    # ленивые импорты моделей/утилит (чтобы не создавать циклические зависимости при импорте модуля)
    from django.contrib.auth import get_user_model
    from core.models import Lesson  # поправь, если модель в другом приложении
    from .utils import send_message_to_user

    # 1) выбираем уроки, которые имеют блоки и требуют уведомления; сортируем по created_at DESC
    lessons_with_blocks = (
        Lesson.objects
        .annotate(blocks_count=Count("blocks"))
        .filter(
            Q(blocks_count__gt=0) & (
                Q(discover_notified=False, is_discovered=False) |
                Q(download_notified=False, is_downloaded=False)
            )
        )
        .select_related("creator", "creator__metrics")
        .order_by("-created_at")
    )

    total_candidates = lessons_with_blocks.count()
    logger.info("Found %d candidate lessons (with blocks and needing notification)", total_candidates)

    if total_candidates == 0:
        logger.info("No lessons to consider")
        return

    # 2) Получаем уникальных authors (creator_id) из отсортированного queryset.
    # Для каждого автора потом возьмем .first() из lessons_with_blocks -> это будет последний урок с блоками.
    creator_ids = list(lessons_with_blocks.values_list("creator_id", flat=True).distinct())

    processed_users = 0

    for creator_id in creator_ids:
        lesson = None
        try:
            # последний урок (по created_at) у этого автора, у которого есть блоки и который требует уведомления
            lesson = lessons_with_blocks.filter(creator_id=creator_id).first()
            if not lesson:
                continue

            user = lesson.creator
            user_id_display = getattr(user, "id", "unknown")
            lesson_id = getattr(lesson, "id", None)
            logger.debug("User %s: considering lesson %s", user_id_display, lesson_id)

            # пропускаем если превысили попытки
            if lesson.notify_attempts >= MAX_ATTEMPTS:
                logger.info(
                    "Skipping lesson %s for user %s: notify_attempts (%s) >= %s",
                    lesson_id, user_id_display, lesson.notify_attempts, MAX_ATTEMPTS
                )
                continue

            # безопасно читаем last_active_at
            try:
                last_active = getattr(user.metrics, "last_active_at", None)
            except Exception as ex:
                logger.debug("User %s metrics missing or read error: %s", user_id_display, ex)
                last_active = None

            logger.debug(
                "Lesson %s: notify_attempts=%s, last_active=%s",
                lesson_id, lesson.notify_attempts, last_active
            )

            # если last_active отсутствует — увеличиваем attempts и пропускаем
            if last_active is None:
                lesson.notify_attempts = lesson.notify_attempts + 1
                try:
                    lesson.save(update_fields=["notify_attempts"])
                    logger.info(
                        "Incremented notify_attempts for lesson %s to %s because last_active is missing",
                        lesson_id, lesson.notify_attempts
                    )
                except Exception as e_save:
                    logger.exception("Failed to save notify_attempts for lesson %s: %s", lesson_id, e_save)
                continue

            # если пользователь был активен недавно — пропускаем
            if last_active > threshold:
                logger.debug(
                    "Skipping lesson %s: user %s active recently (%s)",
                    lesson_id, user_id_display, last_active
                )
                continue

            # решаем, какую кнопку отправлять: приоритет "Посмотреть"
            to_send = None  # "view" или "download"
            if not lesson.is_discovered and not lesson.discover_notified:
                to_send = "view"
            elif not lesson.is_downloaded and not lesson.download_notified:
                to_send = "download"
            else:
                logger.debug("Nothing to notify for lesson %s (flags already set)", lesson_id)
                continue

            # формируем текст и кнопку
            if to_send == "view":
                text = f"Ваш урок на тему «{lesson.title}» готов. Скорее посмотрите его!"
                button_text = "Посмотреть"
                button_command = f"lesson_view:{lesson.id}:1"
            else:
                text = f"Ваш урок на тему «{lesson.title}» готов. Можете скачать его прямо сейчас!"
                button_text = "Скачать"
                button_command = f"lesson_download:{lesson.id}"

            logger.info("Sending '%s' notification for lesson %s to user %s", to_send, lesson_id, user_id_display)

            try:
                ok = send_message_to_user(
                    user=user,
                    text=text,
                    button_text=button_text,
                    button_command=button_command
                )
                logger.info("send_message_to_user returned %s for lesson %s (user %s)", ok, lesson_id, user_id_display)
            except Exception as e_send:
                logger.exception("Exception when sending notification for lesson %s: %s", lesson_id, e_send)
                ok = False

            # обработка результата
            if ok:
                if to_send == "view":
                    lesson.discover_notified = True
                    logger.info("Marked discover_notified=True for lesson %s", lesson_id)
                else:
                    lesson.download_notified = True
                    logger.info("Marked download_notified=True for lesson %s", lesson_id)
                # по желанию: сбрасываем attempts при успехе; пока оставляем как есть
                # lesson.notify_attempts = 0
            else:
                lesson.notify_attempts = lesson.notify_attempts + 1
                logger.warning(
                    "Failed to send notification for lesson %s; notify_attempts -> %s",
                    lesson_id, lesson.notify_attempts
                )

            # сохраняем изменения: флаги и attempts
            fields = []
            if lesson.discover_notified:
                fields.append("discover_notified")
            if lesson.download_notified:
                fields.append("download_notified")
            # attempts всегда возможны к изменению — сохраняем поле
            fields.append("notify_attempts")

            # убираем дубликаты (на всякий случай) и сохраняем
            fields = list(dict.fromkeys(fields))
            try:
                lesson.save(update_fields=fields)
                logger.info("Saved lesson %s fields: %s", lesson_id, fields)
            except Exception as e_save:
                logger.exception("Failed to save lesson %s update_fields=%s: %s", lesson_id, fields, e_save)

            processed_users += 1

        except Exception as e:
            logger.exception("Top-level error processing creator %s: %s", creator_id, e)
            # при ошибке пытаемся увеличить attempts у текущего lesson (если он есть)
            try:
                if lesson is not None:
                    lesson.notify_attempts = lesson.notify_attempts + 1
                    lesson.save(update_fields=["notify_attempts"])
            except Exception:
                logger.exception(
                    "Failed to increment notify_attempts in exception handler for lesson %s",
                    getattr(lesson, "id", "unknown")
                )

    logger.info(
        "notify_unopened_and_undownloaded_lessons finished — processed users: %d",
        processed_users
    )