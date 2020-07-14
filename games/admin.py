from django.contrib import admin
from games.models import *


admin.site.register(Team)
admin.site.register(Profile)
admin.site.register(CheckerType)
admin.site.register(Checker)
admin.site.register(HTMLPage)

class TaskGroupInline(admin.TabularInline):
    model = TaskGroup

@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    inlines = [
        TaskGroupInline,
    ]

class TaskInline(admin.TabularInline):
    model = Task

@admin.register(TaskGroup)
class TaskGroupAdmin(admin.ModelAdmin):
    inlines = [
        TaskInline,
    ]

admin.site.register(Task)
