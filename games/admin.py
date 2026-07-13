import chardet
from collections import OrderedDict
import json

from django.contrib import admin, messages
from django.forms import Textarea, ModelForm, ModelMultipleChoiceField
from django.forms.models import BaseInlineFormSet
from django.db import models
from django.shortcuts import get_object_or_404
from games.google.actions import create_google_doc
from games.ops_actions import (
    accept_ticket,
    add_attempt_to_checker,
    confirm_attempt_prestatus,
    reject_ticket,
    run_recheck,
    run_recheck_after_add_to_checker,
    set_attempt_ok,
    set_ok_and_create_new_task,
)
from games.models import (
    Attempt,
    Audio,
    ChainTaskState,
    CheckerType,
    CorporateGameOrder,
    Game,
    GameResultsSnapshot,
    GameTaskGroup,
    HiddenAnonKey,
    Hint,
    HintAttempt,
    HTMLPage,
    Image,
    Like,
    OrderGameClient,
    OrderGameReview,
    BugReport,
    PendingAttempt,
    PendingBugReport,
    PendingTicketRequest,
    Profile,
    ProfileTeamMembership,
    Project,
    Registration,
    Task,
    TaskGroup,
    Team,
    TicketRequest,
)
from games.recheck import (
    recheck_chain_task,
    recheck_full,
    recheck_queue_from_this,
    recheck_queue_from_next,
    recheck_team_task_all_chronological,
)
from games.results_snapshot import freeze_game_results


admin.site.register([CheckerType, HTMLPage, Like, Image, Audio, Project, Registration, TicketRequest, BugReport])


@admin.register(CorporateGameOrder)
class CorporateGameOrderAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'company_name', 'contact_name', 'contact_method', 'contact_value', 'email_sent')
    list_filter = ('email_sent', 'contact_method')
    search_fields = ('company_name', 'contact_name', 'contact_value', 'contact_other_label', 'message')
    readonly_fields = ('created_at', 'email_sent')


@admin.register(OrderGameClient)
class OrderGameClientAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'sort_order', 'is_published')
    list_editable = ('sort_order', 'is_published')
    search_fields = ('company_name',)


@admin.register(OrderGameReview)
class OrderGameReviewAdmin(admin.ModelAdmin):
    list_display = ('name', 'caption', 'is_important', 'is_published')
    list_filter = ('is_important', 'is_published')
    list_editable = ('is_important', 'is_published')
    search_fields = ('name', 'caption', 'text')


def hintform_factory(task):
    class HintForm(ModelForm):
        required_hints = ModelMultipleChoiceField(
            queryset=Hint.objects.filter(task=task),
            required=False,
        )
    return HintForm


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ['visible_name', 'project', 'get_n_users_on', 'get_n_users_requested', 'is_tester', 'is_hidden']


@admin.register(HiddenAnonKey)
class HiddenAnonKeyAdmin(admin.ModelAdmin):
    list_display = ['anon_key', 'note']
    search_fields = ['anon_key', 'note']


class TaskInline(admin.TabularInline):
    model = Task
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
        models.JSONField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }


class GameTaskGroupInlineOnTaskGroup(admin.TabularInline):
    model = GameTaskGroup
    fk_name = 'task_group'
    autocomplete_fields = ['game']
    extra = 0

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return GameTaskGroup.order_queryset_by_number(qs)


@admin.register(TaskGroup)
class TaskGroupAdmin(admin.ModelAdmin):
    inlines = [
        GameTaskGroupInlineOnTaskGroup,
        TaskInline,
    ]
    list_display = ['__str__', 'label', 'is_18_plus']
    search_fields = ['label', 'id']


class TaskGroupInline(admin.TabularInline):
    """Deprecated: use GameTaskGroup on Game. Kept as alias for migration period."""
    model = GameTaskGroup
    fk_name = 'game'
    autocomplete_fields = ['task_group']
    extra = 0

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return GameTaskGroup.order_queryset_by_number(qs)


def create_new_google_doc(modeladmin, request, queryset):
    for game_id in queryset.values_list('id'):
        create_google_doc(get_object_or_404(Game, id=game_id[0]))


def copy_game(modeladmin, request, queryset):
    for game in queryset.all():
        old_links = list(
            GameTaskGroup.objects.filter(game=game).select_related('task_group')
        )
        game.id = game.id + '_copy'
        game.save()
        for link in old_links:
            old_tg = link.task_group
            old_tasks = list(old_tg.tasks.all())
            new_tg = TaskGroup(
                label=old_tg.label,
                rules=old_tg.rules,
                text=old_tg.text,
                checker=old_tg.checker,
                points=old_tg.points,
                max_attempts=old_tg.max_attempts,
                image_width=old_tg.image_width,
                tags=dict(old_tg.tags or {}),
                view=old_tg.view,
                is_18_plus=old_tg.is_18_plus,
            )
            new_tg.save()
            GameTaskGroup.objects.create(
                game=game,
                task_group=new_tg,
                number=link.number,
                name=link.name,
            )
            for task in old_tasks:
                old_hints = list(task.hints.all())
                task.pk = None
                task.task_group = new_tg
                task.save()
                for hint in old_hints:
                    hint.pk = None
                    hint.task = task
                    hint.save()


def _freeze_results_message(mode_label, created, unchanged):
    """
    `unchanged` = snapshot already existed and was not overwritten (admin never overwrites).
    """
    parts = []
    if created:
        parts.append(f'{created} game(s): snapshot written')
    if unchanged:
        parts.append(
            f'{unchanged} game(s): already had a frozen {mode_label} snapshot (left unchanged)'
        )
    msg = 'Results freeze — ' + ('; '.join(parts) if parts else 'nothing to do')
    if unchanged:
        msg += (
            '. To replace existing snapshots, run: '
            f'python manage.py freeze_results_snapshots --mode {mode_label} --game-id <id> --overwrite'
        )
    return msg


@admin.action(description='Freeze tournament results (selected games)')
def freeze_results_tournament(modeladmin, request, queryset):
    created = 0
    unchanged = 0
    for game in queryset.all():
        _, did = freeze_game_results(game, mode='tournament', overwrite=False)
        if did:
            created += 1
        else:
            unchanged += 1
    modeladmin.message_user(
        request,
        _freeze_results_message('tournament', created, unchanged),
    )


@admin.action(description='Freeze general results (selected games)')
def freeze_results_general(modeladmin, request, queryset):
    created = 0
    unchanged = 0
    for game in queryset.all():
        _, did = freeze_game_results(game, mode='general', overwrite=False)
        if did:
            created += 1
        else:
            unchanged += 1
    modeladmin.message_user(
        request,
        _freeze_results_message('general', created, unchanged),
    )


@admin.action(description='Разморозить результаты (удалить снимки турнира и общей таблицы)')
def unfreeze_results_snapshots(modeladmin, request, queryset):
    """Удаляет GameResultsSnapshot — страницы результатов снова считаются на лету."""
    total = 0
    for game in queryset.all():
        n, _ = GameResultsSnapshot.objects.filter(game=game).delete()
        total += n
    modeladmin.message_user(
        request,
        f'Разморозка: удалено записей снимков: {total} (выбрано игр: {queryset.count()}).',
    )


@admin.action(description='Freeze tournament results (ALL games)')
def freeze_results_all_games(modeladmin, request, queryset):
    # This can be extremely slow on large datasets and will block the HTTP request.
    # Use the management command instead:
    #   python manage.py freeze_results_snapshots --mode tournament --only-missing
    modeladmin.message_user(
        request,
        "Refused: freezing ALL games can take a long time and may hang the admin request. "
        "Run: python manage.py freeze_results_snapshots --mode tournament --only-missing",
        level='warning',
    )


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
        TaskGroupInline,
    ]
    raw_id_fields = ['section_default_rules']
    search_fields = ['id', 'name', 'outside_name']
    list_display = ['__str__', 'name', 'theme', 'author', 'start_time', 'end_time', 'is_ready', 'is_playable', 'is_testing', 'is_registrable', 'requires_ticket', 'is_18_plus']
    actions = [
        copy_game,
        create_new_google_doc,
        freeze_results_tournament,
        freeze_results_general,
        unfreeze_results_snapshots,
        freeze_results_all_games,
    ]

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == 'image':
            kwargs['required'] = False
        return super().formfield_for_dbfield(db_field, request, **kwargs)


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
    list_display = ['id', '__str__', 'task_group', 'number', 'is_removed']
    list_filter = ['is_removed']
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

    def save_model(self, request, obj, form, change):
        if obj.task_type == 'raddle':
            from games.raddle import validate_raddle_checker_data
            errors = validate_raddle_checker_data(obj.checker_data, obj.answer)
            if errors:
                for err in errors:
                    messages.error(request, 'Raddle: {}'.format(err))
                return
        super(TaskAdmin, self).save_model(request, obj, form, change)


def confirm_profile_team_request(modeladmin, request, queryset):
    for profile in queryset:
        if not profile.team_requested:
            continue
        team = profile.team_requested
        mk_primary = profile.join_accept_as_primary
        profile.team_requested = None
        profile.join_accept_as_primary = True
        profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
        profile.add_team_membership(team, make_primary=mk_primary)


def clear_profile_team(modeladmin, request, queryset):
    for profile in queryset:
        ProfileTeamMembership.objects.filter(profile=profile).delete()
        profile.team_on = None
        profile.team_requested = None
        profile.join_accept_as_primary = True
        profile.save()


def recheck_attempt(modeladmin, request, queryset):
    for attempt_id in queryset.values_list('id'):
        run_recheck(attempt_id[0])


def recheck_full_attempt(modeladmin, request, queryset):
    for attempt_id in queryset.values_list('id'):
        recheck_full(request, attempt_id[0])


def recheck_queue_from_this_attempt(modeladmin, request, queryset):
    for attempt_id in queryset.values_list('id'):
        recheck_queue_from_this(request, attempt_id[0])


def recheck_queue_from_next_attempt(modeladmin, request, queryset):
    for attempt_id in queryset.values_list('id'):
        recheck_queue_from_next(request, attempt_id[0])


def recheck_team_task_all_chronological_action(modeladmin, request, queryset):
    for attempt_id in queryset.values_list('id'):
        recheck_team_task_all_chronological(request, attempt_id[0])


def _set_ok(attempt):
    set_attempt_ok(attempt)


def set_ok(modeladmin, request, queryset):
    for attempt in queryset.all():
        set_attempt_ok(attempt)


def _add_to_checker(attempt):
    add_attempt_to_checker(attempt)


def add_to_checker(modeladmin, request, queryset):
    for attempt in queryset.all():
        add_attempt_to_checker(attempt)


def add_to_checker_and_recheck(modeladmin, request, queryset):
    for attempt in queryset.all():
        run_recheck_after_add_to_checker(attempt.id)


def set_ok_and_create_new_task_action(modeladmin, request, queryset):
    for attempt in queryset.all():
        set_ok_and_create_new_task(attempt)


def confirm_prestatus(modeladmin, request, queryset):
    for attempt in queryset.all():
        confirm_attempt_prestatus(attempt)


recheck_attempt.short_description = "Recheck attempt"
recheck_full_attempt.short_description = "Recheck all attempts of this task (all teams)"
recheck_queue_from_this_attempt.short_description = (
    "Recheck this and later attempts (same team/user, same task; chronological). "
    "For replacements_lines state chain, prefer «all chronological» if checker changed earlier."
)
recheck_queue_from_next_attempt.short_description = "Recheck attempts strictly after this one (same team & task)"
recheck_team_task_all_chronological_action.short_description = (
    "Recheck all attempts by this actor on this task (chronological, same team/user)"
)
set_ok.short_description = "Set OK (and max points)"
add_to_checker.short_description = "Add to checker"
add_to_checker_and_recheck.short_description = "Add to checker and recheck"
set_ok_and_create_new_task_action.short_description = "Set OK and create new task (Game 49)"
confirm_prestatus.short_description = "Confirm Prestatus"


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }
    raw_id_fields = ['task', 'team', 'user', 'game']
    list_display = ['__str__', 'team', 'task', 'game', 'get_pretty_text', 'get_answer', 'status', 'points', 'get_max_points', 'skip', 'time']
    actions = [
        set_ok,
        confirm_prestatus,
        add_to_checker,
        add_to_checker_and_recheck,
        recheck_attempt,
        recheck_full_attempt,
        recheck_queue_from_this_attempt,
        recheck_team_task_all_chronological_action,
        set_ok_and_create_new_task_action,
    ]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('team', 'task', 'user')


@admin.register(PendingAttempt)
class PendingAttemptsAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }
    raw_id_fields = ['task', 'team', 'user']

    def get_queryset(self, request):
        qs = super(PendingAttemptsAdmin, self).get_queryset(request)
        return qs.select_related('team', 'task', 'user').filter(status='Pending')

    list_display = ['__str__', 'team', 'task', 'get_pretty_text', 'get_answer', 'status', 'points', 'get_max_points', 'time']
    actions = [
        set_ok,
        confirm_prestatus,
        add_to_checker,
        add_to_checker_and_recheck,
        recheck_attempt,
        recheck_full_attempt,
        recheck_queue_from_this_attempt,
        recheck_team_task_all_chronological_action,
        set_ok_and_create_new_task_action,
    ]


def recheck_chain_task_action(modeladmin, request, queryset):
    for state_row in queryset.select_related('task', 'team', 'user', 'game'):
        recheck_chain_task(
            task=state_row.task,
            team=state_row.team,
            user=state_row.user if state_row.user_id else None,
            anon_key=state_row.anon_key,
            game=state_row.game,
        )


recheck_chain_task_action.short_description = 'Recheck full chain (rebuild ChainTaskState from all attempts)'


@admin.register(ChainTaskState)
class ChainTaskStateAdmin(admin.ModelAdmin):
    list_display = [
        '__str__', 'team', 'user', 'task', 'game_mode',
        'state_summary_display', 'updated_at', 'last_attempt',
    ]
    list_filter = ['game_mode', 'task__task_type', 'game']
    raw_id_fields = ['team', 'task', 'last_attempt', 'user']
    readonly_fields = ['state_summary_display', 'updated_at']
    actions = [recheck_chain_task_action]

    def state_summary_display(self, obj):
        return obj.state_summary()

    state_summary_display.short_description = 'State summary'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('team', 'task', 'user', 'last_attempt')


def confirm_ticket_request(modeladmin, request, queryset):
    for ticket_request in queryset.all():
        accept_ticket(ticket_request.pk, source='admin')


def reject_ticket_request(modeladmin, request, queryset):
    for ticket_request in queryset.all():
        reject_ticket(ticket_request.pk, source='admin')


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


def mark_bug_report_reviewed(modeladmin, request, queryset):
    queryset.update(status='Reviewed')


def mark_bug_report_dismissed(modeladmin, request, queryset):
    queryset.update(status='Dismissed')


mark_bug_report_reviewed.short_description = 'Mark Reviewed'
mark_bug_report_dismissed.short_description = 'Mark Dismissed'


@admin.register(PendingBugReport)
class PendingBugReportAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 4, 'cols': 60})},
    }
    raw_id_fields = ['task', 'game', 'team', 'user']
    readonly_fields = ['time', 'page_url', 'anon_key']

    def get_queryset(self, request):
        qs = super(PendingBugReportAdmin, self).get_queryset(request)
        return qs.select_related('task', 'game', 'team', 'user').filter(status='Pending')

    list_display = ['__str__', 'game', 'task', 'team', 'user', 'time']
    search_fields = ['text', 'task__number', 'game__id', 'game__name']
    actions = [mark_bug_report_reviewed, mark_bug_report_dismissed]
