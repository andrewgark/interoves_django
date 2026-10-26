from datetime import timedelta
import json

from django.contrib import admin, messages
from django.forms import Textarea
from django.db import models
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from games.models import (
    DailyDifficultyQueueStatus,
    DailyGameDifficulty,
    GameDifficultyNorm,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
    DailySolveTiming,
    StatisticsEvent,
)


@admin.action(description='Пересчитать выбранные оценки сложности')
def recalculate_selected_difficulties(modeladmin, request, queryset):
    from games.daily.difficulty.core import calculate_game_difficulty

    updated = 0
    for snapshot in queryset.select_related('placement', 'placement__game', 'placement__task_group'):
        calculate_game_difficulty(snapshot.placement, save=True)
        updated += 1
    messages.success(request, 'Пересчитано оценок: {}'.format(updated))


@admin.action(description='Вернуть выбранные в очередь сейчас')
def enqueue_selected_difficulties(modeladmin, request, queryset):
    updated = queryset.update(
        dirty=True,
        refresh_not_before=timezone.now(),
        refresh_claim_token=None,
        refresh_claimed_until=None,
    )
    messages.success(request, 'Возвращено в очередь: {}'.format(updated))


@admin.register(DailyGameDifficulty)
class DailyGameDifficultyAdmin(admin.ModelAdmin):
    change_list_template = 'admin/games/dailygamedifficulty/change_list.html'
    list_display = (
        'placement', 'n', 'median_time', 'typical_time', 'time_ratio', 'paired_time_ratio',
        'median_attempts', 'typical_attempts', 'attempts_ratio',
        'median_errors', 'help_rate', 'unfinished_rate', 'raw_rating',
        'adjusted_rating', 'stars_display', 'is_preliminary', 'dirty',
        'data_revision', 'calculated_revision', 'refresh_not_before', 'calculated_at',
    )
    list_filter = ('placement__game', 'is_preliminary', 'dirty', 'stars')
    search_fields = ('placement__number', 'placement__name', 'placement__game__name')
    readonly_fields = (
        'placement', 'n', 'payload', 'stars', 'is_preliminary', 'dirty', 'calculated_at',
        'published_at', 'data_revision', 'calculated_revision', 'refresh_not_before',
        'refresh_claim_token', 'refresh_claimed_until', 'refresh_fail_count',
        'refresh_last_error', 'norm_version',
        'median_time', 'typical_time', 'time_ratio', 'median_errors', 'typical_errors',
        'median_attempts', 'typical_attempts', 'attempts_ratio',
        'error_ratio', 'help_rate', 'typical_help_rate', 'unfinished_rate',
        'typical_unfinished_rate', 'raw_rating', 'adjusted_rating', 'component_scores',
        'content_changed_at', 'classic_ratios', 'paired_ratios', 'paired_n', 'blend_weights',
    )
    actions = (enqueue_selected_difficulties, recalculate_selected_difficulties)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                'queue/',
                self.admin_site.admin_view(self.queue_view),
                name='games_dailygamedifficulty_queue',
            ),
        ]
        return custom + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context['difficulty_queue_url'] = reverse(
            'admin:games_dailygamedifficulty_queue',
        )
        return super().changelist_view(request, extra_context=extra_context)

    def queue_view(self, request):
        from games.daily.difficulty.core import SUPPORTED_GAME_IDS, _supported_placements
        from games.daily.difficulty.refresh import (
            due_daily_difficulty_queryset,
            repair_daily_difficulty_queue,
            run_daily_difficulty_refresh,
        )

        if not self.has_view_permission(request):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        now = timezone.now()
        if request.method == 'POST':
            if not self.has_change_permission(request):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied
            operation = request.POST.get('operation')
            if operation == 'repair':
                report = repair_daily_difficulty_queue(now=now)
                messages.success(
                    request,
                    'Очередь восстановлена: создано {}, исправлено сроков {}, '
                    'снято просроченных аренд {}, готово к обработке {}.'.format(
                        report['created'], report['rescheduled'], report['released'],
                        report['due'],
                    ),
                )
            elif operation == 'run':
                try:
                    results = run_daily_difficulty_refresh(limit=10, worker='admin')
                except Exception as exc:
                    messages.error(
                        request,
                        'Запуск очереди завершился ошибкой: {}'.format(exc),
                    )
                else:
                    messages.success(
                        request,
                        'Такт очереди завершён, пересчитано: {}.'.format(len(results)),
                    )
            else:
                messages.error(request, 'Неизвестная операция очереди.')
            return HttpResponseRedirect(request.path)

        queryset = DailyGameDifficulty.objects.select_related(
            'placement', 'placement__game', 'placement__task_group',
        )
        due_queryset = due_daily_difficulty_queryset(now=now)
        schedule_drift = queryset.filter(
            calculated_at__isnull=True,
            dirty=True,
            published_at__lte=now,
        ).filter(
            models.Q(refresh_not_before__isnull=True)
            | models.Q(refresh_not_before__gt=now),
        )
        queue_rows = list(
            queryset.filter(dirty=True).order_by('refresh_not_before', 'id')[:100]
        )
        for row in queue_rows:
            if row.refresh_fail_count:
                row.queue_state = 'ошибка / retry'
            elif row.refresh_claimed_until and row.refresh_claimed_until > now:
                row.queue_state = 'обрабатывается'
            elif (
                row.calculated_at is None
                and row.published_at
                and row.published_at <= now
                and (row.refresh_not_before is None or row.refresh_not_before > now)
            ):
                row.queue_state = 'просрочен первый запуск'
            elif row.refresh_not_before is not None and row.refresh_not_before <= now:
                row.queue_state = 'готово'
            else:
                row.queue_state = 'отложено'

        per_game = []
        for game_id in SUPPORTED_GAME_IDS:
            game_rows = queryset.filter(placement__game_id=game_id)
            per_game.append({
                'game_id': game_id,
                'total': game_rows.count(),
                'dirty': game_rows.filter(dirty=True).count(),
                'due': due_queryset.filter(placement__game_id=game_id).count(),
                'failed': game_rows.filter(refresh_fail_count__gt=0).count(),
                'never': game_rows.filter(calculated_at__isnull=True).count(),
            })

        status = DailyDifficultyQueueStatus.objects.filter(pk=1).first()
        worker_running = bool(
            status and status.last_started_at and (
                status.last_finished_at is None
                or status.last_started_at > status.last_finished_at
            )
        )
        heartbeat_late = bool(
            status is None
            or status.last_success_at is None
            or status.last_success_at < now - timedelta(minutes=3)
        )
        supported_ids = {row.pk for row in _supported_placements()}
        existing_ids = set(queryset.values_list('placement_id', flat=True))
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'title': 'Очередь подсчёта сложности',
            'now': now,
            'status': status,
            'worker_running': worker_running,
            'heartbeat_late': heartbeat_late,
            'counts': {
                'total': queryset.count(),
                'dirty': queryset.filter(dirty=True).count(),
                'due': due_queryset.count(),
                'scheduled': queryset.filter(dirty=True, refresh_not_before__gt=now).count(),
                'active': queryset.filter(refresh_claimed_until__gt=now).count(),
                'failed': queryset.filter(refresh_fail_count__gt=0).count(),
                'never': queryset.filter(calculated_at__isnull=True).count(),
                'drift': schedule_drift.count(),
                'missing': len(supported_ids - existing_ids),
            },
            'per_game': per_game,
            'queue_rows': queue_rows,
            'changelist_url': reverse('admin:games_dailygamedifficulty_changelist'),
            'can_repair': self.has_change_permission(request),
        }
        return TemplateResponse(
            request,
            'admin/games/dailygamedifficulty/queue.html',
            context,
        )

    def has_add_permission(self, request):
        return False

    def _payload_value(self, obj, *path):
        value = obj.payload or {}
        for part in path:
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def _number(self, value, digits=2):
        if value is None:
            return '—'
        return round(float(value), digits)

    def median_time(self, obj):
        return self._number(self._payload_value(obj, 'metrics', 'median_time'), 1)

    def typical_time(self, obj):
        return self._number(self._payload_value(obj, 'typical', 'median_time'), 1)

    def time_ratio(self, obj):
        return self._number(self._payload_value(obj, 'ratios', 'time'))

    def median_attempts(self, obj):
        return self._number(self._payload_value(obj, 'metrics', 'median_attempts'), 1)

    def typical_attempts(self, obj):
        return self._number(self._payload_value(obj, 'typical', 'median_attempts'), 1)

    def attempts_ratio(self, obj):
        return self._number(self._payload_value(obj, 'ratios', 'attempts'))

    def median_errors(self, obj):
        return self._number(self._payload_value(obj, 'metrics', 'median_errors'), 1)

    def typical_errors(self, obj):
        return self._number(self._payload_value(obj, 'typical', 'median_errors'), 1)

    def error_ratio(self, obj):
        return self._number(self._payload_value(obj, 'ratios', 'errors'))

    def help_rate(self, obj):
        return self._number(self._payload_value(obj, 'metrics', 'help_rate'), 3)

    def typical_help_rate(self, obj):
        return self._number(self._payload_value(obj, 'typical', 'help_rate'), 3)

    def unfinished_rate(self, obj):
        return self._number(self._payload_value(obj, 'metrics', 'unfinished_rate'), 3)

    def typical_unfinished_rate(self, obj):
        return self._number(self._payload_value(obj, 'typical', 'unfinished_rate'), 3)

    def raw_rating(self, obj):
        return self._number(self._payload_value(obj, 'raw_rating'))

    def adjusted_rating(self, obj):
        return self._number(self._payload_value(obj, 'adjusted_rating'))

    def component_scores(self, obj):
        return self._payload_value(obj, 'scores') or '—'

    def paired_time_ratio(self, obj):
        return self._number(self._payload_value(obj, 'ratios_paired', 'time'))

    def classic_ratios(self, obj):
        return self._payload_value(obj, 'ratios_classic') or '—'

    def paired_ratios(self, obj):
        return self._payload_value(obj, 'ratios_paired') or '—'

    def paired_n(self, obj):
        return self._payload_value(obj, 'paired_n') or '—'

    def blend_weights(self, obj):
        return self._payload_value(obj, 'blend_weights') or '—'

    def stars_display(self, obj):
        if obj.stars is None:
            return '—'
        return '{}{}'.format('★' * obj.stars, '☆' * (5 - obj.stars))

    stars_display.short_description = 'Звёзды'


@admin.register(GameDifficultyNorm)
class GameDifficultyNormAdmin(admin.ModelAdmin):
    list_display = (
        'game_id', 'typical_time', 'typical_attempts', 'typical_errors', 'typical_help_rate',
        'typical_unfinished_rate', 'version', 'calculated_at',
    )
    readonly_fields = (
        'game_id', 'typical_time', 'typical_attempts', 'typical_errors', 'typical_help_rate',
        'typical_unfinished_rate', 'version', 'calculated_at', 'payload',
    )

    def has_add_permission(self, request):
        return False


@admin.register(PlayerStartedGame)
class PlayerStartedGameAdmin(admin.ModelAdmin):
    raw_id_fields = ['team', 'user', 'game', 'task_group']
    readonly_fields = ['started_at', 'metrika_acked_at']
    list_display = [
        'started_at', 'game_kind', 'public_game_id', 'actor_label', 'is_backfilled', 'metrika_acked_at',
    ]
    list_filter = ['game_kind', 'is_backfilled', 'metrika_acked_at']
    search_fields = ['game_instance_id', 'public_game_id', 'anon_key', 'user__username', 'team__name']
    date_hierarchy = 'started_at'

    def actor_label(self, obj):
        return obj.team or obj.user or obj.anon_key or '—'

    actor_label.short_description = 'Игрок'


@admin.register(PlayerCompletedGame)
class PlayerCompletedGameAdmin(admin.ModelAdmin):
    raw_id_fields = ['team', 'user', 'game', 'task_group']
    readonly_fields = ['completed_at', 'metrika_acked_at']
    list_display = [
        'completed_at', 'game_kind', 'public_game_id', 'actor_label',
        'is_backfilled', 'metrika_acked_at',
    ]
    list_filter = ['game_kind', 'is_backfilled', 'metrika_acked_at']
    search_fields = ['game_instance_id', 'public_game_id', 'anon_key', 'user__username', 'team__name']
    date_hierarchy = 'completed_at'

    def actor_label(self, obj):
        return obj.team or obj.user or obj.anon_key or '—'

    actor_label.short_description = 'Игрок'


@admin.register(DailySolveTiming)
class DailySolveTimingAdmin(admin.ModelAdmin):
    raw_id_fields = ['team', 'user', 'game', 'task_group', 'replay_slot']
    list_display = [
        'updated_at', 'actor_label', 'game', 'status', 'accumulated_ms',
        'timing_version', 'completed_at',
    ]
    list_filter = ['status', 'timing_version']
    search_fields = ['anon_key', 'user__username', 'team__name', 'team__visible_name']
    readonly_fields = ['created_at', 'updated_at']

    def actor_label(self, obj):
        return obj.team or obj.user or obj.anon_key or '—'

    actor_label.short_description = 'Игрок'


@admin.register(PlayerAnalyticsState)
class PlayerAnalyticsStateAdmin(admin.ModelAdmin):
    raw_id_fields = ['team', 'user']
    list_display = [
        'actor_label', 'signup_at', 'signup_goal_acked_at',
        'activated_at', 'activation_is_backfilled', 'activation_goal_acked_at',
    ]
    search_fields = ['anon_key', 'user__username', 'team__name']

    def actor_label(self, obj):
        return obj.team or obj.user or obj.anon_key or '—'

    actor_label.short_description = 'Игрок'


@admin.register(StatisticsEvent)
class StatisticsEventAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.JSONField: {'widget': Textarea(attrs={'rows': 4, 'cols': 60})},
    }
    raw_id_fields = ['user']
    readonly_fields = ['time']
    list_display = ['time', 'kind', 'user', 'payload_summary']
    list_filter = ['kind']
    search_fields = ['kind', 'user__username', 'user__email', 'payload']
    fields = ['kind', 'user', 'payload', 'time']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')

    def payload_summary(self, obj):
        payload = obj.payload or {}
        if obj.kind == StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED:
            return 'moved={} hints={} likes={} anon={}'.format(
                payload.get('moved'),
                payload.get('moved_hints'),
                payload.get('moved_likes'),
                (payload.get('anon_key') or '')[:12],
            )
        try:
            return json.dumps(payload, ensure_ascii=False)[:120]
        except TypeError:
            return str(payload)[:120]

    payload_summary.short_description = 'payload'
