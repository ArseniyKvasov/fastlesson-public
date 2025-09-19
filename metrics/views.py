import os

import requests
from django.contrib import messages
from django.http import StreamingHttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from core.models import User
from metrics.models import SupportTicket, Message, TicketMessage
from django.contrib.admin.views.decorators import staff_member_required

from fastlesson_bot.config import BOT_TOKEN as bot_token

@staff_member_required
def metrics(request):
    return render(request, "panel.html")

@staff_member_required
def send_mass_message(request):
    if request.method != "POST":
        messages.error(request, "Метод не поддерживается")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    targets_type = request.POST.get("targets_type")
    targets = request.POST.get("targets")
    text = request.POST.get("markdown_text")
    button_text = request.POST.get("extra_btn_text")
    button_url = request.POST.get("extra_btn_url")
    button_command = request.POST.get("extra_btn_command")

    if not text:
        messages.error(request, "Текст сообщения обязателен")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # Проверка кнопок
    if button_url and button_command:
        messages.error(request, "Можно указать только URL или команду, но не оба")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # Определяем получателей
    if targets_type == "all" or targets == "all":
        recipients = User.objects.all()
    else:
        ids = [i.strip() for i in targets.split(",") if i.strip().isdigit()]
        recipients = User.objects.filter(id__in=ids)

    if not recipients:
        messages.error(request, "Получатели не найдены")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # Создаём записи в БД
    for user in recipients:
        Message.objects.create(
            recipient=user,
            text=text,
            status="pending",
            send_attempts=0,
            button_text=button_text or None,
            button_command=button_command or None,
            button_url=button_url or None,
        )

    messages.success(request, f"Сообщения ({len(recipients)}) добавлены в очередь")
    return redirect(request.META.get("HTTP_REFERER", "/"))

@require_POST
@staff_member_required
def support_change_status(request, pk):
    ticket = get_object_or_404(SupportTicket, pk=pk)
    new_status = request.POST.get("status")

    if new_status in ["received", "in_progress", "done"]:
        ticket.status = new_status
        ticket.save(update_fields=["status"])
        messages.success(request, f"✅ Статус тикета {ticket.ticket_id} изменён на «{ticket.get_status_display()}».")
    else:
        messages.error(request, "❌ Некорректный статус.")

    return redirect(request.META.get("HTTP_REFERER", "support_list"))

@staff_member_required
def download_attachment(request, message_id: int):
    """
    Скачивает файл из Telegram по attachment_id, привязанному к TicketMessage.
    Использует Telegram getFile -> затем стримит файл клиенту.
    """
    try:
        msg = TicketMessage.objects.get(pk=message_id)
    except TicketMessage.DoesNotExist:
        raise Http404("Message not found")

    if not msg.attachment_id:
        raise Http404("No attachment")

    if not bot_token:
        raise Http404("Bot token not configured")

    # 1) getFile
    getfile_url = f"https://api.telegram.org/bot{bot_token}/getFile"
    resp = requests.get(getfile_url, params={"file_id": msg.attachment_id}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise Http404("Failed to get file info from Telegram")

    file_path = data["result"]["file_path"]  # e.g. photos/file_123.jpg
    file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"

    # 2) download & stream
    r = requests.get(file_url, stream=True, timeout=30)
    r.raise_for_status()

    filename = os.path.basename(file_path) or f"attachment_{msg.id}"
    content_type = r.headers.get("Content-Type", "application/octet-stream")

    response = StreamingHttpResponse(r.iter_content(chunk_size=8192), content_type=content_type)
    content_length = r.headers.get("Content-Length")
    if content_length:
        response["Content-Length"] = content_length
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response