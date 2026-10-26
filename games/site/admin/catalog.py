from django import forms
from django.contrib import admin, messages
from django.forms import Textarea, ModelForm
from django.db import models
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils.html import format_html
from games.ops.support.google.actions import create_google_doc
from games.models import (
    Audio,
    CheckerType,
    Game,
    GameAuthor,
    GameResultsSnapshot,
    GameTaskGroup,
    Hint,
    HTMLPage,
    Image,
    Project,
    Task,
    TaskGroup,
)
from games.results.snapshots.snapshot import freeze_game_results


admin.site.register([CheckerType, HTMLPage, Image, Audio, Project])


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


class GameAuthorInline(admin.TabularInline):
    model = GameAuthor
    extra = 1
    verbose_name = 'Скрытый автор'
    verbose_name_plural = 'Скрытые авторы (доступ без клубной подписки)'
    raw_id_fields = ['profile', 'team']


@admin.register(TaskGroup)
class TaskGroupAdmin(admin.ModelAdmin):
    inlines = [
        GameTaskGroupInlineOnTaskGroup,
        TaskInline,
    ]
    list_display = ['__str__', 'label', 'is_18_plus']
    search_fields = ['label', 'id']
    readonly_fields = ['week_task_source_link']
    autocomplete_fields = ['authors']

    def week_task_source_link(self, obj):
        from django.utils.html import format_html
        from games.sections.week_task_pool import source_play_path_from_tags, source_summary_from_tags

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
            new_tg.authors.set(old_tg.authors.all())
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


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    inlines = [
        TaskGroupInline,
        GameAuthorInline,
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
    word_salad_rare_words_text = forms.CharField(
        required=False,
        label='Word Salad rare words',
        widget=Textarea(attrs={'rows': 6, 'cols': 40}),
        help_text='Необязательные редкие находки: по теме, но слишком редкие, чтобы требовать их у всех. Очков не дают.',
    )

    class Meta:
        model = Task
        fields = '__all__'

    class Media:
        js = ('js/admin_word_salad_task.js',)
        css = {'all': ('css/admin_word_salad_task.css',)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        task_type = None
        if self.is_bound:
            task_type = self.data.get(self.add_prefix('task_type'))
        if not task_type:
            task_type = self.initial.get('task_type') or getattr(self.instance, 'task_type', None)
        if getattr(self.instance, 'task_type', None) == 'word_salad' and getattr(self.instance, 'checker_data', None):
            try:
                from games.word_salad import format_grid_text, format_words_text, parse_task_payload

                grid, words, rare_words = parse_task_payload(self.instance.checker_data, self.instance.answer)
                self.fields['word_salad_grid_text'].initial = format_grid_text(grid)
                self.fields['word_salad_words_text'].initial = format_words_text(words)
                self.fields['word_salad_rare_words_text'].initial = format_words_text(rare_words)
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
            from games.word_salad import serialize_task_data, validate_puzzle

            try:
                validate_puzzle(
                    cleaned.get('word_salad_grid_text'),
                    cleaned.get('word_salad_words_text'),
                    cleaned.get('word_salad_rare_words_text'),
                )
                cleaned['checker_data'] = serialize_task_data(
                    cleaned.get('word_salad_grid_text'),
                    cleaned.get('word_salad_words_text'),
                    cleaned.get('word_salad_rare_words_text'),
                )
            except ValueError as exc:
                raise forms.ValidationError(str(exc))
            cleaned['answer'] = ''
        elif task_type == 'grid-puzzle':
            from games.puzzles.replacements.grid_puzzle import (
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
    search_fields = ['id', 'number', 'text', 'task_group__label']
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
                'Для HTML-форм: JSON вида {"forms": [{"key": "a", "answer": "ответ"}]}; '
                'в тексте задания вставьте {{ html_form:a }}; можно указать answers вместо answer. '
                'Для Word Salad: JSON с grid и words; Ё приравнивается к Е. '
                'Для других типов поле остаётся служебным.'
            )
        return form

    def save_model(self, request, obj, form, change):
        previous_signature = None
        if obj.task_type == 'word_salad' and obj.pk:
            previous = Task.objects.filter(pk=obj.pk).values('checker_data', 'answer').first()
            if previous:
                from games.word_salad import task_data_signature
                previous_signature = task_data_signature(
                    previous['checker_data'], previous['answer'],
                )
        if obj.task_type == 'raddle':
            from games.raddle import validate_raddle_checker_data
            errors = validate_raddle_checker_data(obj.checker_data, obj.answer)
            if errors:
                for err in errors:
                    messages.error(request, 'Raddle: {}'.format(err))
                return
        super(TaskAdmin, self).save_model(request, obj, form, change)
        if obj.task_type == 'word_salad' and obj.task_group is not None:
            from games.word_salad import task_data_signature
            current_signature = task_data_signature(obj.checker_data, obj.answer)
            if previous_signature != current_signature:
                from games.ops.workers.jobs.salad import enqueue_word_salad_recheck
                for link in obj.task_group.game_links.filter(game_id='salad').select_related('game'):
                    enqueue_word_salad_recheck(
                        task=obj, game=link.game, include_dictionary_scan=True,
                    )
