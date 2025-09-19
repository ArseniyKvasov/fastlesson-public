# metrics/models.py
from django.conf import settings
from django.utils import timezone
from core.models import User
from django.db import models


class SupportTicket(models.Model):
    class Status(models.TextChoices):
        RECEIVED = "received", "Получено"
        IN_PROGRESS = "in_progress", "В работе"
        DONE = "done", "Выполнено"

    # дублируем массив для совместимости
    STATUS_CHOICES = [
        (Status.RECEIVED, "Получено"),
        (Status.IN_PROGRESS, "В работе"),
        (Status.DONE, "Выполнено"),
    ]

    ticket_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        verbose_name="ID тикета",
        help_text="Уникальный идентификатор тикета (например, T-20250915093000)",
    )
    user_id = models.BigIntegerField(
        verbose_name="Telegram user_id",
        db_index=True,
        unique=True,
        help_text="ID пользователя в Telegram"
    )
    username = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Telegram username",
        help_text="@username пользователя (если есть)",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=Status.RECEIVED,
        verbose_name="Статус",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создано"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Обновлено"
    )

    class Meta:
        verbose_name = "Тикет поддержки"
        verbose_name_plural = "Тикеты поддержки"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user_id"], name="ticket_user_idx"),
            models.Index(fields=["status"], name="ticket_status_idx"),
        ]

    def __str__(self):
        return f"[{self.ticket_id}] {self.get_status_display()}"

    @classmethod
    def create_ticket(cls, user_id: int, username: str = None) -> "SupportTicket":
        """Фабричный метод для создания тикета с автогенерацией ID."""
        return cls.objects.create(
            user_id=user_id,
            username=username,
            ticket_id=f"T-{timezone.now().strftime('%Y%m%d%H%M%S')}",
            status=cls.Status.RECEIVED,
        )


class TicketMessage(models.Model):
    ticket = models.ForeignKey(
        SupportTicket,
        on_delete=models.CASCADE,
        related_name="messages"
    )
    text = models.TextField(blank=True, null=True)
    attachment_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Telegram file_id"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.text[:30] if self.text else f"Вложение {self.attachment_id}"


class UserMetrics(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="metrics",
        verbose_name="Пользователь"
    )

    registered_at = models.DateTimeField(
        verbose_name="Дата регистрации"
    )
    last_active_at = models.DateTimeField(
        verbose_name="Дата последней активности"
    )
    retention_days = models.PositiveIntegerField(
        default=0,
        verbose_name="Retention (дней)"
    )
    last_generated_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Дата последней генерации"
    )
    pdf_download_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Количество скачанных PDF"
    )

    def update_last_active(self):
        """Обновляем дату последней активности и пересчитываем retention"""
        now = timezone.now()
        self.last_active_at = now
        self.retention_days = (now - self.registered_at).days
        self.save(update_fields=["last_active_at", "retention_days"])

    def increment_pdf_download(self):
        """Увеличиваем счетчик скачанных PDF на 1"""
        self.pdf_download_count += 1
        self.save(update_fields=["pdf_download_count"])

    def update_last_generated(self):
        """Обновляем дату последней генерации"""
        self.last_generated_at = timezone.now()
        self.save(update_fields=["last_generated_at"])

    def __str__(self):
        return f"{self.user.telegram_username or self.user.telegram_id} - {self.retention_days}д, PDF: {self.pdf_download_count}"




class Message(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Ожидает'),
        ('sent', 'Отправлено'),
        ('used', 'Использовано'),
        ('error', 'Ошибка'),
    ]

    recipient = models.ForeignKey(
        User,  # или своя модель пользователя
        on_delete=models.CASCADE,
        related_name='messages'
    )
    text = models.TextField()
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending'
    )
    send_attempts = models.PositiveIntegerField(default=0)

    # Кнопка
    button_text = models.CharField(max_length=100, blank=True, null=True)
    button_command = models.CharField(max_length=200, blank=True, null=True)
    button_url = models.URLField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Сообщение'
        verbose_name_plural = 'Сообщения'

    def clean(self):
        from django.core.exceptions import ValidationError
        # Проверка: должна быть либо команда, либо URL
        if self.button_command and self.button_url:
            raise ValidationError('Для кнопки можно указать только команду или URL, но не оба одновременно.')
        if not self.button_command and not self.button_url and self.button_text:
            raise ValidationError('Если указано название кнопки, необходимо задать команду или URL.')

    def __str__(self):
        return f'{self.recipient} — {self.status} — {self.text[:30]}'
