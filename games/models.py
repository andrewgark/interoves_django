import os

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from allauth.socialaccount.models import SocialAccount
from datetime import datetime

class Team(models.Model):
    name = models.CharField(primary_key=True, max_length=100)

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
    html = models.TextField()


class Checker(models.Model):
    checker_type = models.CharField(primary_key=True, max_length=100)


class Game(models.Model):
    name = models.CharField(primary_key=True, max_length=100)
    image = models.ImageField(null=True, blank=True)
    theme = models.CharField(max_length=100, null=True, blank=True)
    author = models.CharField(max_length=100)

    start_time = models.DateTimeField(default=datetime.now, blank=True)
    end_time = models.DateTimeField(default=datetime.now, blank=True)

    is_ready = models.BooleanField()
    is_playable = models.BooleanField()

    game_url = models.CharField(max_length=500, null=True, blank=True)
    answers_url = models.CharField(max_length=500, null=True, blank=True)
    standings_url = models.CharField(max_length=500, null=True, blank=True)

    rules = models.ForeignKey(HTMLPage, to_field='name', related_name='games', blank=True, null=True, on_delete=models.SET_NULL)


class TaskGroup(models.Model):
    name = models.CharField(primary_key=True, max_length=100)
    # game = models.ForeignKey(Game, related_name='task_groups', blank=True, null=True, on_delete=models.SET_NULL)
    number = models.IntegerField()    
    rules = models.ForeignKey(HTMLPage, to_field='name', related_name='task_groups', blank=True, null=True, on_delete=models.SET_NULL)    


class Task(models.Model):
    task_group = models.ForeignKey(TaskGroup, related_name='tasks', blank=True, null=True, on_delete=models.SET_NULL)
    number = models.IntegerField()
    image = models.ImageField(null=True, blank=True)
    text = models.TextField()
    checker = models.ForeignKey(Checker, related_name='tasks', blank=True, null=True, on_delete=models.SET_NULL)
    points = models.DecimalField(decimal_places=3, max_digits=10)
    max_attempts = models.IntegerField(default=0)


class Attempt(models.Model):
    team = models.ForeignKey(Team, related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    task = models.ForeignKey(Task, related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)    
    value = models.TextField()
    result = models.CharField(max_length=100)
    time = models.DateTimeField(auto_now_add=True, blank=True)


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
