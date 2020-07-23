import os

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from allauth.socialaccount.models import SocialAccount


class Team(models.Model):
    name = models.CharField(primary_key=True, max_length=100)
    is_tester = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class Profile(models.Model):
    user = models.OneToOneField(User, related_name='profile', primary_key=True, on_delete=models.CASCADE)
    first_name = models.TextField()
    last_name = models.TextField()
    avatar_url = models.TextField(blank=True, null=True)
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

    def has_started(self):
        now = timezone.now()
        if self.is_playable and now >= self.start_time:
            return True
        return False

    def results_are_available(self, team, mode='general'):
        if mode == 'tournament' and not self.is_tournament:
            return False
        if not self.is_playable:
            return False
        if self.is_ready and self.has_started():
            return True
        if self.is_testing and team.is_tester:
            return True
        return False

    def general_results_are_available(self, team):
        return self.results_are_available(team, mode='general')

    def tournament_results_are_available(self, team):
        return self.results_are_available(team, mode='tournament')

    def is_available(self, team):
        if not team:
            return False
        return self.results_are_available(team)

    def get_time_reference(self, attempt):
        if attempt.time < self.start_time:
            return 'before'
        if self.start_time <= attempt.time <= self.end_time:
            return 'during'
        if self.end_time < attempt.time:
            return 'after'
        raise Exception('Impossible situation')

    def get_modes(self, attempt):
        modes = []
        if self.get_time_reference(attempt) == 'after':
            modes.append('general')
        if self.get_time_reference(attempt) == 'during':
            modes.append('general')
            if self.is_tournaments:
                modes.append('tournament')
        return modes


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
    )

    view = models.CharField(default='default', max_length=100, choices=VIEW_VARIANTS)

    def __str__(self):
        return '[{}]: {}. {}'.format(self.game.name, self.number, self.name)

    def get_li_class(self):
        return ''

    def get_attempt_form(self):
        from games.forms import AttemptForm
        return AttemptForm()

    def get_n_tasks(self):
        return len(self.tasks.all())


class Task(models.Model):
    id = models.AutoField(primary_key=True)
    task_group = models.ForeignKey(TaskGroup, related_name='tasks', blank=True, null=True, on_delete=models.SET_NULL)
    number = models.IntegerField()
    image = models.ImageField(null=True, blank=True)
    text = models.TextField(null=True, blank=True)
    checker_data = models.TextField(null=True, blank=True)
    answer = models.TextField(null=True, blank=True)
    answer_comment = models.TextField(null=True, blank=True)

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


class AttemptsInfo(models.Model):
    team = models.ForeignKey(Team, related_name='attempt_infos', blank=True, null=True, on_delete=models.SET_NULL)
    task = models.ForeignKey(Task, related_name='attempt_infos', blank=True, null=True, on_delete=models.SET_NULL)

    best_attempt = models.ForeignKey('Attempt', blank=True, null=True, on_delete=models.SET_NULL)

    MODE_VARIANTS = (
        ('general', 'general'),
        ('tournament', 'tournament'),
    )

    mode = models.CharField(default='general', max_length=100, choices=MODE_VARIANTS)

    def __str__(self):
        return '[{}]: ({}), ({})'.format(self.team, self.task, self.mode)

    def get_n_attempts(self):
        return len(self.attempts.all())


class Attempt(models.Model):
    STATUS_VARIANTS = (
        ('Ok', 'Ok'),
        ('Pending', 'Pending'),
        ('Wrong', 'Wrong'),
    )

    team = models.ForeignKey(Team, related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    task = models.ForeignKey(Task, related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    attempts_infos = models.ManyToManyField(AttemptsInfo, related_name='attempts', blank=True)

    text = models.TextField()
    status = models.CharField(max_length=100, choices=STATUS_VARIANTS)
    possible_status = models.CharField(blank=True, null=True, max_length=100, choices=STATUS_VARIANTS)
    points = models.DecimalField(default=0, decimal_places=3, max_digits=10, blank=True, null=True)
    time = models.DateTimeField(auto_now_add=True, blank=True)

    def __str__(self):
        return '[{}]: ({}) - {} [{}]'.format(self.team, self.task, self.text, self.status)

    def get_answer(self):
        return self.task.answer

    def get_max_points(self):
        return self.task.get_points()

    def get_tournament_attempts_info(self):
        for attempts_info in self.attempts_infos:
            if attempts_info.mode == 'tournament':
                return attempts_info
        return None


class ProxyAttempt(Attempt):
    class Meta:
        proxy=True


def create_profile(sender, **kw):
    social_account = kw["instance"]
    if kw["created"]:
        user = social_account.user
        profile = Profile(
            user=user,
            first_name=social_account.extra_data['first_name'],
            last_name=social_account.extra_data['last_name'],
            avatar_url=social_account.extra_data['photo_medium']
        )
        profile.save()

post_save.connect(create_profile, sender=SocialAccount, dispatch_uid="socialaccount-profilecreation-signal")
