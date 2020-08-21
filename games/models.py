import json
import os

from django.contrib.auth.models import User
from django.db import models
from django.dispatch import receiver
from django.utils import timezone
from games.access import get_game_access
from games.util import better_status
from games.wall import Wall
from allauth.socialaccount.models import SocialAccount


class Team(models.Model):
    name = models.CharField(primary_key=True, max_length=100)
    is_tester = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)

    def __str__(self):
        return self.name
    
    def get_n_users_on(self):
        return len(self.users_on.all())
    
    def get_n_users_requested(self):
        return len(self.users_requested.all())


class Profile(models.Model):
    user = models.OneToOneField(User, related_name='profile', primary_key=True, on_delete=models.CASCADE)
    first_name = models.TextField()
    last_name = models.TextField()
    avatar_url = models.TextField(blank=True, null=True)
    vk_url = models.TextField(blank=True, null=True)
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
    name = models.CharField(max_length=100)
    image = models.ImageField(null=True, blank=True)
    theme = models.CharField(max_length=100, null=True, blank=True)
    author = models.CharField(max_length=100)

    start_time = models.DateTimeField(default=timezone.now, blank=True)
    end_time = models.DateTimeField(default=timezone.now, blank=True)

    is_ready = models.BooleanField(default=False)
    is_testing = models.BooleanField(default=False)
    is_playable = models.BooleanField(default=False)
    is_tournament = models.BooleanField(default=False)

    game_url = models.CharField(max_length=500, null=True, blank=True)
    answers_url = models.CharField(max_length=500, null=True, blank=True)
    standings_url = models.CharField(max_length=500, null=True, blank=True)

    rules = models.ForeignKey(HTMLPage, to_field='name', related_name='games', blank=True, null=True, on_delete=models.SET_NULL)

    tournament_rules = models.ForeignKey(HTMLPage, to_field='name', related_name='games_tournament', blank=True, null=True, on_delete=models.SET_NULL)
    general_rules = models.ForeignKey(HTMLPage, to_field='name', related_name='games_general', blank=True, null=True, on_delete=models.SET_NULL)

    def __str__(self):
        return self.name

    def has_access(self, action, team=None, attempt=None, mode='general'):
        return get_game_access(game=self, action=action, team=team, attempt=attempt, mode=mode)

    def get_current_mode(self, attempt=None):
        if self.has_access(action='attempt_is_tournament', attempt=attempt):
            return 'tournament'
        return 'general'


class TaskGroup(models.Model):
    id = models.AutoField(primary_key=True)
    game = models.ForeignKey(Game, related_name='task_groups', blank=True, null=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=100)
    number = models.IntegerField()
    rules = models.ForeignKey(HTMLPage, to_field='name', related_name='task_groups', blank=True, null=True, on_delete=models.SET_NULL)

    checker = models.ForeignKey(CheckerType, related_name='task_groups', blank=True, null=True, on_delete=models.SET_NULL)
    points = models.DecimalField(default=1, decimal_places=3, max_digits=10, blank=True, null=True)
    max_attempts = models.IntegerField(default=3, blank=True, null=True)
    image_width = models.IntegerField(default=300, null=True, blank=True)

    VIEW_VARIANTS = (
        ('default', 'default'),
        ('table-3-n', 'table-3-n')
    )

    view = models.CharField(default='default', max_length=100, choices=VIEW_VARIANTS)

    def __str__(self):
        return '[{}]: {}. {}'.format(self.game.name, self.number, self.name)

    def get_li_class(self):
        if self.view == 'table-3-n':
            return 'table-3-n-cell'
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
    )

    task_type = models.CharField(default='default', max_length=100, choices=TASK_TYPE_VARIANTS)

    checker = models.ForeignKey(CheckerType, related_name='tasks', blank=True, null=True, on_delete=models.SET_NULL)
    points = models.DecimalField(decimal_places=3, max_digits=10, blank=True, null=True)
    max_attempts = models.IntegerField(blank=True, null=True)
    image_width = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return '{}: {}.{}'.format(self.task_group.game.name, self.task_group.number, self.number)

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
        raise Exception('Task has no points')

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
        try:
            return tuple([int(x) for x in self.number.split('.')])
        except:
            return self.number

    def get_wall(self):
        return Wall(self)

    def get_attempt_form(self, *args, **kwargs):
        from games.forms import AttemptForm
        return AttemptForm(*args, **kwargs)


class AttemptsInfo:
    def __init__(self, best_attempt, attempts):
        self.best_attempt = best_attempt
        self.attempts = attempts
        self.last_attempt = None
        if attempts:
            self.last_attempt = attempts[-1]

    def get_n_attempts(self):
        return len(self.attempts)


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

    def filter_attempts_with_mode(self, attempts, mode='general'):
        if mode == 'general' or not attempts:
            return attempts
        if mode == 'tournament':
            game = attempts[0].task.task_group.game
            return [attempt for attempt in attempts if game.has_access('attempt_is_tournament', attempt=attempt, team=attempt.team, mode=mode)]
        raise Exception('Unknown mode: {}'.filter(mode))

    def get_attempts(self, team, task, mode="general"):
        attempts = self.get_all_attempts(team, task)
        return self.filter_attempts_with_mode(attempts, mode)

    def get_attempts_before(self, team, task, time, mode="general"):
        attempts = self.get_all_attempts_before(team, task, time)
        return self.filter_attempts_with_mode(attempts, mode)

    def get_task_attempts(self, task, mode="general"):
        attempts = self.get_all_task_attempts(task)
        return self.filter_attempts_with_mode(attempts, mode)

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
        if not attempts:
            return None
        best_attempt = self.get_best_attempt(attempts, mode)
        return AttemptsInfo(best_attempt, attempts)

    def get_task_attempts_infos(self, task, mode="general"):
        attempts = self.get_task_attempts(task, mode)
        team_to_attempts = {}
        for attempt in attempts:
            if attempt.team not in team_to_attempts:
                team_to_attempts[attempt.team] = []
            team_to_attempts[attempt.team].append(attempt)
        attempts_infos = []
        for attempts in team_to_attempts.values():
            best_attempt = self.get_best_attempt(attempts, mode)
            attempts_info = AttemptsInfo(best_attempt, attempts)
            attempts_infos.append(attempts_info)
        return attempts_infos


class Attempt(models.Model):
    STATUS_VARIANTS = (
        ('Ok', 'Ok'),
        ('Pending', 'Pending'),
        ('Partial', 'Partial'),
        ('Wrong', 'Wrong'),
    )

    team = models.ForeignKey(Team, related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    task = models.ForeignKey(Task, related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    manager = AttemptManager()

    text = models.TextField()
    status = models.CharField(max_length=100, choices=STATUS_VARIANTS)
    possible_status = models.CharField(blank=True, null=True, max_length=100, choices=STATUS_VARIANTS)
    points = models.DecimalField(default=0, decimal_places=3, max_digits=10, blank=True, null=True)
    time = models.DateTimeField(auto_now_add=True, blank=True)
    state = models.TextField(blank=True, null=True)

    comment = models.TextField(blank=True, null=True)

    skip = models.BooleanField(default=False)

    def __str__(self):
        return '[{}]: ({}) - {} [{}]'.format(self.team, self.task, self.get_pretty_text(), self.status)

    def get_answer(self):
        return self.task.answer

    def get_max_points(self):
        return self.task.get_points()

    def get_pretty_text(self):
        if self.task.task_type == 'default':
            return self.text
        if self.task.task_type == 'wall':
            return self.task.get_wall().get_attempt_text(json.loads(self.text))
        raise Exception('Unknown task_type: {}'.format(self.task.task_type))


class ProxyAttempt(Attempt):
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
    team = models.ForeignKey(Team, related_name='likes', blank=True, null=True, on_delete=models.SET_NULL)
    task = models.ForeignKey(Task, related_name='likes', blank=True, null=True, on_delete=models.SET_NULL)
    value = models.IntegerField()
    manager = LikeManager()

    def __str__(self):
        return '{} to task {} by team {}'.format('Like' if self.value == 1 else 'Dislike', self.task, self.team)


class Hint(models.Model):
    task = models.ForeignKey(Task, related_name='hints', blank=True, null=True, on_delete=models.SET_NULL)
    text = models.TextField(null=True, blank=True)
    points_penalty = models.DecimalField(decimal_places=3, max_digits=10, blank=True, null=True)
