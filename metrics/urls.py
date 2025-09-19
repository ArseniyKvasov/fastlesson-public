from django.urls import path
from metrics import views

urlpatterns = [
    path("m/", views.metrics, name="metrics"),
    path("send-mass-message/", views.send_mass_message, name="send_mass_message"),
    path("support/ticket/<int:pk>/status/", views.support_change_status, name="support_change_status"),
    path("support/attachment/<int:message_id>/download/", views.download_attachment, name="support_attachment_download"),
]