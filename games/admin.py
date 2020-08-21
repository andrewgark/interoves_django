from django.contrib import admin
from django.forms import Textarea
from django.db import models
from django.shortcuts import get_object_or_404
from games.google.actions import create_google_doc
from games.models import *
from games.recheck import recheck, recheck_full, recheck_queue_from_this, recheck_queue_from_next


admin.site.register([CheckerType, HTMLPage, Like])


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ['name', 'get_n_users_on', 'get_n_users_requested', 'is_tester', 'is_hidden']


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'team_on', 'team_requested', 'vk_url']


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


def create_new_google_doc(modeladmin, request, queryset):
    for game_id in queryset.values_list('id'):
        create_google_doc(get_object_or_404(Game, id=game_id[0]))


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    inlines = [
        TaskGroupInline
    ]
    list_display = ['__str__', 'name', 'theme', 'author', 'start_time', 'end_time', 'is_ready', 'is_playable', 'is_testing']
    actions = [create_new_google_doc]


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }


def recheck_attempt(modeladmin, request, queryset):
    for attempt_id in queryset.values_list('id'):
        recheck(request, attempt_id[0])

def recheck_full_attempt(modeladmin, request, queryset):
    for attempt_id in queryset.values_list('id'):
        recheck_full(request, attempt_id[0])

def recheck_queue_from_this_attempt(modeladmin, request, queryset):
    for attempt_id in queryset.values_list('id'):
        recheck_queue_from_this(request, attempt_id[0])

def recheck_queue_from_next_attempt(modeladmin, request, queryset):
    for attempt_id in queryset.values_list('id'):
        recheck_queue_from_next(request, attempt_id[0])

def set_ok(modeladmin, request, queryset):
    for attempt in queryset.all():
        attempt.points = attempt.get_max_points()
        attempt.status = 'Ok'
        attempt.save()

def confirm_prestatus(modeladmin, request, queryset):
    for attempt in queryset.all():
        attempt.status = attempt.possible_status
        attempt.save()


recheck_attempt.short_description = "Recheck attempt"
recheck_full_attempt.short_description = "Recheck all attempts of this task"
recheck_queue_from_this_attempt.short_description = "Recheck all attempts starting with this"
recheck_queue_from_next_attempt.short_description = "Recheck all attempts starting with next"
set_ok.short_description = "Set OK (and max points)"
confirm_prestatus.short_description = "Confirm Prestatus"


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }
    list_display = ['__str__', 'team', 'task', 'get_pretty_text', 'get_answer', 'status', 'points', 'get_max_points', 'skip']
    actions = [set_ok, confirm_prestatus, recheck_attempt, recheck_full_attempt, recheck_queue_from_this_attempt, recheck_queue_from_next_attempt]


@admin.register(ProxyAttempt)
class PendingAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }

    def get_queryset(self, request):
        qs = super(PendingAdmin, self).get_queryset(request)
        return qs.filter(status='Pending')

    list_display = ['__str__', 'team', 'task', 'get_pretty_text', 'get_answer', 'status', 'points', 'get_max_points']
    actions = [set_ok, confirm_prestatus, recheck_attempt, recheck_full_attempt, recheck_queue_from_this_attempt, recheck_queue_from_next_attempt]
