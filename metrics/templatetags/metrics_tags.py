# metrics/templatetags/metrics_tags.py
from django import template
from django.template import loader
from django.template.exceptions import TemplateDoesNotExist
from django.utils.safestring import mark_safe
from django.utils.html import escape
from django.utils import timezone
from django.db.models import Count, Avg, Sum, Q, Max
from datetime import timedelta

from metrics.models import SupportTicket, TicketMessage, UserMetrics, Message
from django.contrib.admin.views.decorators import staff_member_required

register = template.Library()


@register.simple_tag(takes_context=True)
def render_support_tickets(context, template_name=None, show_completed=False, status_filter=None, limit=100):
    """
    Подготавливает контекст с тикетами и рендерит указанный шаблон.
    Данные в контексте:
        tickets, request, status_change_url, status_choices
    """
    try:
        qs = SupportTicket.objects.all()

        if status_filter:
            qs = qs.filter(status=status_filter)

        tickets = qs.order_by("-created_at")[:limit]

        ctx = {
            "tickets": tickets,
            "request": context.get("request"),
            "status_change_url": context.get("status_change_url"),
            "status_choices": dict(SupportTicket.STATUS_CHOICES),  # <-- сюда
        }

        template_to_use = template_name or "panel_elements/support_list.html"

        try:
            html = loader.render_to_string(template_to_use, ctx, request=context.get("request"))
            return mark_safe(html)
        except TemplateDoesNotExist:
            return mark_safe(
                f"<div class='tag-placeholder'>Шаблон {escape(template_to_use)} не найден. "
                "Данные: tickets в контексте.</div>"
            )
        except Exception as e:
            return mark_safe(f"<div class='tag-error'>Ошибка рендера render_support_tickets: {escape(str(e))}</div>")

    except Exception as e:
        return mark_safe(f"<div class='tag-error'>Ошибка render_support_tickets: {escape(str(e))}</div>")

@register.simple_tag(takes_context=True)
def user_message_block(context, template_name=None, telegram_ids=None, markdown_text="", send_url=None):
    """
    Подготавливает контекст для блока составления письма.
    Контекст:
        targets (list|'all'), markdown_text, request, send_url
    Шаблон по умолчанию: metrics/user_message_block.html
    """
    try:
        # нормализуем telegram_ids
        targets = "all"
        if telegram_ids:
            if isinstance(telegram_ids, str):
                if telegram_ids.strip().lower() == "all":
                    targets = "all"
                else:
                    try:
                        targets = [int(x.strip()) for x in telegram_ids.split(",") if x.strip()]
                    except Exception:
                        targets = [telegram_ids]
            elif isinstance(telegram_ids, (list, tuple)):
                targets = list(telegram_ids)
            else:
                targets = [telegram_ids]

        ctx = {
            "targets": targets,
            "markdown_text": markdown_text,
            "request": context.get("request"),
            "send_url": send_url or context.get("send_url"),
        }

        template_to_use = template_name or "panel_elements/user_message_block.html"

        try:
            html = loader.render_to_string(template_to_use, ctx, request=context.get("request"))
            return mark_safe(html)
        except TemplateDoesNotExist:
            return mark_safe(
                f"<div class='tag-placeholder'>Шаблон {escape(template_to_use)} не найден. "
                "Данные: targets, markdown_text, send_url в контексте.</div>"
            )
        except Exception as e:
            return mark_safe(f"<div class='tag-error'>Ошибка рендера user_message_block: {escape(str(e))}</div>")

    except Exception as e:
        return mark_safe(f"<div class='tag-error'>Ошибка user_message_block: {escape(str(e))}</div>")



@register.simple_tag(takes_context=True)
def render_metrics(context, template_name=None, user_id=None, limit=100):
    """
    Готовит данные метрик.
    Если user_id указан — передаёт конкретный UserMetrics в контексте (в т.ч. админов).
    Иначе даёт агрегированные значения (без админов) + queryset пользователей (включая админов).
    """
    try:
        if user_id:
            # Показываем метрики конкретного пользователя (даже если он админ)
            try:
                um = UserMetrics.objects.get(user_id=user_id)
                ctx = {"user_metrics": um, "request": context.get("request")}
                template_to_use = template_name or "panel_elements/metrics.html"
            except UserMetrics.DoesNotExist:
                return mark_safe(f"<div class='tag-empty'>Нет метрик для user_id={escape(str(user_id))}</div>")
        else:
            now = timezone.now()
            last_7 = now - timedelta(days=7)

            # агрегации без админов
            agg_qs = UserMetrics.objects.exclude(user_id__in=[1, 2, 3])

            agg = agg_qs.aggregate(
                total_users=Count("id"),
                avg_retention_days=Avg("retention_days"),
                total_pdf_downloads=Sum("pdf_download_count"),
            )
            active_last_7 = agg_qs.filter(last_active_at__gte=last_7).count()

            # список всех пользователей (включая админов) для отображения
            user_qs = UserMetrics.objects.order_by("-id")[:limit]

            ctx = {
                "aggregated": {
                    "total_users": agg.get("total_users") or 0,
                    "avg_retention_days": float(agg.get("avg_retention_days") or 0),
                    "total_pdf_downloads": int(agg.get("total_pdf_downloads") or 0),
                    "active_last_7_days": active_last_7,
                },
                "user_metrics_qs": user_qs,
                "request": context.get("request"),
            }
            template_to_use = template_name or "panel_elements/metrics.html"

        try:
            html = loader.render_to_string(template_to_use, ctx, request=context.get("request"))
            return mark_safe(html)
        except TemplateDoesNotExist:
            return mark_safe(
                f"<div class='tag-placeholder'>Шаблон {escape(template_to_use)} не найден. "
                "Данные: aggregated/user_metrics_qs (или user_metrics) в контексте.</div>"
            )
        except Exception as e:
            return mark_safe(f"<div class='tag-error'>Ошибка рендера render_metrics: {escape(str(e))}</div>")

    except Exception as e:
        return mark_safe(f"<div class='tag-error'>Ошибка render_metrics: {escape(str(e))}</div>")

@register.inclusion_tag('panel_elements/message_metrics.html')
def render_message_table():
    """
    Возвращает сгруппированные сообщения для отображения в таблице.
    Админов (user_id=1,2) показываем, не исключаем.
    Группируем только по text.
    """
    grouped = (
        Message.objects
        .values('text')  # группируем только по text
        .annotate(
            total=Count('id'),
            pending=Count('id', filter=Q(status='pending')),
            sent=Count('id', filter=Q(status='sent')),
            used=Count('id', filter=Q(status='used')),
            error=Count('id', filter=Q(status='error')),
            latest_created_at=Max('created_at'),
        )
        .order_by('-latest_created_at')
    )

    return {'messages': grouped}
