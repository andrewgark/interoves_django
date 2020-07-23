from django.contrib import admin
from django.forms import Textarea
from django.db import models
from games.models import *


admin.site.register([CheckerType, HTMLPage])


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ['name', 'get_n_users_on', 'get_n_users_requested', 'is_tester', 'is_hidden']


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'team_on', 'team_requested', 'vk_url']


@admin.register(AttemptsInfo)
class AttemptsInfoAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'team', 'task', 'mode', 'get_n_attempts', 'best_attempt']


class TaskInline(admin.TabularInline):
    model = Task
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }


@admin.register(TaskGroup)
class TaskGroupAdmin(admin.ModelAdmin):
    inlines = [
        TaskInline,
    ]
    list_display = ['__str__', 'game', 'number', 'name']


class TaskGroupInline(admin.TabularInline):
    model = TaskGroup


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    inlines = [
        TaskGroupInline
    ]
    list_display = ['__str__', 'name', 'theme', 'author', 'start_time', 'end_time', 'is_ready', 'is_playable', 'is_testing']


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }
    list_display = ['__str__', 'team', 'task', 'text', 'get_answer', 'status', 'points', 'get_max_points']


@admin.register(ProxyAttempt)
class PendingAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }

    def get_queryset(self, request):
        qs = super(PendingAdmin, self).get_queryset(request)
        return qs.filter(status='Pending')

    list_display = ['__str__', 'team', 'task', 'text', 'get_answer', 'status', 'points', 'get_max_points']
