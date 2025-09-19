import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fastlesson.settings")

app = Celery("fastlesson")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# расписания
app.conf.beat_schedule = {
    "send-pending-messages-every-20-minutes": {
        "task": "metrics.tasks.send_pending_messages",
        "schedule": crontab(minute="*/20"),
    },
    "notify-unopened-undownloaded-lessons-every-10-minutes": {
        "task": "metrics.tasks.notify_unopened_and_undownloaded_lessons",
        "schedule": crontab(minute="*/10"),
    },
}