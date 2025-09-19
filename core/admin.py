from django.contrib import admin
from .models import User, Lesson, LessonBlock, Answer

admin.site.register(User)
admin.site.register(Lesson)
admin.site.register(LessonBlock)
admin.site.register(Answer)
