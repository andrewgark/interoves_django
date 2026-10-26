import uuid
import re

from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models import F
from django.db.models.functions import Coalesce
from django.utils import timezone
from games.club.access.game import get_game_access
from games.wall import Wall

class Project(models.Model):
    id = models.CharField(primary_key=True, max_length=100)

    background = models.ImageField(null=True, blank=True)
    logo = models.ImageField(null=True, blank=True)

    def __str__(self):
        return self.id
    
    def is_main(self):
        return self.id == 'main'

    def get_url(self):
        if self.is_main():
            return '/'
        return '/{}'.format(self.id)


class HTMLPage(models.Model):
    name = models.CharField(primary_key=True, max_length=100)
    html = models.TextField(null=True, blank=True)

    def __str__(self):
        return self.name


class CheckerType(models.Model):
    id = models.CharField(primary_key=True, max_length=100)

    def __str__(self):
        return self.id


class Game(models.Model):
    # These section games are daily puzzles, never tournament rounds.  Keep
    # this invariant at the model boundary because ``is_tournament`` defaults
    # to True for the legacy Tenfold games and admin/import code may omit it.
    DAILY_NON_TOURNAMENT_GAME_IDS = frozenset({'ladder', 'alphabetty', 'salad', 'censorly'})

    id = models.CharField(primary_key=True, max_length=100)
    name = models.TextField()
    outside_name = models.TextField(null=True, blank=True)
    no_html_name = models.TextField(null=True, blank=True)
    image = models.ImageField(null=True, blank=True)
    theme = models.CharField(max_length=100, null=True, blank=True)
    author = models.CharField(max_length=100)
    author_extra = models.CharField(max_length=500, null=True, blank=True)

    start_time = models.DateTimeField(default=timezone.now, blank=True)
    end_time = models.DateTimeField(default=timezone.now, blank=True)
    visible_start_time = models.DateTimeField(null=True, blank=True)
    visible_end_time = models.DateTimeField(null=True, blank=True)

    project = models.ForeignKey(
        Project, related_name='games',
        default='main', on_delete=models.CASCADE
    )

    is_ready = models.BooleanField(default=False)
    is_testing = models.BooleanField(default=False)
    is_registrable = models.BooleanField(default=True)
    requires_ticket = models.BooleanField(default=True)
    is_playable = models.BooleanField(default=True)
    is_tournament = models.BooleanField(default=True)
    is_18_plus = models.BooleanField(
        default=False,
        verbose_name='18+',
        help_text='Контент для лиц старше 18 лет: при открытии игры показывается подтверждение возраста.',
    )

    game_url = models.CharField(max_length=500, null=True, blank=True)
    answers_url = models.CharField(max_length=500, null=True, blank=True)
    standings_url = models.CharField(max_length=500, null=True, blank=True)

    rules = models.ForeignKey(
        HTMLPage, to_field='name', related_name='games',
        blank=True, null=True, on_delete=models.SET_NULL,
        default="Правила Десяточки"
    )

    tournament_rules = models.ForeignKey(
        HTMLPage, to_field='name', related_name='games_tournament',
        blank=True, null=True, on_delete=models.SET_NULL,
        default="Правила турнирного режима"
    )
    general_rules = models.ForeignKey(
        HTMLPage, to_field='name', related_name='games_general',
        blank=True, null=True, on_delete=models.SET_NULL,
        default="Правила тренировочного режима"
    )

    note = models.ForeignKey(
        HTMLPage, to_field='name', related_name='games_note',
        blank=True, null=True, on_delete=models.SET_NULL
    )

    # Модалка «Правила» на странице раздела (project sections): один HTMLPage, без склейки по TaskGroup.
    section_default_rules = models.ForeignKey(
        HTMLPage, to_field='name', related_name='games_section_default_rules',
        blank=True, null=True, on_delete=models.SET_NULL,
    )

    results = models.TextField(null=True, blank=True)
    tags = models.JSONField(default=dict, null=True, blank=True)

    def get_outside_name(self):
        if self.outside_name:
            return self.outside_name
        return self.name

    def get_no_html_name(self):
        if self.no_html_name:
            return self.no_html_name
        return self.name

    def __str__(self):
        return self.get_no_html_name()

    def has_access(self, action, team=None, attempt=None, mode='general'):
        return get_game_access(game=self, action=action, team=team, attempt=attempt, mode=mode)

    def get_current_mode(self, attempt=None):
        if self.has_access(action='attempt_is_tournament', attempt=attempt):
            return 'tournament'
        return 'general'

    def save(self, *args, **kwargs):
        if self.pk in self.DAILY_NON_TOURNAMENT_GAME_IDS:
            self.is_tournament = False
        return super().save(*args, **kwargs)

    def has_registered(self, team):
        if team is None:
            return False
        return self.registrations.filter(team_id=team.pk).exists()

    def get_visible_start_time(self):
        return self.visible_start_time if self.visible_start_time is not None else self.start_time

    def get_visible_end_time(self):
        return self.visible_end_time if self.visible_end_time is not None else self.end_time


class GameAuthor(models.Model):
    """Hidden access grants for a game, owned by a person or a team."""

    game = models.ForeignKey(Game, related_name='authors', on_delete=models.CASCADE)
    profile = models.ForeignKey(
        'Profile', related_name='authored_games', blank=True, null=True,
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        'Team', related_name='authored_games', blank=True, null=True,
        on_delete=models.CASCADE,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(profile__isnull=False, team__isnull=True)
                    | models.Q(profile__isnull=True, team__isnull=False)
                ),
                name='games_gameauthor_exactly_one_actor',
            ),
            models.UniqueConstraint(
                fields=('game', 'profile'), name='games_gameauthor_game_profile_uniq',
            ),
            models.UniqueConstraint(
                fields=('game', 'team'), name='games_gameauthor_game_team_uniq',
            ),
        ]

    def __str__(self):
        return str(self.profile or self.team or self.game)


class GameResultsSnapshot(models.Model):
    """
    Frozen results table for a game.

    Stores computed results (including hint penalties) so later task/checker changes
    do not affect already finished tournaments.
    """
    game = models.ForeignKey(Game, related_name='results_snapshots', on_delete=models.CASCADE)
    mode = models.CharField(max_length=32, default='tournament')  # 'general' | 'tournament'
    created_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField(default=dict)

    class Meta:
        unique_together = (('game', 'mode'),)

    def __str__(self):
        return f'{self.game_id} [{self.mode}] @ {self.created_at:%Y-%m-%d %H:%M:%S}'


class TaskGroup(models.Model):
    """
    Канонический набор заданий (задачи, чекер, вид). Привязка к играм с номером/названием — GameTaskGroup.
    """
    id = models.AutoField(primary_key=True)
    label = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Подпись (админка)',
        help_text='Для списков в админке; игрокам не показывается.',
    )
    rules = models.ForeignKey(HTMLPage, to_field='name', related_name='task_groups', blank=True, null=True, on_delete=models.SET_NULL)
    text = models.TextField(null=True, blank=True)

    checker = models.ForeignKey(
        CheckerType, related_name='task_groups', blank=True, null=True, on_delete=models.SET_NULL,
        default='equals_with_possible_spaces',
    )
    points = models.DecimalField(default=1, decimal_places=3, max_digits=10, blank=True, null=True)
    max_attempts = models.IntegerField(default=3, blank=True, null=True)
    image_width = models.IntegerField(default=300, null=True, blank=True)
    tags = models.JSONField(default=dict, null=True, blank=True)
    authors = models.ManyToManyField(
        'Profile', related_name='authored_task_groups', blank=True,
        verbose_name='Авторы задания',
        help_text='Зарегистрированные авторы для исключения из таблицы результатов этого выпуска.',
    )

    VIEW_VARIANTS = (
        ('default', 'default'),
        ('table-3-n', 'table-3-n'),
        ('table-4-n', 'table-4-n'),
        ('proportions', 'Пропорции'),
    )

    view = models.CharField(default='default', max_length=100, choices=VIEW_VARIANTS)
    is_18_plus = models.BooleanField(
        default=False,
        verbose_name='18+',
        help_text='Контент для лиц старше 18 лет: при открытии набора заданий показывается подтверждение возраста.',
    )

    def get_derived_title(self):
        """
        Подпись как у старого TaskGroup: «[игра] N. название» по самой ранней по времени игре
        среди вхождений (visible_start_time или start_time, затем id игры и номер круга).
        """
        link = (
            GameTaskGroup.objects.filter(task_group_id=self.pk)
            .select_related('game')
            .annotate(
                _eg_sort=Coalesce(
                    F('game__visible_start_time'),
                    F('game__start_time'),
                )
            )
            .order_by('_eg_sort', 'game_id', 'number', 'pk')
            .first()
        )
        if link is None:
            return None
        return '[{}] {}. {}'.format(
            link.game.get_no_html_name(),
            link.number,
            link.name,
        ).strip()

    def __str__(self):
        if self.label:
            return self.label
        derived = self.get_derived_title()
        if derived:
            return derived
        return 'Набор заданий #{}'.format(self.pk)

    def get_li_class(self):
        if self.view == 'table-3-n':
            return 'table-3-n-cell'
        if self.view == 'table-4-n':
            return 'table-4-n-cell'
        return ''

    def get_n_tasks(self):
        return len(self.tasks.visible())

    def get_n_tasks_for_results(self):
        return len(self.tasks.visible().filter(~models.Q(task_type='text_with_forms')))


GAME_TASK_GROUP_NUMBER_VALIDATOR = RegexValidator(
    regex=r'^\d+(?:\.\d+)?$',
    message='Номер круга: целое число или число с точкой (например 1 или 1.5).',
)


class GameTaskGroup(models.Model):
    """Вхождение набора заданий в игру: свой номер и название для каждой игры."""

    id = models.AutoField(primary_key=True)
    game = models.ForeignKey(Game, related_name='task_group_links', on_delete=models.CASCADE)
    task_group = models.ForeignKey(TaskGroup, related_name='game_links', on_delete=models.CASCADE)
    number = models.CharField(max_length=20, validators=[GAME_TASK_GROUP_NUMBER_VALIDATOR])
    name = models.CharField(max_length=100)
    share_hash = models.CharField(max_length=32, unique=True, null=True, blank=True)
    is_deferred = models.BooleanField(
        default=False,
        help_text='Временно убрано из расписания support.',
    )
    deferred_number = models.CharField(max_length=20, blank=True, default='')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['game', 'task_group'],
                name='unique_gametaskgroup_game_taskgroup',
            ),
            models.UniqueConstraint(
                fields=['game', 'number'],
                name='unique_gametaskgroup_game_number',
            ),
        ]
        ordering = ['number']

    def __str__(self):
        return '[{}] {}. {}'.format(self.game_id, self.number, self.name)

    def save(self, *args, **kwargs):
        if not self.share_hash and self.game_id in ('ladder', 'salad', 'alphabetty', 'week_task'):
            from games.sections.placement_share import allocate_share_hash
            self.share_hash = allocate_share_hash()
            update_fields = kwargs.get('update_fields')
            if update_fields is not None:
                kwargs['update_fields'] = set(update_fields) | {'share_hash'}
        super().save(*args, **kwargs)

    @staticmethod
    def number_key(number):
        return tuple(int(x) for x in str(number).split('.'))

    @classmethod
    def try_number_key(cls, number):
        """Like number_key, or None if number is not dotted-int (e.g. share hash / reserved path)."""
        try:
            return cls.number_key(number)
        except (TypeError, ValueError):
            return None

    def key_sort(self):
        return self.number_key(self.number)

    @classmethod
    def _schedule_sort_key(cls, link, *, include_deferred):
        if include_deferred and link.is_deferred:
            # Deferred slots have their own queue numbering. Their technical
            # ``number`` is only a temporary unique-index placeholder.
            deferred_key = cls.try_number_key(link.deferred_number)
            return (1, deferred_key if deferred_key is not None else (10**9,), link.pk)
        return (0, link.key_sort(), link.pk)

    @staticmethod
    def _exclude_random_schedule_links(queryset):
        random_hash_q = (
            models.Q(
                game_id='alphabetty',
                task_group__random_alphabetty_game__isnull=False,
            )
            | models.Q(
                game_id='censorly',
                task_group__random_censorly_game__isnull=False,
            )
        )
        return queryset.exclude(random_hash_q)


    @classmethod
    def sorted_links(cls, queryset=None, *, game=None, reverse=False, include_deferred=False):
        if queryset is None:
            queryset = cls.objects.filter(game=game)
        # Permanent random Alphabetty and Цензурка games use a hash as
        # ``number`` and do not belong in numeric schedule/navigation lists.
        if hasattr(queryset, 'exclude'):
            if not include_deferred:
                queryset = queryset.filter(is_deferred=False)
            queryset = cls._exclude_random_schedule_links(queryset)
        else:
            links = list(queryset)
            if not include_deferred:
                links = [link for link in links if not link.is_deferred]
            task_group_ids = [link.task_group_id for link in links]
            random_task_group_ids = set()
            if task_group_ids:
                from games.models import RandomAlphabettyGame, RandomCensorlyGame

                random_task_group_ids = set(
                    RandomAlphabettyGame.objects.filter(
                        task_group_id__in=task_group_ids,
                    ).values_list('task_group_id', flat=True)
                )
                random_task_group_ids.update(
                    RandomCensorlyGame.objects.filter(
                        task_group_id__in=task_group_ids,
                    ).values_list('task_group_id', flat=True)
                )
            queryset = [
                link for link in links
                if not (
                    link.game_id in ('alphabetty', 'censorly')
                    and link.task_group_id in random_task_group_ids
                )
            ]
        links = sorted(
            queryset,
            key=lambda link: cls._schedule_sort_key(
                link,
                include_deferred=include_deferred,
            ),
            reverse=reverse,
        )
        return links

    @classmethod
    def order_queryset_by_number(cls, queryset, *, reverse=False, include_deferred=False):
        """Числовая сортировка номера круга (1, 2, …, 10), не лексикографическая."""
        queryset = cls._exclude_random_schedule_links(queryset)
        if not include_deferred:
            queryset = queryset.filter(is_deferred=False)
        links = list(queryset)
        if not links:
            return queryset.none()
        links.sort(
            key=lambda link: cls._schedule_sort_key(
                link,
                include_deferred=include_deferred,
            ),
            reverse=reverse,
        )
        ordered_pks = [link.pk for link in links]
        preserved = models.Case(
            *[models.When(pk=pk, then=pos) for pos, pk in enumerate(ordered_pks)],
            output_field=models.IntegerField(),
        )
        return queryset.filter(pk__in=ordered_pks).order_by(preserved)

    @classmethod
    def get_by_number(cls, game, number):
        return cls.objects.filter(game=game, number=str(number)).first()

    @classmethod
    def nearest_by_number(cls, game, number):
        """Ближайший круг по порядку, если точного номера нет."""
        ordered = cls.sorted_links(game=game)
        if not ordered:
            return None
        key = cls.try_number_key(number)
        if key is None:
            return None
        placement = next((p for p in ordered if p.number == str(number)), None)
        if placement:
            return placement
        after = [p for p in ordered if p.key_sort() > key]
        before = [p for p in ordered if p.key_sort() < key]
        return (after[0] if after else None) or (before[-1] if before else None)

    @classmethod
    def prev_next_for(cls, game, placement):
        ordered = cls.sorted_links(game=game)
        try:
            idx = ordered.index(placement)
        except ValueError:
            return None, None
        prev_link = ordered[idx - 1] if idx > 0 else None
        next_link = ordered[idx + 1] if idx + 1 < len(ordered) else None
        return prev_link, next_link

    @staticmethod
    def resolve_game_for_task(task, game_id=None):
        """
        Игра в контексте задания: явный game_id, иначе единственная привязанная игра.

        Для черновиков предложений лесенок/салатиков GameTaskGroup ещё нет —
        если task_group связан с LadderOffer или WordSaladOffer, вернуть
        соответствующую игру. Учебная Лесенка (games/tutorials) играется так же.
        """
        if task is None or task.task_group_id is None:
            return None
        qs = GameTaskGroup.objects.filter(task_group_id=task.task_group_id).select_related('game')
        if game_id:
            row = qs.filter(game_id=game_id).first()
            if row is not None:
                return row.game
        elif qs.count() == 1:
            return qs.first().game
        elif qs.exists():
            return None

        # Нет (нужной) привязки в расписании — черновик LadderOffer / WordSaladOffer?
        from games.daily.catalog.ladder import LADDER_GAME_ID
        from games.puzzles.salad.rules import WORD_SALAD_GAME_ID
        from games.models import Game, LadderOffer, WordSaladOffer
        from games.tutorials.ladder import is_ladder_tutorial_task_group

        is_ladder_draft = (
            is_ladder_tutorial_task_group(task.task_group)
            or LadderOffer.objects.filter(task_group_id=task.task_group_id).exists()
        )
        if game_id:
            gid = str(game_id)
            if gid == LADDER_GAME_ID:
                if is_ladder_draft:
                    return Game.objects.filter(pk=LADDER_GAME_ID).first()
                return None
            if gid == WORD_SALAD_GAME_ID:
                if WordSaladOffer.objects.filter(task_group_id=task.task_group_id).exists():
                    return Game.objects.filter(pk=WORD_SALAD_GAME_ID).first()
                return None
            return None
        if is_ladder_draft:
            return Game.objects.filter(pk=LADDER_GAME_ID).first()
        if WordSaladOffer.objects.filter(task_group_id=task.task_group_id).exists():
            return Game.objects.filter(pk=WORD_SALAD_GAME_ID).first()
        return None


class TaskQuerySet(models.QuerySet):
    """Задания с is_removed=True скрыты из игры и результатов, в админке видны все."""

    def visible(self):
        return self.filter(is_removed=False)


class Task(models.Model):
    id = models.AutoField(primary_key=True)
    # Changes on every Task.save(). Attempts keep a snapshot so an answer that
    # was submitted against an older version of the task is not a duplicate of
    # the same answer submitted after an edit.
    attempt_revision = models.UUIDField(default=uuid.uuid4, editable=False)
    task_group = models.ForeignKey(TaskGroup, related_name='tasks', blank=True, null=True, on_delete=models.SET_NULL)
    number = models.CharField(max_length=100)
    image = models.ImageField(null=True, blank=True)
    text = models.TextField(null=True, blank=True)
    checker_data = models.TextField(null=True, blank=True)
    answer = models.TextField(null=True, blank=True)
    answer_comment = models.TextField(null=True, blank=True)

    TASK_TYPE_VARIANTS = (
        ('default', 'default'),
        ('wall', 'wall'),
        ('text_with_forms', 'text_with_forms'),
        ('html_forms', 'html_forms'),
        ('replacements_lines', 'replacements_lines'),
        ('distribute_to_teams', 'distribute_to_teams'),
        ('with_tag', 'with_tag'),
        ('autohint', 'autohint'),
        ('proportions', 'Пропорции'),
        ('raddle', 'raddle'),
        ('alphabetty', 'alphabetty'),
        ('word_salad', 'Салатик'),
        ('censorly', 'Цензурка'),
        ('grid-puzzle', 'Grid Puzzle'),
    )

    task_type = models.CharField(default='default', max_length=100, choices=TASK_TYPE_VARIANTS)
    # Persisted because parsing replacements text is relatively expensive and
    # the value is part of the task definition, not of a result request.
    replacements_answer_rows = models.PositiveIntegerField(
        blank=True, null=True, editable=False,
    )

    checker = models.ForeignKey(
        CheckerType, related_name='tasks', blank=True, null=True, on_delete=models.SET_NULL,
        default='equals_with_possible_spaces',
    )
    points = models.DecimalField(decimal_places=3, max_digits=10, blank=True, null=True)
    max_attempts = models.IntegerField(blank=True, null=True)
    image_width = models.IntegerField(null=True, blank=True)
    field_text_width = models.IntegerField(null=True, blank=True)
    tags = models.JSONField(default=dict, null=True, blank=True)
    is_removed = models.BooleanField(
        default=False,
        verbose_name='Скрыто из игры и результатов',
        help_text='Не удаляет задание из БД; скрывает везде, кроме админки.',
    )

    objects = TaskQuerySet.as_manager()

    def __str__(self):
        game_name = 'NONE'
        g = GameTaskGroup.resolve_game_for_task(self)
        if g is not None:
            game_name = g.name
        tg_label = 'NONE'
        if self.task_group is not None:
            tg_label = (
                self.task_group.label
                or self.task_group.get_derived_title()
                or str(self.task_group_id)
            )
        return '{}: {}.{}'.format(game_name, tg_label, self.number)

    def save(self, *args, **kwargs):
        skip_semantics_reconciliation = kwargs.pop('skip_semantics_reconciliation', False)
        from games.realtime.track_compat import track_task_change
        from games.puzzles import get_puzzle
        puzzle = get_puzzle(self.task_type)
        assign_rows = getattr(puzzle, 'assign_answer_rows', None) if puzzle is not None else None
        if assign_rows is not None:
            assign_rows(self)
        else:
            self.replacements_answer_rows = None
        is_existing = not self._state.adding
        previous_semantics = None
        old_actor_keys = set()
        old_game_ids = set()
        capture = getattr(puzzle, 'capture_before_save', None) if puzzle is not None else None
        previous_puzzle = capture(self) if capture is not None and is_existing else None
        if is_existing:
            previous_semantics = type(self).objects.filter(pk=self.pk).values(
                'task_group_id', 'is_removed', 'task_type', 'checker_id',
                'checker_data', 'answer', 'text', 'points', 'max_attempts',
            ).first()
            if previous_semantics:
                from games.daily.completion.targeted_completion_reconciliation import _actor_keys_for_group
                old_group_id = previous_semantics['task_group_id']
                if old_group_id:
                    old_game_ids = set(
                        GameTaskGroup.objects.filter(task_group_id=old_group_id)
                        .values_list('game_id', flat=True)
                    )
                    for old_game_id in old_game_ids:
                        old_actor_keys.update(_actor_keys_for_group(
                            game_id=old_game_id,
                            task_group_id=old_group_id,
                        ))
        if not self._state.adding:
            self.attempt_revision = uuid.uuid4()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields) | {'replacements_answer_rows'}
            if not self._state.adding:
                update_fields.add('attempt_revision')
            kwargs['update_fields'] = update_fields
        super(Task, self).save(*args, **kwargs)
        projection_fields = (
            'task_group_id', 'is_removed', 'task_type', 'checker_id',
            'checker_data', 'answer', 'text', 'points', 'max_attempts',
        )
        projection_needs_refresh = not is_existing or bool(
            previous_semantics and any(
                previous_semantics[field] != getattr(self, field)
                for field in projection_fields
            )
        )
        if projection_needs_refresh:
            projection_groups = {self.task_group_id}
            if previous_semantics:
                projection_groups.add(previous_semantics['task_group_id'])
            from games.daily.projection import mark_projection_dirty
            for group_id in projection_groups:
                if not group_id:
                    continue
                for link in GameTaskGroup.objects.filter(
                    task_group_id=group_id, game__project_id='sections',
                ).select_related('game', 'task_group'):
                    transaction.on_commit(
                        lambda game=link.game, task_group=link.task_group:
                        mark_projection_dirty(game, task_group, full=True),
                    )
        after = getattr(puzzle, 'after_save', None) if puzzle is not None else None
        if after is not None:
            after(self, is_existing=is_existing, previous=previous_puzzle)
        if is_existing and previous_semantics:
            changed_fields = {
                field for field in (
                    'task_group_id', 'is_removed', 'task_type', 'checker_id',
                    'checker_data', 'answer', 'text', 'points', 'max_attempts',
                ) if previous_semantics[field] != getattr(self, field)
            }
            if changed_fields and not skip_semantics_reconciliation:
                from games.daily.completion.targeted_completion_reconciliation import (
                    schedule_task_semantics_reconciliation,
                )
                new_game_ids = set(
                    GameTaskGroup.objects.filter(task_group_id=self.task_group_id)
                    .values_list('game_id', flat=True)
                ) if self.task_group_id else set()
                rebuild_task_id = self.pk if changed_fields.intersection({
                    'task_group_id', 'task_type', 'checker_id', 'checker_data',
                    'answer', 'text', 'points', 'max_attempts',
                }) else None
                schedule_task_semantics_reconciliation(
                    old_group_id=previous_semantics['task_group_id'],
                    new_group_id=self.task_group_id,
                    game_ids=old_game_ids | new_game_ids,
                    actor_keys=old_actor_keys,
                    rebuild_task_id=rebuild_task_id,
                )
        elif not is_existing and self.task_group_id:
            from games.daily.completion.targeted_completion_reconciliation import (
                _actor_keys_for_group,
                schedule_task_semantics_reconciliation,
            )
            new_game_ids = set(
                GameTaskGroup.objects.filter(task_group_id=self.task_group_id)
                .values_list('game_id', flat=True)
            )
            actor_keys = set()
            for game_id in new_game_ids:
                actor_keys.update(_actor_keys_for_group(
                    game_id=game_id,
                    task_group_id=self.task_group_id,
                ))
            schedule_task_semantics_reconciliation(
                old_group_id=None,
                new_group_id=self.task_group_id,
                game_ids=new_game_ids,
                actor_keys=actor_keys,
            )
        track_task_change(self)

    def clean(self):
        from games.puzzles import get_puzzle
        puzzle = get_puzzle(self.task_type)
        if puzzle is None and self.task_type == 'grid-puzzle':
            from games.puzzles.replacements.grid import GridPuzzle
            puzzle = GridPuzzle
        cleaner = getattr(puzzle, 'clean_checker_data', None) if puzzle is not None else None
        if cleaner is not None:
            cleaner(self)
            return

    def get_checker(self):
        if self.checker:
            return self.checker
        if self.task_group.checker:
            return self.task_group.checker
        raise Exception('Task has empty checker')

    def get_points(self):
        if self.points:
            return self.points
        if self.task_group.points:
            return self.task_group.points
        return 0

    def _replacements_lines_n_answer_rows(self):
        """Число строк ответа (как в ReplacementsLinesChecker._resolve_answer_rows)."""
        from games.puzzles.replacements.lines import (
            parse_replacements_checker_json_lines,
            parse_replacements_lines_text,
        )

        raw = (self.checker_data or '').strip()
        if raw:
            parsed = parse_replacements_checker_json_lines(raw)
            if parsed:
                canonical_rows, _ = parsed
                return len(canonical_rows)
        parsed_text = parse_replacements_lines_text(self.text or '', raw or None)
        return len(parsed_text.get('left_lines') or [])

    def get_results_max_points(self):
        """
        Максимум баллов для сравнения в таблице результатов (зелёная ячейка = набрано не меньше этого).
        Для стены и замен совпадает с тем, как после check_attempt копятся attempt.points
        (внутренний разбал × множитель задания; для замен — строки × множитель).
        """
        mul = self.get_points()
        try:
            m = float(mul)
        except (TypeError, ValueError):
            m = 0.0
        from games.puzzles import get_puzzle
        puzzle = get_puzzle(self.task_type)
        score = getattr(puzzle, 'results_max_points', None) if puzzle is not None else None
        if score is not None:
            return score(self, m)
        return m

    def get_max_attempts(self):
        if self.max_attempts:
            return self.max_attempts
        if self.task_group.max_attempts:
            return self.task_group.max_attempts
        raise Exception('Task has no max_attempts')

    def get_image_width(self):
        if self.image_width:
            return self.image_width
        return self.task_group.image_width

    def key_sort(self):
        if 'Г' in str(self.number): # for crossword
            key = re.sub(r"Г", "", self.number)
            return tuple([0] + [int(x) for x in key.split('.')])
        if 'В' in str(self.number): # for crossword
            key = re.sub(r"В", "", self.number)
            return tuple([1] + [int(x) for x in key.split('.')])
        number = re.sub(r"\*", "", self.number)
        try:
            return tuple([int(x) for x in number.split('.')])
        except:
            return number

    def get_wall(self):
        return Wall(self)

    def get_attempt_form(self, *args, **kwargs):
        from games.forms import AttemptForm
        kwargs['field_text_width'] = self.field_text_width
        return AttemptForm(*args, **kwargs)
    
    def get_hints(self):
        return list(self.hints.all())

    def get_player_hints(self):
        """Подсказки для панели игрока. Пазл может скрыть свои служебные строки."""
        hints = self.get_hints()
        from games.puzzles import get_puzzle
        puzzle = get_puzzle(self.task_type)
        visible = getattr(puzzle, 'player_hints', None) if puzzle is not None else None
        if visible is not None:
            return visible(hints)
        return hints


class ImageManager(models.Manager):
    def get_image(self, id):
        img = Image.objects.filter(id=id)[0]
        return img


class Image(models.Model):
    id = models.CharField(primary_key=True, max_length=100)
    image = models.ImageField(null=True, blank=True)


class AudioManager(models.Manager):
    def get_audio(self, id):
        audio = Audio.objects.filter(id=id)[0]
        return audio


class Audio(models.Model):
    id = models.CharField(primary_key=True, max_length=100)
    audio = models.FileField()
    title = models.TextField(null=True, blank=True)

from games.models.content import RandomAlphabettyGame, RandomCensorlyGame  # noqa: E402
