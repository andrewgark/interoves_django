import hashlib
import uuid
import json
import os
import re

from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
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
    name_hash = models.CharField(max_length=256, null=True)
    is_tester = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)

    project = models.ForeignKey(
        Project, related_name='teams',
        default='main', on_delete=models.CASCADE
    )

    tickets = models.IntegerField(default=0)
    ticket_price = models.IntegerField(default=1000)

    referer = models.ForeignKey('Team', related_name='referents', blank=True, null=True, on_delete=models.SET_NULL)


    def save_name_hash(self):
        self.name_hash = hashlib.sha512((self.name + 'salt').encode()).hexdigest()[:50]

    def get_name_hash(self):
        # if self.name_hash is None:
        self.save_name_hash()
        return str(self.name_hash)

    def save(self, *args, **kwargs):
        if not self.visible_name:
            self.visible_name = self.name
        self.save_name_hash()
        super(Team, self).save(*args, **kwargs)

    def __str__(self):
        return self.visible_name
    
    def get_n_users_on(self):
        return len(self.users_on.all())
    
    def get_n_users_requested(self):
        return len(self.users_requested.all())

    def get_team_reg_number(self, game):
        regs = [reg for reg in Registration.objects.filter(game=game) if not reg.team.is_hidden]
        regs.sort(key=lambda r: r.time)
        team_number = None
        for i, reg in enumerate(regs):
            if self == reg.team:
                team_number = i
                break
        return team_number


class Profile(models.Model):
    user = models.OneToOneField(User, related_name='profile', primary_key=True, on_delete=models.CASCADE)
    first_name = models.TextField()
    last_name = models.TextField()
    avatar_url = models.TextField(blank=True, null=True)
    vk_url = models.TextField(blank=True, null=True)
    email = models.TextField(blank=True, null=True)
    team_on = models.ForeignKey(Team, related_name='users_on', blank=True, null=True, on_delete=models.SET_NULL)
    team_requested = models.ForeignKey(Team, related_name='users_requested', blank=True, null=True, on_delete=models.SET_NULL)

    def __str__(self):
        return self.first_name + ' ' + self.last_name


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

    def __str__(self):
        return self.name

    def has_access(self, action, team=None, attempt=None, mode='general'):
        return get_game_access(game=self, action=action, team=team, attempt=attempt, mode=mode)

    def get_current_mode(self, attempt=None):
        if self.has_access(action='attempt_is_tournament', attempt=attempt):
            return 'tournament'
        return 'general'

    def has_registered(self, team):
        for reg in self.registrations.all():
            if reg.team == team:
                return True
        return False

    def get_visible_start_time(self):
        return self.visible_start_time if self.visible_start_time is not None else self.start_time

    def get_visible_end_time(self):
        return self.visible_end_time if self.visible_end_time is not None else self.end_time


class TaskGroup(models.Model):
    id = models.AutoField(primary_key=True)
    game = models.ForeignKey(Game, related_name='task_groups', blank=True, null=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=100)
    number = models.IntegerField()
    rules = models.ForeignKey(HTMLPage, to_field='name', related_name='task_groups', blank=True, null=True, on_delete=models.SET_NULL)
    text = models.TextField(null=True, blank=True)

    checker = models.ForeignKey(CheckerType, related_name='task_groups', blank=True, null=True, on_delete=models.SET_NULL)
    points = models.DecimalField(default=1, decimal_places=3, max_digits=10, blank=True, null=True)
    max_attempts = models.IntegerField(default=3, blank=True, null=True)
    image_width = models.IntegerField(default=300, null=True, blank=True)
    tags = models.JSONField(default=dict, null=True, blank=True)

    VIEW_VARIANTS = (
        ('default', 'default'),
        ('table-3-n', 'table-3-n'),
        ('table-4-n', 'table-4-n')
    )

    view = models.CharField(default='default', max_length=100, choices=VIEW_VARIANTS)

    def __str__(self):
        return '[{}]: {}. {}'.format(self.game.name, self.number, self.name)

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
        ('distribute_to_teams', 'distribute_to_teams'),
        ('with_tag', 'with_tag'),
        ('autohint', 'autohint'),
    )

    task_type = models.CharField(default='default', max_length=100, choices=TASK_TYPE_VARIANTS)

    checker = models.ForeignKey(CheckerType, related_name='tasks', blank=True, null=True, on_delete=models.SET_NULL)
    points = models.DecimalField(decimal_places=3, max_digits=10, blank=True, null=True)
    max_attempts = models.IntegerField(blank=True, null=True)
    image_width = models.IntegerField(null=True, blank=True)
    field_text_width = models.IntegerField(null=True, blank=True)
    tags = models.JSONField(default=dict, null=True, blank=True)

    def __str__(self):
        game_name = 'NONE'
        if self.task_group is not None and self.task_group.game is not None:
            game_name = self.task_group.game.name
        task_group_number = 'NONE'
        if self.task_group is not None:
            task_group_number = self.task_group.number
        return '{}: {}.{}'.format(game_name, task_group_number, self.number)

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
    def get_all_task_attempts(self, task, exclude_skip=True):
        queryset = super().get_queryset()
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        return sorted(queryset.filter(task=task), key=lambda x: x.time)

    def get_all_attempts(self, team, task, exclude_skip=True):
        queryset = super().get_queryset()
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        return sorted(queryset.filter(team=team, task=task), key=lambda x: x.time)

    def get_all_attempts_after_equal(self, team, task, time, exclude_skip=True):
        queryset = super().get_queryset()
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        return sorted(queryset.filter(team=team, task=task, time__gte=time), key=lambda x: x.time)

    def get_all_attempts_after(self, team, task, time, exclude_skip=True):
        queryset = super().get_queryset()
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        return sorted(queryset.filter(team=team, task=task, time__gt=time), key=lambda x: x.time)

    def get_all_attempts_before(self, team, task, time, exclude_skip=True):
        queryset = super().get_queryset()
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        return sorted(queryset.filter(team=team, task=task, time__lt=time), key=lambda x: x.time)

    def filter_attempts_with_mode(self, attempts, mode='general', is_hint_attempts=False):
        if mode == 'general' or not attempts:
            return attempts
        if mode == 'tournament':
            if not is_hint_attempts:
                game = attempts[0].task.task_group.game
            else:
                game = attempts[0].hint.task.task_group.game
            return [attempt for attempt in attempts if game.has_access('attempt_is_tournament', attempt=attempt, team=attempt.team, mode=mode)]
        raise Exception('Unknown mode: {}'.filter(mode))

    def get_attempts(self, team, task, mode="general"):
        attempts = self.get_all_attempts(team, task)
        return self.filter_attempts_with_mode(attempts, mode)

    def get_hint_attempts(self, team, task, mode="general"):
        hint_attempts = []
        for hint in task.hints.all():
            hint_attempts.extend(list(HintAttempt.objects.filter(team=team, hint=hint)))
        return self.filter_attempts_with_mode(hint_attempts, mode, is_hint_attempts=True)

    def get_attempts_before(self, team, task, time, mode="general"):
        attempts = self.get_all_attempts_before(team, task, time)
        return self.filter_attempts_with_mode(attempts, mode)

    def get_task_attempts(self, task, mode="general"):
        attempts = self.get_all_task_attempts(task)
        return self.filter_attempts_with_mode(attempts, mode)

    def get_task_hint_attempts(self, task, mode="general"):
        hint_attempts = []
        for hint in task.hints.all():
            hint_attempts.extend(list(HintAttempt.objects.filter(hint=hint)))
        return self.filter_attempts_with_mode(hint_attempts, mode, is_hint_attempts=True)

    def get_best_attempt(self, attempts, mode="general"):
        best_attempt = None
        for attempt in attempts:
            if best_attempt is None or \
               attempt.points > best_attempt.points or \
               (attempt.points == best_attempt.points and better_status(attempt.status, best_attempt.status)):
                best_attempt = attempt
        return best_attempt

    def get_attempts_info(self, team, task, mode="general"):
        attempts = self.get_attempts(team, task, mode)
        hint_attempts = self.get_hint_attempts(team, task, mode)
        best_attempt = self.get_best_attempt(attempts, mode)
        return AttemptsInfo(best_attempt, attempts, hint_attempts)

    # for results page
    def get_task_attempts_infos(self, task, mode="general"):
        attempts = self.get_task_attempts(task, mode)
        hint_attempts = self.get_task_hint_attempts(task, mode)

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


class Attempt(models.Model):
    STATUS_VARIANTS = (
        ('Ok', 'Ok'),
        ('Pending', 'Pending'),
        ('Partial', 'Partial'),
        ('Wrong', 'Wrong'),
    )

    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(Team, related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    task = models.ForeignKey(Task, related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    manager = AttemptManager()

    text = models.TextField()
    status = models.CharField(max_length=100, choices=STATUS_VARIANTS)
    possible_status = models.CharField(blank=True, null=True, max_length=100, choices=STATUS_VARIANTS)
    points = models.DecimalField(default=0, decimal_places=3, max_digits=10, blank=True, null=True)
    time = models.DateTimeField(auto_now_add=True, blank=True, null=True)
    state = models.TextField(blank=True, null=True)

    comment = models.TextField(blank=True, null=True)

    skip = models.BooleanField(default=False)

    def __str__(self):
        return '[{}]: ({}) - {} [{}] ({})'.format(
            self.team, self.task, self.get_pretty_text(), self.status,
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
        if self.task.task_type in ('default', 'with_tag', 'text_with_forms', 'autohint'):
            return self.text
        if self.task.task_type == 'wall':
            return self.task.get_wall().get_attempt_text(
                json.loads(self.text),
                ImageManager(),
                AudioManager()
            )
        if self.task.task_type == 'distribute_to_teams':
            try:
                distr_text = json.loads(self.task.text)['list'][self.team.get_team_reg_number(self.task.task_group.game)]
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
    def get_likes(self, task, team=None):
        if team is None:
            return super().get_queryset().filter(task=task, value=1).count()
        return super().get_queryset().filter(task=task, team=team, value=1).count()

    def get_dislikes(self, task, team=None):
        if team is None:
            return super().get_queryset().filter(task=task, value=-1).count()
        return super().get_queryset().filter(task=task, team=team, value=-1).count()

    def team_has_like(self, task, team):
        return self.get_likes(task, team) > 0

    def team_has_dislike(self, task, team):
        return self.get_dislikes(task, team) > 0

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


class Like(models.Model):
    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(Team, related_name='likes', blank=True, null=True, on_delete=models.SET_NULL)
    task = models.ForeignKey(Task, related_name='likes', blank=True, null=True, on_delete=models.SET_NULL)
    value = models.IntegerField()
    manager = LikeManager()

    def __str__(self):
        return '{} to task {} by team {}'.format('Like' if self.value == 1 else 'Dislike', self.task, self.team)


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
    hint = models.ForeignKey(Hint, related_name='hint_attempts', blank=True, null=True, on_delete=models.SET_NULL)
    time = models.DateTimeField(auto_now_add=True, blank=True)
    is_real_request = models.BooleanField(default=False)

    def __str__(self):
        return '{}[{}]: {}'.format(
            '(just watching)' if self.is_real_request else '',
            self.team,
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
    def __str__(self):
        return '{} --- {} ({}){}'.format(
            self.game,
            self.team,
            self.time.strftime('%Y-%m-%d %H:%M:%S'),
            ' [with referent {}]'.format(self.with_referent) if self.with_referent is not None else ''
        )
