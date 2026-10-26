from django.contrib import admin, messages
from django.forms import Textarea, ModelForm, ModelMultipleChoiceField
from django.db import models
from django.utils.html import format_html, format_html_join
from games.ops_actions import (
    confirm_attempt_prestatus,
    run_recheck,
)
from games.models import (
    Attempt,
    CHAIN_TASK_TYPES,
    ChainTaskState,
    Hint,
    HintAttempt,
    Like,
    PendingAttempt,
    Task,
)
from games.recheck import (
    recheck_full,
    recheck_queue_from_this,
    recheck_queue_from_next,
    recheck_team_task_all_chronological,
)


def hintform_factory(task):
    class HintForm(ModelForm):
        required_hints = ModelMultipleChoiceField(
            queryset=Hint.objects.filter(task=task),
            required=False,
        )
    return HintForm


@admin.register(Hint)
class HintAdmin(admin.ModelAdmin):
    list_display = ['task', 'number', 'text', 'points_penalty']
    search_fields = ['id', 'number', 'text', 'task__id', 'task__number']
    raw_id_fields = ['task', 'required_hints']

    def get_form(self, request, obj=None, **kwargs):
        if obj is not None and obj.task is not None:
            kwargs['form'] = hintform_factory(obj.task)
        return super(HintAdmin, self).get_form(request, obj, **kwargs)


@admin.register(HintAttempt)
class HintAttemptAdmin(admin.ModelAdmin):
    list_display = ['team', 'requested_by_profile', 'hint', 'time']
    raw_id_fields = ['team', 'user', 'requested_by_profile', 'hint']
    readonly_fields = ['requested_by_profile']
    search_fields = [
        'id', 'anon_key', 'team__name', 'user__username',
        'requested_by_profile__user__username',
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'team', 'user', 'requested_by_profile', 'hint', 'hint__task',
        )


@admin.register(Like)
class LikeAdmin(admin.ModelAdmin):
    list_display = ['id', 'value', 'task', 'team', 'user', 'anon_key']
    list_filter = ['value']
    search_fields = ['id', 'anon_key', 'team__name', 'user__username']
    raw_id_fields = ['team', 'user', 'task']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('team', 'user', 'task', 'task__task_group')


def _bulk_chain_recheck_ids(queryset, *, full_task=False):
    chain_ids = list(
        queryset.filter(task__task_type__in=CHAIN_TASK_TYPES)
        .values_list('id', flat=True)
    )
    if chain_ids:
        from games.attempt_admin_bulk import recheck_chain_attempts
        recheck_chain_attempts(chain_ids, full_task=full_task)
    return set(chain_ids)


def _queue_receipt_links(receipt):
    jobs = (receipt or {}).get('jobs') or []
    if not jobs:
        return ''
    links = format_html_join(
        ', ',
        '<a href="{}">Job #{}</a>',
        ((job.get('observatory_url') or job.get('url'), job['id']) for job in jobs),
    )
    return format_html(' Jobs: {}.', links)


def recheck_attempt(modeladmin, request, queryset):
    chain_ids = _bulk_chain_recheck_ids(queryset)
    for attempt_id in queryset.values_list('id'):
        if attempt_id[0] not in chain_ids:
            run_recheck(attempt_id[0])


def recheck_full_attempt(modeladmin, request, queryset):
    chain_ids = _bulk_chain_recheck_ids(queryset, full_task=True)
    non_chain_task_ids = set(
        queryset.exclude(task__task_type__in=CHAIN_TASK_TYPES)
        .values_list('task_id', flat=True)
    )
    for task_id in non_chain_task_ids:
        recheck_full(request, task=Task.objects.get(pk=task_id))


def recheck_queue_from_this_attempt(modeladmin, request, queryset):
    chain_ids = _bulk_chain_recheck_ids(queryset)
    for attempt_id in queryset.values_list('id'):
        if attempt_id[0] not in chain_ids:
            recheck_queue_from_this(request, attempt_id[0])


def recheck_queue_from_next_attempt(modeladmin, request, queryset):
    chain_ids = _bulk_chain_recheck_ids(queryset)
    for attempt_id in queryset.values_list('id'):
        if attempt_id[0] not in chain_ids:
            recheck_queue_from_next(request, attempt_id[0])


def recheck_team_task_all_chronological_action(modeladmin, request, queryset):
    from django.contrib import messages

    from games.models import WordSaladRecheckJob

    chain_ids = _bulk_chain_recheck_ids(queryset)

    queued = 0
    non_chain_actor_keys = {}
    for attempt in queryset.exclude(task__task_type__in=CHAIN_TASK_TYPES).select_related('task', 'game', 'team', 'user'):
        actor_key = (
            attempt.task_id,
            attempt.game_id,
            attempt.team_id,
            attempt.user_id,
            attempt.anon_key,
        )
        non_chain_actor_keys[actor_key] = min(
            attempt.pk, non_chain_actor_keys.get(actor_key, attempt.pk),
        )
    for attempt_id in sorted(non_chain_actor_keys.values()):
        result = recheck_team_task_all_chronological(request, attempt_id)
        if isinstance(result, WordSaladRecheckJob):
            queued += 1
    if queued:
        modeladmin.message_user(
            request,
            'Queued chronological recheck for {} actor(s). The worker will replay them.'.format(queued),
            level=messages.SUCCESS,
        )


def add_to_checker(modeladmin, request, queryset):
    from games.attempt_admin_bulk import add_attempts_to_checker
    from django.contrib import messages
    result = add_attempts_to_checker(
        queryset.values_list('id', flat=True), return_receipt=True,
    )
    modeladmin.message_user(
        request,
        format_html(
            'Добавлено в checker ответов: {}.{}',
            result['accepted_count'],
            _queue_receipt_links(result['queue_receipt']),
        ),
        messages.SUCCESS,
    )


def add_to_checker_and_recheck(modeladmin, request, queryset):
    from games.attempt_admin_bulk import add_attempts_to_checker
    from django.contrib import messages
    result = add_attempts_to_checker(
        queryset.values_list('id', flat=True),
        recheck_selected_non_chain=True,
        return_receipt=True,
    )
    modeladmin.message_user(
        request,
        format_html(
            'Добавлено в checker и поставлено на recheck ответов: {}.{}',
            result['accepted_count'],
            _queue_receipt_links(result['queue_receipt']),
        ),
        messages.SUCCESS,
    )


def accept_pending(modeladmin, request, queryset):
    from games.attempt_admin_bulk import accept_pending_attempts
    chain_count = queryset.filter(task__task_type__in=CHAIN_TASK_TYPES).count()
    result = accept_pending_attempts(
        queryset.values_list('id', flat=True), return_receipt=True,
    )
    accepted = result['accepted_count']
    suffix = ''
    if chain_count:
        suffix = (
            ' Для {} chain-посылок replay поставлен в durable queue; '
            'Pending по другим категориям сохраняются до отдельного решения.'.format(chain_count)
        )
    modeladmin.message_user(
        request,
        format_html(
            'Принято ответов: {}.{}{}',
            accepted,
            suffix,
            _queue_receipt_links(result['queue_receipt']),
        ),
        messages.SUCCESS,
    )


def reject_pending(modeladmin, request, queryset):
    from games.attempt_admin_bulk import reject_pending_attempts
    rejected = reject_pending_attempts(queryset.values_list('id', flat=True))
    modeladmin.message_user(request, 'Отклонено ответов: {}'.format(rejected), messages.SUCCESS)


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
add_to_checker.short_description = "Add to checker"
add_to_checker_and_recheck.short_description = "Add to checker and recheck"
accept_pending.short_description = "ДА — засчитать и перепроверить хронологически"
reject_pending.short_description = "НЕТ — отклонить ответ"
confirm_prestatus.short_description = "Confirm Prestatus"


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }
    raw_id_fields = ['task', 'team', 'user', 'submitted_by_profile', 'game']
    list_display = ['__str__', 'team', 'task', 'game', 'get_pretty_text', 'get_answer', 'status', 'points', 'get_max_points', 'skip', 'time']
    actions = [
        confirm_prestatus,
        add_to_checker,
        add_to_checker_and_recheck,
        recheck_attempt,
        recheck_full_attempt,
        recheck_queue_from_this_attempt,
        recheck_team_task_all_chronological_action,
    ]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('team', 'task', 'user', 'submitted_by_profile')

    def get_readonly_fields(self, request, obj=None):
        """Keep ownership immutable in ordinary Attempt editing.

        Ownership transfers have dedicated claim/merge paths and must not be
        indistinguishable from routine checker/status edits in the admin.
        """
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.extend(
                field for field in ('user', 'anon_key', 'team', 'submitted_by_profile')
                if field not in fields
            )
        return tuple(fields)


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
        accept_pending,
        reject_pending,
    ]


def recheck_chain_task_action(modeladmin, request, queryset):
    from django.contrib import messages

    from games.word_salad_recheck import enqueue_actor_rechecks

    queued = 0
    for state_row in queryset.select_related('task', 'team', 'user', 'game'):
        if state_row.game_id is None:
            continue
        enqueue_actor_rechecks(
            task=state_row.task,
            game=state_row.game,
            actors=[(
                state_row.team_id,
                state_row.user_id,
                state_row.anon_key or None,
                state_row.replay_slot_id,
            )],
        )
        queued += 1
    if queued:
        modeladmin.message_user(
            request,
            'Queued full-chain recheck for {} actor(s).'.format(queued),
            level=messages.SUCCESS,
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
