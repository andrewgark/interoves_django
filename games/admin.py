import chardet
from collections import OrderedDict
import json

from django import forms
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
    AlphabettyDictSuggestion,
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
    PendingAlphabettyDictSuggestion,
    PendingAttempt,
    PendingBugReport,
    Donation,
    PendingTicketRequest,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
    Profile,
    ProfileTeamMembership,
    Project,
    Registration,
    StatisticsEvent,
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
from games.social.models import SocialQueuePost


admin.site.register([CheckerType, HTMLPage, Like, Image, Audio, Project, Registration])


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


@admin.register(TicketRequest)
class TicketRequestAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'team', 'tickets', 'money', 'currency', 'payment_provider', 'merchant',
        'status', 'time', 'checkout_goal_acked_at', 'purchase_goal_sent_at',
    )
    list_filter = ('status', 'currency', 'payment_provider', 'merchant')
    search_fields = ('id', 'team__name', 'team__visible_name', 'yookassa_id', 'nowpayments_id', 'tribute_id')
    readonly_fields = ('time', 'checkout_goal_acked_at', 'purchase_goal_sent_at')


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'status',
        'amount_rub',
        'pay_amount',
        'pay_currency',
        'user',
        'created_at',
        'confirmed_at',
    )
    list_filter = ('status', 'pay_currency')
    search_fields = ('public_token', 'nowpayments_id', 'pay_amount')
    readonly_fields = ('public_token', 'created_at', 'confirmed_at')
    raw_id_fields = ('user',)

    def changelist_view(self, request, extra_context=None):
        from games.donation_service import reject_stale_pending_donations

        reject_stale_pending_donations()
        return super().changelist_view(request, extra_context=extra_context)


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
    list_display = [
        'visible_name', 'project', 'ticket_price', 'ticket_price_amd',
        'get_n_users_on', 'get_n_users_requested', 'is_tester', 'is_hidden',
    ]


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
    readonly_fields = ['week_task_source_link']

    def week_task_source_link(self, obj):
        from django.utils.html import format_html
        from games.week_task_pool import source_play_path_from_tags, source_summary_from_tags

        if obj is None:
            return '—'
        src = source_summary_from_tags(obj.tags or {})
        if not src:
            return '—'
        path = source_play_path_from_tags(obj.tags or {})
        label = src.get('desyatka_label') or src.get('game_id') or 'источник'
        major = src.get('major')
        if major is not None:
            label = '{} · п.{}'.format(label, major)
        if not path:
            return label
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a>',
            path,
            label,
        )

    week_task_source_link.short_description = 'Источник в десяточке'


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


class WordSaladTaskForm(ModelForm):
    word_salad_grid_text = forms.CharField(
        required=False,
        label='Word Salad grid',
        widget=Textarea(attrs={'rows': 4, 'cols': 20}),
        help_text='4 строки по 4 буквы или 16 букв подряд. Ё приравнивается к Е.',
    )
    word_salad_words_text = forms.CharField(
        required=False,
        label='Word Salad words',
        widget=Textarea(attrs={'rows': 8, 'cols': 40}),
        help_text='Одно слово на строку. Слова будут сохраняться в JSON автоматически.',
    )

    class Meta:
        model = Task
        fields = '__all__'

    class Media:
        js = ('js/admin_word_salad_task.js',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        task_type = None
        if self.is_bound:
            task_type = self.data.get(self.add_prefix('task_type'))
        if not task_type:
            task_type = self.initial.get('task_type') or getattr(self.instance, 'task_type', None)
        if getattr(self.instance, 'task_type', None) == 'word_salad' and getattr(self.instance, 'checker_data', None):
            try:
                from games.word_salad import format_grid_text, format_words_text, parse_task_data

                grid, words = parse_task_data(self.instance.checker_data, self.instance.answer)
                self.fields['word_salad_grid_text'].initial = format_grid_text(grid)
                self.fields['word_salad_words_text'].initial = format_words_text(words)
            except Exception:
                pass
        if task_type == 'word_salad':
            self.fields['checker_data'].help_text = (
                'Word Salad: можно редактировать через отдельные поля ниже; '
                'JSON соберётся автоматически. Проверка требует grid и words.'
            )
        elif task_type == 'grid-puzzle':
            self.fields['checker_data'].help_text = (
                'Grid Puzzle JSON: {"version":1,"rows":5,"cols":6,'
                '"marks":[{"row":0,"col":1,"value":"arrow-up"}],'
                '"can_set_walls":true,"can_set_path":true,"can_set_shading":false,'
                '"solution_walls":["h:1:0","v:0:2"]}. Для grid-shading-checker: '
                'явно задайте "can_set_walls":false, "can_set_path":false, '
                '"can_set_shading":true и "solution_shading":["BBGG", ...]. '
                'B = чёрный, G = светло-зелёный; координаты с нуля.'
            )

    def clean(self):
        cleaned = super().clean()
        task_type = cleaned.get('task_type') or getattr(self.instance, 'task_type', None)
        if task_type == 'word_salad':
            from games.word_salad import serialize_task_data

            try:
                cleaned['checker_data'] = serialize_task_data(
                    cleaned.get('word_salad_grid_text'),
                    cleaned.get('word_salad_words_text'),
                )
            except ValueError as exc:
                raise forms.ValidationError(str(exc))
            cleaned['answer'] = ''
        elif task_type == 'grid-puzzle':
            from games.grid_puzzle import (
                GridPuzzleDataError,
                parse_grid_puzzle_data,
                validate_grid_checker_data,
            )

            try:
                parsed = parse_grid_puzzle_data(cleaned.get('checker_data'))
                checker = cleaned.get('checker')
                checker_id = getattr(checker, 'pk', None)
                if checker_id:
                    validate_grid_checker_data(parsed, checker_id)
            except GridPuzzleDataError as exc:
                self.add_error('checker_data', str(exc))
        return cleaned


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ['id', '__str__', 'task_group', 'number', 'is_removed']
    list_filter = ['is_removed']
    inlines = [
        HintInline
    ]
    form = WordSaladTaskForm
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }
    def get_form(self, request, obj=None, **kwargs):
        # just save obj reference for future processing in Inline
        request._obj_ = obj
        form = super(TaskAdmin, self).get_form(request, obj, **kwargs)
        if 'checker_data' in form.base_fields:
            form.base_fields['checker_data'].help_text = (
                'Для Word Salad: JSON вида {"grid": ["Д", ... 16 букв ...], "words": ["ВОЛГА", ...]}. '
                'Ё приравнивается к Е. Для других типов это поле остаётся служебным.'
            )
        return form

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
    list_display = ['__str__', 'team_on', 'team_requested', 'telegram_handle', 'vk_url']
    actions = [confirm_profile_team_request, clear_profile_team]


@admin.register(PendingTicketRequest)
class PendingTicketRequestAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }

    def get_queryset(self, request):
        qs = super(PendingTicketRequestAdmin, self).get_queryset(request)
        return qs.filter(status='Pending')

    list_display = [
        '__str__', 'team', 'tickets', 'money', 'currency', 'payment_provider', 'merchant', 'status', 'time',
    ]
    list_filter = ('currency', 'payment_provider', 'merchant')
    actions = [confirm_ticket_request, reject_ticket_request]


def mark_bug_report_reviewed(modeladmin, request, queryset):
    queryset.update(status='Reviewed')


def mark_bug_report_dismissed(modeladmin, request, queryset):
    queryset.update(status='Dismissed')


mark_bug_report_reviewed.short_description = 'Mark Reviewed'
mark_bug_report_dismissed.short_description = 'Mark Dismissed'


def approve_alphabetty_dict_suggestions(modeladmin, request, queryset):
    from games.alphabetty.suggestions import approve_suggestions

    n = approve_suggestions(queryset)
    modeladmin.message_user(
        request,
        'Одобрено слов: {}'.format(n),
        level=messages.SUCCESS,
    )


def approve_alphabetty_dict_suggestions_for_answer(modeladmin, request, queryset):
    from games.alphabetty.suggestions import approve_suggestions_for_answer

    n = approve_suggestions_for_answer(queryset)
    modeladmin.message_user(
        request,
        'Одобрено для загадывания: {}'.format(n),
        level=messages.SUCCESS,
    )


def reject_alphabetty_dict_suggestions(modeladmin, request, queryset):
    from games.alphabetty.suggestions import reject_suggestions

    n = reject_suggestions(queryset)
    modeladmin.message_user(
        request,
        'Отклонено слов: {}'.format(n),
        level=messages.SUCCESS,
    )


approve_alphabetty_dict_suggestions.short_description = 'Одобрить (добавить в словарь)'
approve_alphabetty_dict_suggestions_for_answer.short_description = 'Одобрить для загадывания'
reject_alphabetty_dict_suggestions.short_description = 'Отклонить'


class AlphabettyDictSuggestionAdminBase(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 60})},
    }
    raw_id_fields = ['user']
    readonly_fields = [
        'word', 'suggest_count', 'user', 'anon_key', 'created_at', 'updated_at', 'reviewed_at',
    ]
    fields = [
        'status',
        'word',
        'suggest_count',
        'admin_notes',
        'user',
        'anon_key',
        'created_at',
        'updated_at',
        'reviewed_at',
    ]
    list_display = ['word', 'status', 'suggest_count', 'user', 'updated_at', 'created_at']
    list_filter = ['status']
    search_fields = ['word', 'admin_notes', 'anon_key']
    actions = [
        approve_alphabetty_dict_suggestions,
        approve_alphabetty_dict_suggestions_for_answer,
        reject_alphabetty_dict_suggestions,
    ]
    ordering = ['-updated_at']

    def save_model(self, request, obj, form, change):
        from django.utils import timezone
        from games.alphabetty.dicts import invalidate_dict_caches

        if change and 'status' in form.changed_data:
            if obj.status in (
                AlphabettyDictSuggestion.STATUS_APPROVED,
                AlphabettyDictSuggestion.STATUS_APPROVED_ANSWER,
                AlphabettyDictSuggestion.STATUS_REJECTED,
            ):
                obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)
        invalidate_dict_caches()


@admin.register(AlphabettyDictSuggestion)
class AlphabettyDictSuggestionAdmin(AlphabettyDictSuggestionAdminBase):
    pass


@admin.register(PendingAlphabettyDictSuggestion)
class PendingAlphabettyDictSuggestionAdmin(AlphabettyDictSuggestionAdminBase):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(status=AlphabettyDictSuggestion.STATUS_PENDING)


class BugReportAdminBase(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 4, 'cols': 60})},
    }
    raw_id_fields = ['task', 'game', 'team', 'user']
    readonly_fields = ['time', 'page_url', 'anon_key', 'context_links']
    fields = [
        'status',
        'text',
        'admin_notes',
        'context_links',
        'task',
        'game',
        'team',
        'user',
        'anon_key',
        'page_url',
        'time',
    ]
    list_display = ['__str__', 'game', 'task', 'team', 'user', 'status', 'time']
    list_filter = ['status']
    search_fields = ['text', 'task__number', 'game__id', 'game__name']
    actions = [mark_bug_report_reviewed, mark_bug_report_dismissed]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('task', 'task__task_group', 'game', 'game__project', 'team', 'user')

    def context_links(self, obj):
        from django.utils.html import format_html, format_html_join
        from games.telegram.game_urls import (
            game_admin_url,
            game_site_url,
            task_admin_url,
            task_group_admin_url,
            task_play_url,
        )

        if obj is None or not obj.pk:
            return '—'
        rows = []
        if obj.game_id:
            rows.append(('Игра на сайте', game_site_url(obj.game)))
            rows.append(('Игра в админке', game_admin_url(obj.game)))
        if obj.task_id:
            rows.append(('Задание на сайте', task_play_url(obj.game, obj.task)))
            rows.append(('Task в админке', task_admin_url(obj.task)))
            if obj.task.task_group_id:
                rows.append(('TaskGroup в админке', task_group_admin_url(obj.task.task_group)))
        if obj.page_url:
            rows.append(('page_url', obj.page_url))
        if not rows:
            return '—'
        return format_html(
            '<ul style="margin:0;padding-left:1.2em">{}</ul>',
            format_html_join(
                '',
                '<li><a href="{}" target="_blank" rel="noopener">{}</a></li>',
                ((url, label) for label, url in rows),
            ),
        )

    context_links.short_description = 'Ссылки'


@admin.register(BugReport)
class BugReportAdmin(BugReportAdminBase):
    pass


@admin.register(PendingBugReport)
class PendingBugReportAdmin(BugReportAdminBase):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(status='Pending')


@admin.register(SocialQueuePost)
class SocialQueuePostAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'source',
        'ladder_number',
        'caption_short',
        'telegram_status',
        'twitter_status',
        'instagram_status',
        'telegram_queued_for',
        'twitter_queued_for',
        'instagram_queued_for',
        'telegram_scheduled_for',
        'created_at',
    ]
    list_filter = [
        'source',
        'telegram_status',
        'twitter_status',
        'instagram_status',
    ]
    search_fields = ['caption', 'ladder_number', 'play_url', 'telegram_external_id', 'twitter_external_id']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'created_at'

    def caption_short(self, obj):
        text = (obj.caption or '').replace('\n', ' ')
        return text[:60] + ('…' if len(text) > 60 else '')

    caption_short.short_description = 'caption'


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
