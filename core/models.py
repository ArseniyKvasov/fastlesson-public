import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone

class UserRole(models.TextChoices):
    STUDENT = "student", "Ученик"
    TUTOR = "tutor", "Репетитор"
    SCHOOL_TEACHER = "school_teacher", "Школьный учитель"

# --- Subjects ---
class SubjectChoices(models.TextChoices):
    MATH = "math", "Математика"
    FOREIGN_LANG = "foreign_lang", "Иностранный язык"
    RUSSIAN = "russian", "Русский"
    IT = "it", "Информатика"
    SOCIAL = "social", "Обществознание"
    HISTORY = "history", "История"
    BIOLOGY = "biology", "Биология"
    PHYSICS = "physics", "Физика"
    OTHER = "other", "Другое"

# --- Levels ---
class LevelChoices(models.TextChoices):
    GRADE_1_4 = "grade_1_4", "1-4 классы"
    GRADE_5_7 = "grade_5_7", "5-7 классы"
    GRADE_8_11 = "grade_8_11", "8-11 классы"
    UNIVERSITY = "university", "Университет"
    ADULTS = "adults", "Взрослые"


class User(models.Model):
    telegram_id = models.CharField(max_length=50, unique=True)
    telegram_username = models.CharField(max_length=255, blank=True, null=True)
    is_staff = models.BooleanField(default=False)
    role = models.CharField(max_length=20, choices=UserRole.choices)
    subject = models.CharField(max_length=50, choices=SubjectChoices.choices, blank=True, null=True)
    level = models.CharField(max_length=20, choices=LevelChoices.choices, blank=True, null=True)
    remaining_generations = models.PositiveIntegerField(default=10, verbose_name="Оставшиеся генерации")
    created_at = models.DateTimeField(auto_now_add=True)

    def decrement_generation(self, count: int = 1) -> bool:
        """
        Минус count генераций, если хватает.
        Возвращает True, если списание прошло, False если не хватило.
        """
        if self.remaining_generations >= count:
            self.remaining_generations -= count
            self.save(update_fields=["remaining_generations"])
            return True
        return False

    def __str__(self):
        return f"{self.telegram_username or self.telegram_id} ({self.remaining_generations} генераций)"

class Payment(models.Model):
    user = models.ForeignKey("core.User", on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="RUB")
    created_at = models.DateTimeField(auto_now_add=True)

    # ID платежа у провайдера (если появится)
    payment_id = models.CharField(max_length=255, blank=True, null=True)
    payment_method_id = models.CharField(max_length=255, blank=True, null=True)

    # хранить payload Telegram-инвойса — удобно для поиска и проверки
    payload = models.CharField(max_length=255, blank=True, null=True)
    telegram_invoice_payload = models.CharField(max_length=255, blank=True, null=True)

    # provider_charge_id / provider_payment_charge_id (YooKassa / YooMoney)
    provider_charge_id = models.CharField(max_length=255, blank=True, null=True)

    # telegram charge id (telegram_payment_charge_id)
    telegram_charge_id = models.CharField(max_length=255, blank=True, null=True)

    # raw provider data (receipt / vendor response) и order_info от Telegram
    provider_data = models.JSONField(blank=True, null=True)
    order_info = models.JSONField(blank=True, null=True)

    # необязательно: info о сообщении "Обрабатываем платёж..." чтобы потом редактировать
    processing_chat_id = models.BigIntegerField(blank=True, null=True)
    processing_message_id = models.BigIntegerField(blank=True, null=True)

    status = models.CharField(max_length=20, default="pending")  # pending / succeeded / failed / cancelled

    def __str__(self):
        return f"{self.user} — {self.amount} {self.currency} — {self.status}"


class GenerationStatus(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "В ожидании"
        IN_PROGRESS = "in_progress", "В процессе"
        DONE = "done", "Готово"
        FAILED = "failed", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lesson = models.OneToOneField(
        "Lesson", on_delete=models.CASCADE, related_name="generation_status"
    )
    total = models.PositiveIntegerField(default=0)
    completed = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def progress_percent(self) -> int:
        """Вернуть прогресс в процентах (0–100)."""
        if self.total == 0:
            return 0
        return min(100, int((self.completed / self.total) * 100))

    def __str__(self):
        return f"Генерация для урока {self.lesson.title}: {self.progress_percent()}%"

class ImproveStatus(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "В очереди"
        IN_PROGRESS = "IN_PROGRESS", "В процессе"
        DONE = "DONE", "Готово"
        FAILED = "FAILED", "Ошибка"

    block = models.ForeignKey("LessonBlock", on_delete=models.CASCADE, related_name="improve_statuses")
    mode = models.CharField(max_length=50)  # simplify / complexify / more_tasks / remove_tasks
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    task_id = models.CharField(max_length=255, blank=True, null=True)
    result_content = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Lesson(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    subject = models.CharField(max_length=50, choices=SubjectChoices.choices, blank=True, null=True)
    level = models.CharField(max_length=20, choices=LevelChoices.choices, blank=True, null=True)

    is_discovered = models.BooleanField(default=False)
    is_downloaded = models.BooleanField(default=False)
    discover_notified = models.BooleanField(default=False)
    download_notified = models.BooleanField(default=False)
    notify_attempts = models.PositiveIntegerField(default=0)

    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lessons")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class LessonBlock(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lesson = models.ForeignKey("Lesson", on_delete=models.CASCADE, related_name="blocks")
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=255)  # название блока
    content = models.TextField()              # основной текст
    has_task = models.BooleanField(default=False)  # есть ли в блоке задание
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.order}. {self.title} ({'с заданием' if self.has_task else 'без задания'})"


class Answer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lesson = models.ForeignKey('Lesson', on_delete=models.CASCADE, related_name='answers')
    student = models.ForeignKey('User', on_delete=models.CASCADE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Answer by {self.student} for {self.lesson.title}"