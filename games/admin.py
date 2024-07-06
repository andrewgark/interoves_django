import chardet
from collections import OrderedDict
import json

from django.contrib import admin
from django.forms import Textarea, ModelForm, ModelMultipleChoiceField
from django.forms.models import BaseInlineFormSet
from django.db import models
from django.shortcuts import get_object_or_404
from games.google.actions import create_google_doc
from games.models import *
from games.recheck import recheck, recheck_full, recheck_queue_from_this, recheck_queue_from_next


admin.site.register([CheckerType, HTMLPage, Like, Image, Audio, Project, Registration, TicketRequest])


def hintform_factory(task):
    class HintForm(ModelForm):
        required_hints = ModelMultipleChoiceField(
            queryset=Hint.objects.filter(task=task)
        )
    return HintForm


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ['visible_name', 'project', 'get_n_users_on', 'get_n_users_requested', 'is_tester', 'is_hidden']


class TaskInline(admin.TabularInline):
    model = Task
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
        models.JSONField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
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


def copy_game(modeladmin, request, queryset):
    for game in queryset.all():
        old_task_groups = game.task_groups.all()
        game.id = game.id + '_copy'
        game.save()
        for task_group in old_task_groups:
            old_tasks = task_group.tasks.all()
            task_group.pk = None
            task_group.game = game
            task_group.save()
            for task in old_tasks:
                old_hints = task.hints.all()
                task.pk = None
                task.task_group = task_group
                task.save()
                for hint in old_hints:
                    hint.pk = None
                    hint.task = task
                    hint.save()


@admin.register(Hint)
class HintAdmin(admin.ModelAdmin):
    list_display = ['task', 'number', 'text', 'points_penalty']

    def get_form(self, request, obj=None, **kwargs):
        if obj is not None and obj.task is not None:
            kwargs['form'] = hintform_factory(obj.task)
        return super(HintAdmin, self).get_form(request, obj, **kwargs)


@admin.register(HintAttempt)
class HintAttemptAdmin(admin.ModelAdmin):
    list_display = ['team', 'hint', 'time']


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    inlines = [
        TaskGroupInline
    ]
    list_display = ['__str__', 'name', 'theme', 'author', 'start_time', 'end_time', 'is_ready', 'is_playable', 'is_testing', 'is_registrable', 'requires_ticket']
    actions = [copy_game, create_new_google_doc]


class HintInline(admin.TabularInline):
    model = Hint
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }
    def formfield_for_manytomany(self, db_field, request=None, **kwargs):
        field = super(HintInline, self).formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == 'required_hints':
            if request._obj_ is not None:
                field.queryset = field.queryset.filter(task__exact = request._obj_)  
            else:
                field.queryset = field.queryset.none()
        return field


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    inlines = [
        HintInline
    ]
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }
    def get_form(self, request, obj=None, **kwargs):
        # just save obj reference for future processing in Inline
        request._obj_ = obj
        return super(TaskAdmin, self).get_form(request, obj, **kwargs)


def confirm_profile_team_request(modeladmin, request, queryset):
    for profile in queryset:
        profile.team_on = profile.team_requested
        profile.team_requested = None
        profile.save()


def clear_profile_team(modeladmin, request, queryset):
    for profile in queryset:
        profile.team_on = None
        profile.team_requested = None
        profile.save()


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


def _set_ok(attempt):
    attempt.points = attempt.get_max_points()
    attempt.status = 'Ok'
    if attempt.task.task_type == 'autohint':
        hints = set(Hint.objects.filter(task=attempt.task))
        hint_attempts = HintAttempt.objects.filter(team=attempt.team, hint__in=hints)
        hint_attempts = sorted(hint_attempts, key=lambda h: h.time, reverse=True)
        if len(hint_attempts) != 0:
            last_hint_attempt = hint_attempts[0]
            last_hint_attempt.is_real_request = False
            last_hint_attempt.save()
    attempt.save()


def set_ok(modeladmin, request, queryset):
    for attempt in queryset.all():
        _set_ok(attempt)


def _add_to_checker(attempt):
    if attempt.task.task_type != 'wall':
        attempt.task.checker_data = attempt.task.checker_data + '\n' + attempt.text
        attempt.task.save()
        return
    json_data = json.loads(attempt.task.checker_data)
    json_attempt = json.loads(attempt.text)
    for category in json_data['answers']:
        if sorted([x.lower() for x in category['words']]) == sorted([x.lower() for x in json_attempt['words']]):
            category['checker'] = category['checker'] + '\n' + json_attempt['explanation']
    attempt.task.checker_data = json.dumps(json_data)
    attempt.task.save()


def add_to_checker(modeladmin, request, queryset):
    for attempt in queryset.all():
        _add_to_checker(attempt)


def add_to_checker_and_recheck(modeladmin, request, queryset):
    for attempt in queryset.all():
        _add_to_checker(attempt)
        recheck(request, attempt.id)


def set_ok_and_create_new_task(modeladmin, request, queryset):
    for attempt in queryset.all():
        attempt.status = 'Ok'
        attempt.save()
        team_number = attempt.team.get_team_reg_number(attempt.task.task_group.game)
        if team_number is None:
            continue
        task = attempt.task
        task_data = json.loads(task.text)
        task_checker_data = json.loads(task.checker_data)
        try:
            max_number = max([
                int(x.number.split('.')[1])
                for x in Task.objects.filter(task_group=task.task_group)
                if x.number.startswith('2.')
            ])
        except:
            max_number = 0
        new_task = Task(
            number='2.{}'.format(max_number + 1),
            task_group=task.task_group,
            tags={'team': attempt.team.name, 'task': task_checker_data['tag']},
            text='{}<br><br><b>{}</b>'.format(task_checker_data.get('tag_text'), attempt.text),
            checker_data=task_data['list'][team_number],
            answer=task_data['list'][team_number],
            answer_comment='',
            task_type='with_tag',
            checker=CheckerType('equals_with_possible_spaces'),
            points=1
        )
        new_task.save()


def confirm_prestatus(modeladmin, request, queryset):
    for attempt in queryset.all():
        attempt.status = attempt.possible_status
        attempt.save()


recheck_attempt.short_description = "Recheck attempt"
recheck_full_attempt.short_description = "Recheck all attempts of this task"
recheck_queue_from_this_attempt.short_description = "Recheck wall attempt"
recheck_queue_from_next_attempt.short_description = "Recheck all attempts starting with next"
set_ok.short_description = "Set OK (and max points)"
add_to_checker.short_description = "Add to checker"
add_to_checker_and_recheck.short_description = "Add to checker and recheck"
set_ok_and_create_new_task.short_description = "Set OK and create new task (Game 49)"
confirm_prestatus.short_description = "Confirm Prestatus"


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }
    list_display = ['__str__', 'team', 'task', 'get_pretty_text', 'get_answer', 'status', 'points', 'get_max_points', 'skip', 'time']
    actions = [set_ok, confirm_prestatus, add_to_checker, add_to_checker_and_recheck, recheck_attempt, recheck_full_attempt, recheck_queue_from_this_attempt, set_ok_and_create_new_task]


@admin.register(PendingAttempt)
class PendingAttemptsAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }

    def get_queryset(self, request):
        qs = super(PendingAttemptsAdmin, self).get_queryset(request)
        return qs.filter(status='Pending')

    list_display = ['__str__', 'team', 'task', 'get_pretty_text', 'get_answer', 'status', 'points', 'get_max_points', 'time']
    actions = [set_ok, confirm_prestatus, add_to_checker, add_to_checker_and_recheck, recheck_attempt, recheck_full_attempt, recheck_queue_from_this_attempt, set_ok_and_create_new_task]


def confirm_ticket_request(modeladmin, request, queryset):
    for ticket_request in queryset.all():
        ticket_request.status = 'Accepted'
        ticket_request.save()
        ticket_request.team.tickets += ticket_request.tickets
        ticket_request.team.save()


def reject_ticket_request(modeladmin, request, queryset):
    for ticket_request in queryset.all():
        ticket_request.status = 'Rejected'
        ticket_request.save()


confirm_profile_team_request.short_description = "Confirm Team Request"
clear_profile_team.short_description = "Clear Profile Team"
confirm_ticket_request.short_description = "Confirm Ticket Request"
reject_ticket_request.short_description = "Reject Ticket Request"


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'team_on', 'team_requested', 'vk_url']
    actions = [confirm_profile_team_request, clear_profile_team]


@admin.register(PendingTicketRequest)
class PendingTicketRequestAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }

    def get_queryset(self, request):
        qs = super(PendingTicketRequestAdmin, self).get_queryset(request)
        return qs.filter(status='Pending')

    list_display = ['__str__', 'team', 'tickets', 'money', 'status', 'time']
    actions = [confirm_ticket_request, reject_ticket_request]
