import hashlib
import secrets
import uuid
import json
import os
import re

from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F
from django.db.models.functions import Coalesce
from django.dispatch import receiver
from django.utils import timezone
from games.access import get_game_access
from games.util import better_status
from games.wall import Wall
from allauth.socialaccount.models import SocialAccount


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


class Team(models.Model):
    name = models.CharField(max_length=100, primary_key=True)
    visible_name = models.TextField(blank=True, null=True)
    name_hash = models.CharField(max_length=256, editable=False, null=True)
    is_tester = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)

    project = models.ForeignKey(
        Project, related_name='teams',
        default='main', on_delete=models.CASCADE
    )

    tickets = models.IntegerField(default=0)
    ticket_price = models.IntegerField(default=2000)

    referer = models.ForeignKey('Team', related_name='referents', blank=True, null=True, on_delete=models.SET_NULL)
    join_password = models.CharField(max_length=32, blank=True, null=True)


    def save_name_hash(self):
        self.name_hash = hashlib.sha512((self.name + 'salt').encode()).hexdigest()[:50]

    def get_name_hash(self):
        self.save_name_hash()
        return str(self.name_hash)

    def save(self, *args, **kwargs):
        if not self.visible_name:
            self.visible_name = self.name
        if not self.join_password:
            # короткий пароль "для своих" — можно менять в /new/team/
            self.join_password = secrets.token_hex(4)  # 8 hex chars
        self.save_name_hash()
        super(Team, self).save(*args, **kwargs)

    def __str__(self):
        return self.visible_name
    
    def get_n_users_on(self):
        return self.member_links.count()

    def get_n_users_requested(self):
        return len(self.users_requested.all())

    @property
    def roster_profiles(self):
        """Все участники команды (многокомандность через ProfileTeamMembership)."""
        return Profile.objects.filter(team_memberships__team=self).select_related('user').order_by('pk')

    def get_team_reg_number(self, game):
        regs = [reg for reg in Registration.objects.filter(game=game) if not reg.team.is_hidden]
        regs.sort(key=lambda r: r.time)
        team_number = None
        for i, reg in enumerate(regs):
            if self == reg.team:
                team_number = i
                break
        return team_number

    # Для шаблона общих результатов (команда vs личный участник)
    is_team_results_row = True


class PersonalResultsParticipant:
    """
    Участник общей таблицы результатов без команды (личный или анонимный режим).
    Не модель БД — только для отображения и ключей в dict.
    """

    __slots__ = ('_user', 'user_id', 'anon_key', '_display_name_override')

    def __init__(self, *, user=None, user_id=None, anon_key=None, display_name=None):
        self._display_name_override = display_name
        if user is not None:
            self._user = user
            self.user_id = user.pk
            self.anon_key = None
            if user_id is not None and int(user_id) != int(user.pk):
                raise ValueError('user_id does not match user.pk')
        elif user_id is not None:
            self._user = None
            self.user_id = int(user_id)
            self.anon_key = None
        elif anon_key is not None and str(anon_key).strip() != '':
            self._user = None
            self.user_id = None
            self.anon_key = str(anon_key)
        else:
            raise ValueError('Pass user=, user_id=, or anon_key=')

    @property
    def pk(self):
        if self.user_id is not None:
            return 'personal:user:{}'.format(self.user_id)
        return 'personal:anon:{}'.format(self.anon_key)

    @property
    def visible_name(self):
        if self._display_name_override:
            return self._display_name_override
        if self._user is not None:
            u = self._user
            try:
                p = u.profile
            except Exception:
                p = None
            if p is not None:
                label = ('{} {}'.format(p.first_name or '', p.last_name or '')).strip()
                if label:
                    return label
            full = (u.get_full_name() or '').strip()
            if full:
                return full
            return u.get_username()
        tail = self.anon_key[-4:] if len(self.anon_key) >= 4 else self.anon_key
        return 'Аноним ··{}'.format(tail)

    is_team_results_row = False

    def __eq__(self, other):
        if not isinstance(other, PersonalResultsParticipant):
            return False
        return self.user_id == other.user_id and self.anon_key == other.anon_key

    def __hash__(self):
        if self.user_id is not None:
            return hash(('PersonalResultsParticipant', 'u', self.user_id))
        return hash(('PersonalResultsParticipant', 'a', self.anon_key))


class ProfileTeamMembership(models.Model):
    """Участие профиля в команде (полный состав). Активная команда для игр — Profile.team_on."""

    profile = models.ForeignKey('Profile', related_name='team_memberships', on_delete=models.CASCADE)
    team = models.ForeignKey(Team, related_name='member_links', on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('profile', 'team'), name='games_profileteammembership_uniq_profile_team'),
        ]

    def __str__(self):
        return '{} @ {}'.format(self.profile, self.team)


class Profile(models.Model):
    user = models.OneToOneField(User, related_name='profile', primary_key=True, on_delete=models.CASCADE)
    first_name = models.TextField()
    last_name = models.TextField()
    avatar_url = models.TextField(blank=True, null=True)
    timezone = models.CharField(max_length=64, default='Europe/Moscow')
    vk_url = models.TextField(blank=True, null=True)
    email = models.TextField(blank=True, null=True)
    team_on = models.ForeignKey(
        Team, related_name='primary_profiles', blank=True, null=True, on_delete=models.SET_NULL
    )
    team_requested = models.ForeignKey(Team, related_name='users_requested', blank=True, null=True, on_delete=models.SET_NULL)
    # При подаче заявки в команду: сделать принятую команду активной (учитывается в confirm).
    join_accept_as_primary = models.BooleanField(default=True)

    def __str__(self):
        return self.first_name + ' ' + self.last_name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.team_on_id:
            ProfileTeamMembership.objects.get_or_create(profile=self, team_id=self.team_on_id)

    def repair_primary_team(self):
        """Есть членства, но team_on пустой или не из списка — выставить детерминированно."""
        ids = list(ProfileTeamMembership.objects.filter(profile=self).values_list('team_id', flat=True))
        if not ids:
            if self.team_on_id is not None:
                Profile.objects.filter(pk=self.pk).update(team_on=None)
                self.team_on_id = None
            return
        id_set = frozenset(ids)
        if self.team_on_id is None or self.team_on_id not in id_set:
            chosen = sorted(id_set)[0]
            Profile.objects.filter(pk=self.pk).update(team_on_id=chosen)
            self.team_on_id = chosen

    def sync_membership_for_team_on(self):
        """Если задана активная команда — гарантировать строку членства (админка, миграции)."""
        if self.team_on_id:
            ProfileTeamMembership.objects.get_or_create(profile=self, team_id=self.team_on_id)

    def add_team_membership(self, team, *, make_primary=False):
        from django.db import transaction
        with transaction.atomic():
            ProfileTeamMembership.objects.get_or_create(profile=self, team=team)
            if make_primary or not self.team_on_id:
                Profile.objects.filter(pk=self.pk).update(team_on=team)
                self.team_on_id = team.pk

    def remove_team_membership(self, team):
        from django.db import transaction
        with transaction.atomic():
            ProfileTeamMembership.objects.filter(profile=self, team=team).delete()
            next_id = (
                ProfileTeamMembership.objects.filter(profile=self)
                .order_by('team_id')
                .values_list('team_id', flat=True)
                .first()
            )
            Profile.objects.filter(pk=self.pk).update(team_on_id=next_id)
            self.team_on_id = next_id

    def set_primary_team(self, team):
        if not ProfileTeamMembership.objects.filter(profile=self, team=team).exists():
            return False
        if self.team_on_id != team.pk:
            Profile.objects.filter(pk=self.pk).update(team_on=team)
            self.team_on_id = team.pk
        return True

    def other_member_teams(self):
        """Команды членства кроме активной (для UI «второстепенных»)."""
        if not self.team_on_id:
            return Team.objects.filter(member_links__profile=self).order_by('visible_name', 'name').distinct()
        return (
            Team.objects.filter(member_links__profile=self)
            .exclude(pk=self.team_on_id)
            .order_by('visible_name', 'name')
            .distinct()
        )


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

    def has_registered(self, team):
        if team is None:
            return False
        return self.registrations.filter(team_id=team.pk).exists()

    def get_visible_start_time(self):
        return self.visible_start_time if self.visible_start_time is not None else self.start_time

    def get_visible_end_time(self):
        return self.visible_end_time if self.visible_end_time is not None else self.end_time


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

    VIEW_VARIANTS = (
        ('default', 'default'),
        ('table-3-n', 'table-3-n'),
        ('table-4-n', 'table-4-n'),
        ('proportions', 'Пропорции'),
    )

    view = models.CharField(default='default', max_length=100, choices=VIEW_VARIANTS)

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
        return len(self.tasks.all())

    def get_n_tasks_for_results(self):
        return len(self.tasks.filter(~models.Q(task_type='text_with_forms')))


class GameTaskGroup(models.Model):
    """Вхождение набора заданий в игру: свой номер и название для каждой игры."""

    id = models.AutoField(primary_key=True)
    game = models.ForeignKey(Game, related_name='task_group_links', on_delete=models.CASCADE)
    task_group = models.ForeignKey(TaskGroup, related_name='game_links', on_delete=models.CASCADE)
    number = models.IntegerField()
    name = models.CharField(max_length=100)

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

    @staticmethod
    def resolve_game_for_task(task, game_id=None):
        """
        Игра в контексте задания: явный game_id, иначе единственная привязанная игра.
        """
        if task is None or task.task_group_id is None:
            return None
        qs = GameTaskGroup.objects.filter(task_group_id=task.task_group_id).select_related('game')
        if game_id:
            row = qs.filter(game_id=game_id).first()
            return row.game if row else None
        if qs.count() == 1:
            return qs.first().game
        return None


class Task(models.Model):
    id = models.AutoField(primary_key=True)
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
        ('replacements_lines', 'replacements_lines'),
        ('distribute_to_teams', 'distribute_to_teams'),
        ('with_tag', 'with_tag'),
        ('autohint', 'autohint'),
        ('proportions', 'Пропорции'),
    )

    task_type = models.CharField(default='default', max_length=100, choices=TASK_TYPE_VARIANTS)

    checker = models.ForeignKey(
        CheckerType, related_name='tasks', blank=True, null=True, on_delete=models.SET_NULL,
        default='equals_with_possible_spaces',
    )
    points = models.DecimalField(decimal_places=3, max_digits=10, blank=True, null=True)
    max_attempts = models.IntegerField(blank=True, null=True)
    image_width = models.IntegerField(null=True, blank=True)
    field_text_width = models.IntegerField(null=True, blank=True)
    tags = models.JSONField(default=dict, null=True, blank=True)

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
        from games.views.track import track_task_change
        track_task_change(self)
        super(Task, self).save(*args, **kwargs)

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
        from games.replacements_lines import parse_replacements_checker_json_lines, parse_replacements_lines_text

        raw = (self.checker_data or '').strip()
        if raw:
            parsed = parse_replacements_checker_json_lines(raw)
            if parsed:
                canonical_rows, _ = parsed
                return len(canonical_rows)
        pt = parse_replacements_lines_text(self.text or '', raw or None)
        return len(pt.get('left_lines') or [])

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
        if self.task_type == 'wall':
            try:
                w = self.get_wall()
                base = getattr(w, 'max_points', None)
                if base is not None:
                    return float(base) * m
            except Exception:
                pass
            return m
        if self.task_type == 'replacements_lines':
            n = self._replacements_lines_n_answer_rows()
            if n > 0:
                return m * n
            return m
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


class AttemptsInfo:
    def __init__(self, best_attempt, attempts, hint_attempts):
        self.best_attempt = best_attempt
        self.attempts = attempts
        self.hint_attempts = hint_attempts
        self.last_attempt = None
        if attempts:
            self.last_attempt = attempts[-1]

    def get_n_attempts(self):
        return len(self.attempts)

    def is_solved(self):
        return self.best_attempt and self.best_attempt.status == 'Ok'

    def get_sum_hint_penalty(self):
        if not self.hint_attempts:
            return 0
        return sum([
            hint_attempt.hint.points_penalty
            for hint_attempt in self.hint_attempts
            if hint_attempt.is_real_request
        ])
    
    def get_result_points(self):
        result_points = 0
        if self.best_attempt:
            result_points += self.best_attempt.points
        return max(0, result_points - self.get_sum_hint_penalty())


class AttemptManager(models.Manager):
    def get_all_task_attempts(self, task, exclude_skip=True, game=None):
        queryset = super().get_queryset().filter(task=task).select_related('team', 'user')
        if game is not None:
            queryset = queryset.filter(game=game)
        if exclude_skip:
            queryset = queryset.exclude(skip=True)
        return sorted(queryset, key=lambda x: x.time)

    def _filter_by_actor(self, queryset, team=None, user=None, anon_key=None):
        if team is not None:
            return queryset.filter(team=team, user__isnull=True, anon_key__isnull=True)
        if user is not None:
            return queryset.filter(user=user, team__isnull=True, anon_key__isnull=True)
        if anon_key is not None:
            return queryset.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
        return queryset.none()

    def _attempt_row_game(self, row, hint_game=None):
        """Game for tournament filtering: Attempt.game, or HintAttempt + hint_game context."""
        if isinstance(row, Attempt):
            if row.game_id:
                return row.game
            return GameTaskGroup.resolve_game_for_task(row.task)
        if hint_game is not None:
            return hint_game
        return GameTaskGroup.resolve_game_for_task(row.hint.task)

    def get_all_attempts(self, team, task, exclude_skip=True, user=None, anon_key=None, game=None):
        queryset = super().get_queryset()
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        queryset = self._filter_by_actor(queryset, team=team, user=user, anon_key=anon_key)
        queryset = queryset.filter(task=task)
        if game is not None:
            queryset = queryset.filter(game=game)
        return sorted(queryset, key=lambda x: x.time)

    def get_all_attempts_after_equal(self, team, task, time, exclude_skip=True, user=None, anon_key=None, game=None):
        queryset = super().get_queryset()
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        queryset = self._filter_by_actor(queryset, team=team, user=user, anon_key=anon_key)
        queryset = queryset.filter(task=task, time__gte=time)
        if game is not None:
            queryset = queryset.filter(game=game)
        return sorted(queryset, key=lambda x: x.time)

    def get_all_attempts_after(self, team, task, time, exclude_skip=True, user=None, anon_key=None, game=None):
        queryset = super().get_queryset()
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        queryset = self._filter_by_actor(queryset, team=team, user=user, anon_key=anon_key)
        queryset = queryset.filter(task=task, time__gt=time)
        if game is not None:
            queryset = queryset.filter(game=game)
        return sorted(queryset, key=lambda x: x.time)

    def get_all_attempts_before(self, team, task, time, exclude_skip=True, user=None, anon_key=None, game=None):
        queryset = super().get_queryset()
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        queryset = self._filter_by_actor(queryset, team=team, user=user, anon_key=anon_key)
        queryset = queryset.filter(task=task, time__lt=time)
        if game is not None:
            queryset = queryset.filter(game=game)
        return sorted(queryset, key=lambda x: x.time)

    def filter_attempts_with_mode(self, attempts, mode='general', is_hint_attempts=False, hint_game=None):
        if mode == 'general' or not attempts:
            return attempts
        if mode == 'tournament':
            return [
                attempt for attempt in attempts
                if self._attempt_row_game(attempt, hint_game=hint_game).has_access(
                    'attempt_is_tournament', attempt=attempt, team=attempt.team, mode=mode,
                )
            ]
        raise Exception('Unknown mode: {}'.format(mode))

    def get_attempts(self, team, task, mode="general", user=None, anon_key=None, game=None):
        attempts = self.get_all_attempts(team, task, user=user, anon_key=anon_key, game=game)
        return self.filter_attempts_with_mode(attempts, mode, hint_game=game)

    def get_hint_attempts(self, team, task, mode="general", user=None, anon_key=None, game=None):
        hint_attempts = []
        for hint in task.hints.all():
            if team is not None:
                hint_attempts.extend(list(HintAttempt.objects.filter(team=team, user__isnull=True, anon_key__isnull=True, hint=hint)))
            elif user is not None:
                hint_attempts.extend(list(HintAttempt.objects.filter(user=user, team__isnull=True, anon_key__isnull=True, hint=hint)))
            elif anon_key is not None:
                hint_attempts.extend(list(HintAttempt.objects.filter(anon_key=anon_key, team__isnull=True, user__isnull=True, hint=hint)))
        return self.filter_attempts_with_mode(hint_attempts, mode, is_hint_attempts=True, hint_game=game)

    def get_attempts_before(self, team, task, time, mode="general", user=None, anon_key=None, game=None):
        attempts = self.get_all_attempts_before(team, task, time, user=user, anon_key=anon_key, game=game)
        return self.filter_attempts_with_mode(attempts, mode, hint_game=game)

    def get_task_attempts(self, task, mode="general", game=None):
        attempts = self.get_all_task_attempts(task, game=game)
        return self.filter_attempts_with_mode(attempts, mode)

    def get_task_hint_attempts(self, task, mode="general", game=None):
        hint_attempts = list(
            HintAttempt.objects.filter(hint__task=task).select_related('hint', 'team', 'user')
        )
        return self.filter_attempts_with_mode(hint_attempts, mode, is_hint_attempts=True, hint_game=game)

    def get_best_attempt(self, attempts, mode="general"):
        best_attempt = None
        for attempt in attempts:
            if best_attempt is None or \
               attempt.points > best_attempt.points or \
               (attempt.points == best_attempt.points and better_status(attempt.status, best_attempt.status)):
                best_attempt = attempt
        return best_attempt

    def get_attempts_info(self, team, task, mode="general", user=None, anon_key=None, game=None):
        attempts = self.get_attempts(team, task, mode, user=user, anon_key=anon_key, game=game)
        hint_attempts = self.get_hint_attempts(team, task, mode, user=user, anon_key=anon_key, game=game)
        best_attempt = self.get_best_attempt(attempts, mode)
        return AttemptsInfo(best_attempt, attempts, hint_attempts)

    # for results page
    def get_task_attempts_infos(self, task, mode="general", game=None):
        attempts = self.get_task_attempts(task, mode, game=game)
        hint_attempts = self.get_task_hint_attempts(task, mode, game=game)

        teams = set()

        team_to_attempts = {}
        for attempt in attempts:
            if attempt.team not in team_to_attempts:
                team_to_attempts[attempt.team] = []
            team_to_attempts[attempt.team].append(attempt)
            teams.add(attempt.team)

        team_to_hint_attempts = {}
        for hint_attempt in hint_attempts:
            if hint_attempt.team not in team_to_hint_attempts:
                team_to_hint_attempts[hint_attempt.team] = []
            team_to_hint_attempts[hint_attempt.team].append(hint_attempt)
            teams.add(hint_attempt.team)

        attempts_infos = []
        for team in teams:
            attempts = team_to_attempts.get(team, [])
            hint_attempts = team_to_hint_attempts.get(team, [])
            best_attempt = self.get_best_attempt(attempts, mode)
            attempts_info = AttemptsInfo(best_attempt, attempts, hint_attempts)
            attempts_infos.append(attempts_info)
        return attempts_infos

    def _general_results_actor_bucket(self, attempt_or_hint):
        if attempt_or_hint.team_id:
            return ('team', attempt_or_hint.team_id)
        uid = getattr(attempt_or_hint, 'user_id', None)
        if uid:
            return ('user', uid)
        ak = getattr(attempt_or_hint, 'anon_key', None)
        if ak:
            return ('anon', str(ak))
        return None

    def get_bulk_game_actor_rows(self, task_ids, mode='general', game=None):
        """
        O(1) bulk alternative to calling get_general_results_task_actor_rows or
        get_task_attempts_infos for every task individually.

        Loads all attempts + hint attempts for the given task IDs in exactly
        2 queries, groups by (task_id, actor) in Python, and returns:
            {task_id: [(actor, AttemptsInfo), ...]}

        Hidden teams are already filtered out.  For 'general' mode all actor
        types (team / personal user / anon) are returned; for 'tournament' mode
        personal rows are included but callers typically filter them.

        Pass ``game`` when the caller already has the Game object — this avoids
        the costly task__task_group__game JOIN chain used by filter_attempts_with_mode
        to look up the tournament window.
        """
        from collections import defaultdict

        if not task_ids:
            return {}

        # 1 query: all attempts for all tasks (optionally scoped to one game).
        attempt_related = ['team', 'user', 'game']
        attempt_qs = self.filter(task_id__in=task_ids, skip=False).select_related(*attempt_related).order_by('time')
        if game is not None:
            attempt_qs = attempt_qs.filter(game=game)
        all_attempts = list(attempt_qs)
        if mode == 'tournament':
            all_attempts = self.filter_attempts_with_mode(all_attempts, mode)

        # 1 query: all hint attempts for all hints in these tasks.
        hint_related = ['hint', 'team', 'user']
        all_hint_attempts = list(
            HintAttempt.objects.filter(hint__task_id__in=task_ids)
            .select_related(*hint_related)
        )
        if mode == 'tournament':
            all_hint_attempts = self.filter_attempts_with_mode(
                all_hint_attempts, mode, is_hint_attempts=True, hint_game=game,
            )

        # Group by (task_id, actor_bucket) in Python
        task_actor_attempts = defaultdict(lambda: defaultdict(list))
        task_actor_hints = defaultdict(lambda: defaultdict(list))
        actor_obj_cache = {}  # bucket -> Team / User object / anon_key string

        for a in all_attempts:
            b = self._general_results_actor_bucket(a)
            if b is None:
                continue
            task_actor_attempts[a.task_id][b].append(a)
            if b not in actor_obj_cache:
                kind, _ = b
                actor_obj_cache[b] = a.team if kind == 'team' else (a.user if kind == 'user' else a.anon_key)

        for ha in all_hint_attempts:
            b = self._general_results_actor_bucket(ha)
            if b is None:
                continue
            task_actor_hints[ha.hint.task_id][b].append(ha)
            if b not in actor_obj_cache:
                kind, _ = b
                actor_obj_cache[b] = ha.team if kind == 'team' else (ha.user if kind == 'user' else ha.anon_key)

        # Build {task_id: [(actor, AttemptsInfo), ...]}
        result = {}
        for task_id in task_ids:
            all_buckets = (
                set(task_actor_attempts[task_id].keys())
                | set(task_actor_hints[task_id].keys())
            )
            rows = []
            for b in all_buckets:
                att = task_actor_attempts[task_id].get(b, [])
                hints = task_actor_hints[task_id].get(b, [])
                if not att and not hints:
                    continue
                best = self.get_best_attempt(att)
                ai = AttemptsInfo(best, att, hints)
                kind, key = b
                obj = actor_obj_cache.get(b)
                if kind == 'team':
                    if obj is None or obj.is_hidden:
                        continue
                    rows.append((obj, ai))
                elif kind == 'user':
                    rows.append((PersonalResultsParticipant(user=obj), ai))
                else:
                    rows.append((PersonalResultsParticipant(anon_key=key), ai))
            result[task_id] = rows

        return result

    def get_general_results_task_actor_rows(self, task, game=None):
        """
        Для общей таблицы: по одному AttemptsInfo на команду или на личного/анонимного участника.
        """
        attempts = self.get_task_attempts(task, mode='general', game=game)
        hint_attempts = self.get_task_hint_attempts(task, mode='general', game=game)

        buckets = {}
        for attempt in attempts:
            b = self._general_results_actor_bucket(attempt)
            if b is None:
                continue
            buckets.setdefault(b, {'attempts': [], 'hints': []})
            buckets[b]['attempts'].append(attempt)

        for ha in hint_attempts:
            b = self._general_results_actor_bucket(ha)
            if b is None:
                continue
            buckets.setdefault(b, {'attempts': [], 'hints': []})
            buckets[b]['hints'].append(ha)

        rows = []
        for b, data in buckets.items():
            att = data['attempts']
            hints = data['hints']
            if not att and not hints:
                continue
            best_attempt = self.get_best_attempt(att, mode='general')
            ai = AttemptsInfo(best_attempt, att, hints)
            kind, key = b
            if kind == 'team':
                team = att[0].team if att else hints[0].team
                if team is None or team.is_hidden:
                    continue
                actor = team
            elif kind == 'user':
                user = att[0].user if att else hints[0].user
                actor = PersonalResultsParticipant(user=user)
            else:
                actor = PersonalResultsParticipant(anon_key=key)
            rows.append((actor, ai))
        return rows


CHAIN_TASK_TYPES = ('wall', 'replacements_lines')


class ChainTaskState(models.Model):
    """
    Authoritative accumulated chain state for wall and replacements_lines tasks.

    One row per (actor, task, game, game_mode).  Protected by SELECT FOR UPDATE during
    attempt submission so concurrent submissions for the same actor+task+mode are
    serialised at the DB level and cannot corrupt each other's chain.

    game_mode mirrors game.get_current_mode() at submission time:
      'general'    – outside tournament window (includes all historical attempts)
      'tournament' – inside tournament window (fresh start, independent chain)

    Both wall and replacements_lines use current_mode as the key, so tournament
    progress is always isolated from general progress.
    """
    team = models.ForeignKey(
        Team, related_name='chain_task_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        'auth.User', related_name='chain_task_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True)
    task = models.ForeignKey(
        Task, related_name='chain_task_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    game = models.ForeignKey(
        Game, related_name='chain_task_states',
        on_delete=models.CASCADE,
    )
    game_mode = models.CharField(max_length=20)   # 'general' | 'tournament'

    state = models.TextField(blank=True, null=True)
    last_attempt = models.ForeignKey(
        'Attempt', blank=True, null=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Partial unique indexes per actor type: correct NULL handling on all DBs.
        constraints = [
            models.UniqueConstraint(
                fields=['team', 'task', 'game', 'game_mode'],
                condition=models.Q(team__isnull=False),
                name='unique_chain_state_team_game',
            ),
            models.UniqueConstraint(
                fields=['user', 'task', 'game', 'game_mode'],
                condition=models.Q(user__isnull=False),
                name='unique_chain_state_user_game',
            ),
            models.UniqueConstraint(
                fields=['anon_key', 'task', 'game', 'game_mode'],
                condition=models.Q(anon_key__isnull=False),
                name='unique_chain_state_anon_key_game',
            ),
        ]
        indexes = [
            models.Index(fields=['team', 'task', 'game', 'game_mode']),
            models.Index(fields=['user', 'task', 'game', 'game_mode']),
            models.Index(fields=['anon_key', 'task', 'game', 'game_mode']),
        ]

    def __str__(self):
        actor = self.team if self.team_id else (self.user if self.user_id else self.anon_key)
        return 'ChainTaskState[{}][{}][{}]'.format(actor, self.task, self.game_mode)

    def state_summary(self):
        if not self.state:
            return '—'
        try:
            s = json.loads(self.state)
            task = self.task
            if task and task.task_type == 'replacements_lines':
                solved = len(s.get('solved_lines', []))
                total = s.get('total', '?')
                return '{} lines solved ({} pts)'.format(solved, total)
            if task and task.task_type == 'wall':
                pts = s.get('best_points', 0)
                stage = s.get('last_attempt', {}).get('stage', '?')
                guessed = len(s.get('guessed_words', []))
                return 'stage={}, pts={}, guessed={} cats'.format(stage, pts, guessed)
        except Exception:
            pass
        return (self.state or '')[:100]


class Attempt(models.Model):
    STATUS_VARIANTS = (
        ('Ok', 'Ok'),
        ('Pending', 'Pending'),
        ('Partial', 'Partial'),
        ('Wrong', 'Wrong'),
    )

    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(Team, related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    user = models.ForeignKey('auth.User', related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    task = models.ForeignKey(Task, related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    game = models.ForeignKey(
        Game, related_name='game_attempts',
        blank=True, null=True, on_delete=models.SET_NULL,
    )
    manager = AttemptManager()

    text = models.TextField()
    status = models.CharField(max_length=100, choices=STATUS_VARIANTS)
    possible_status = models.CharField(blank=True, null=True, max_length=100, choices=STATUS_VARIANTS)
    points = models.DecimalField(default=0, decimal_places=3, max_digits=10, blank=True, null=True)
    time = models.DateTimeField(auto_now_add=True, blank=True, null=True)
    state = models.TextField(blank=True, null=True)

    comment = models.TextField(blank=True, null=True)

    skip = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['task', 'team', 'time']),
            models.Index(fields=['task', 'user', 'time']),
            models.Index(fields=['task', 'anon_key', 'time']),
            models.Index(fields=['task', 'status']),
        ]

    def __str__(self):
        actor = self.team if self.team is not None else (self.user if self.user is not None else self.anon_key)
        return '[{}]: ({}) - {} [{}] ({})'.format(
            actor, self.task, self.get_pretty_text(), self.status,
            self.time.strftime('%Y-%m-%d %H:%M:%S')
        )

    def save(self, *args, **kwargs):
        from games.views.track import track_task_change
        track_task_change(self.task)
        super(Attempt, self).save(*args, **kwargs)

    def get_answer(self):
        if self.task is None:
            return 'DELETED'
        return self.task.answer

    def get_max_points(self):
        if self.task is None:
            return 'DELETED'
        return self.task.get_points()

    def get_pretty_text(self):
        if self.task is None:
            return 'DELETED TASK'
        if self.task.task_type in ('default', 'with_tag', 'text_with_forms', 'autohint', 'proportions'):
            return self.text
        if self.task.task_type == 'replacements_lines':
            try:
                p = json.loads(self.text)
                line_idx = p.get('line_index', 0)
                answers = p.get('answers', [])
                return 'Строка {}: {}'.format(line_idx + 1, ' '.join(answers))
            except (ValueError, TypeError):
                return self.text
        if self.task.task_type == 'wall':
            return self.task.get_wall().get_attempt_text(
                json.loads(self.text),
                ImageManager(),
                AudioManager()
            )
        if self.task.task_type == 'distribute_to_teams':
            try:
                g = self.game_id and self.game or GameTaskGroup.resolve_game_for_task(self.task)
                distr_text = json.loads(self.task.text)['list'][self.team.get_team_reg_number(g)]
            except Exception as e:
                distr_text = 'ERROR: {}'.format(e)
            return '{} ({})'.format(
                self.text,
                distr_text
            )
        raise Exception('Unknown task_type: {}'.format(self.task.task_type))


class PendingAttempt(Attempt):
    class Meta:
        proxy=True


class LikeManager(models.Manager):
    def _actor_filter(self, team=None, user=None, anon_key=None):
        if team is not None:
            return {'team': team, 'user__isnull': True, 'anon_key__isnull': True}
        if user is not None:
            return {'user': user, 'team__isnull': True, 'anon_key__isnull': True}
        return {'anon_key': anon_key, 'team__isnull': True, 'user__isnull': True}

    def get_likes(self, task, team=None):
        # По умолчанию показываем сумму КОМАНДНЫХ лайков (как раньше).
        if team is None:
            return super().get_queryset().filter(task=task, value=1, team__isnull=False, user__isnull=True, anon_key__isnull=True).count()
        return super().get_queryset().filter(task=task, value=1, **self._actor_filter(team=team)).count()

    def get_dislikes(self, task, team=None):
        # По умолчанию показываем сумму КОМАНДНЫХ дизлайков (как раньше).
        if team is None:
            return super().get_queryset().filter(task=task, value=-1, team__isnull=False, user__isnull=True, anon_key__isnull=True).count()
        return super().get_queryset().filter(task=task, value=-1, **self._actor_filter(team=team)).count()

    def get_total_likes(self, task):
        return super().get_queryset().filter(task=task, value=1).count()

    def get_total_dislikes(self, task):
        return super().get_queryset().filter(task=task, value=-1).count()

    def team_has_like(self, task, team):
        return self.get_likes(task, team) > 0

    def team_has_dislike(self, task, team):
        return self.get_dislikes(task, team) > 0

    def actor_has_like(self, task, team=None, user=None, anon_key=None):
        return super().get_queryset().filter(task=task, value=1, **self._actor_filter(team=team, user=user, anon_key=anon_key)).exists()

    def actor_has_dislike(self, task, team=None, user=None, anon_key=None):
        return super().get_queryset().filter(task=task, value=-1, **self._actor_filter(team=team, user=user, anon_key=anon_key)).exists()

    def add_like(self, task, team):
        if not self.team_has_like(task, team):
            like = Like(team=team, task=task, value=1)
            like.save()

    def add_dislike(self, task, team):
        if not self.team_has_dislike(task, team):
            dislike = Like(team=team, task=task, value=-1)
            dislike.save()

    def delete_like(self, task, team):
        like_filter = super().get_queryset().filter(task=task, team=team, value=1)
        if like_filter:
            like_filter[0].delete()

    def delete_dislike(self, task, team):
        dislike_filter = super().get_queryset().filter(task=task, team=team, value=-1)
        if dislike_filter:
            dislike_filter[0].delete()

    def add_like_actor(self, task, team=None, user=None, anon_key=None):
        if not self.actor_has_like(task, team=team, user=user, anon_key=anon_key):
            like = Like(team=team, user=user, anon_key=anon_key, task=task, value=1)
            like.save()

    def add_dislike_actor(self, task, team=None, user=None, anon_key=None):
        if not self.actor_has_dislike(task, team=team, user=user, anon_key=anon_key):
            dislike = Like(team=team, user=user, anon_key=anon_key, task=task, value=-1)
            dislike.save()

    def delete_like_actor(self, task, team=None, user=None, anon_key=None):
        qs = super().get_queryset().filter(task=task, value=1, **self._actor_filter(team=team, user=user, anon_key=anon_key))
        if qs:
            qs[0].delete()

    def delete_dislike_actor(self, task, team=None, user=None, anon_key=None):
        qs = super().get_queryset().filter(task=task, value=-1, **self._actor_filter(team=team, user=user, anon_key=anon_key))
        if qs:
            qs[0].delete()


class Like(models.Model):
    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(Team, related_name='likes', blank=True, null=True, on_delete=models.SET_NULL)
    user = models.ForeignKey('auth.User', related_name='likes', blank=True, null=True, on_delete=models.SET_NULL)
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    task = models.ForeignKey(Task, related_name='likes', blank=True, null=True, on_delete=models.SET_NULL)
    value = models.IntegerField()
    manager = LikeManager()

    def __str__(self):
        actor = self.team if self.team is not None else (self.user if self.user is not None else self.anon_key)
        return '{} to task {} by {}'.format('Like' if self.value == 1 else 'Dislike', self.task, actor)


class Hint(models.Model):
    id = models.AutoField(primary_key=True)
    task = models.ForeignKey(Task, related_name='hints', blank=True, null=True, on_delete=models.SET_NULL)
    number = models.IntegerField(null=True, blank=True)
    desc = models.TextField(null=True, blank=True)
    text = models.TextField(null=True, blank=True)
    points_penalty = models.DecimalField(decimal_places=3, max_digits=10, blank=True, null=True)
    required_hints = models.ManyToManyField('Hint', blank=True)

    def __str__(self):
        return '{} - Подсказка #{} [-{}]'.format(self.task, self.number, self.points_penalty)

    def save(self, *args, **kwargs):
        from games.views.track import track_task_change
        track_task_change(self.task)
        super(Hint, self).save(*args, **kwargs)

class HintAttempt(models.Model):
    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(Team, related_name='hint_attempts', blank=True, null=True, on_delete=models.SET_NULL)
    user = models.ForeignKey('auth.User', related_name='hint_attempts', blank=True, null=True, on_delete=models.SET_NULL)
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    hint = models.ForeignKey(Hint, related_name='hint_attempts', blank=True, null=True, on_delete=models.SET_NULL)
    time = models.DateTimeField(auto_now_add=True, blank=True)
    is_real_request = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['hint', 'team', 'time']),
            models.Index(fields=['hint', 'user', 'time']),
            models.Index(fields=['hint', 'anon_key', 'time']),
            models.Index(fields=['hint', 'is_real_request']),
        ]

    def __str__(self):
        actor = self.team if self.team is not None else (self.user if self.user is not None else self.anon_key)
        return '{}[{}]: {}'.format(
            '(just watching)' if self.is_real_request else '',
            actor,
            self.hint
        )

    def save(self, *args, **kwargs):
        from games.views.track import track_task_change
        track_task_change(self.hint.task)
        super(HintAttempt, self).save(*args, **kwargs)


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


class TicketRequest(models.Model):
    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(Team, related_name='ticket_requests', blank=True, null=True, on_delete=models.SET_NULL)
    money = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    tickets = models.IntegerField(default=0, validators=[MinValueValidator(1),MaxValueValidator(20)])
    time = models.DateTimeField(auto_now_add=True, blank=True)
    yookassa_id = models.TextField(null=True, blank=True)

    TICKER_REQUEST_STATUS_VARIANTS = (
        ('Pending', 'Pending'),
        ('Accepted', 'Accepted'),
        ('Rejected', 'Rejected'),
    )

    status = models.CharField(default='Pending', max_length=100, choices=TICKER_REQUEST_STATUS_VARIANTS)

    def __str__(self):
        return '{}: [{}] Сумма: {} р. - Билетов: {} - Команда: {}'.format(
            self.time.strftime('%Y-%m-%d %H:%M:%S'),
            self.status,
            self.money,
            self.tickets,
            self.team
        )


class PendingTicketRequest(TicketRequest):
    class Meta:
        proxy=True


class Registration(models.Model):
    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(Team, related_name='registrations', blank=True, null=True, on_delete=models.SET_NULL)
    game = models.ForeignKey(Game, related_name='registrations', blank=True, null=True, on_delete=models.SET_NULL)
    time = models.DateTimeField(auto_now_add=True, blank=True, null=True)
    with_referent = models.ForeignKey(Team, related_name='registrations_with_referent', blank=True, null=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [
            models.Index(fields=['game', 'team'], name='games_reg_game_team_idx'),
        ]

    def __str__(self):
        return '{} --- {} ({}){}'.format(
            self.game,
            self.team,
            self.time.strftime('%Y-%m-%d %H:%M:%S'),
            ' [with referent {}]'.format(self.with_referent) if self.with_referent is not None else ''
        )
