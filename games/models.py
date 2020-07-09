import os

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from allauth.socialaccount.models import SocialAccount


class Team(models.Model):
    name = models.TextField(primary_key=True)

    def __str__(self):
        return self.name


class Profile(models.Model):
    user = models.OneToOneField(User, related_name='profile', primary_key=True, on_delete=models.CASCADE)
    first_name = models.TextField()
    last_name = models.TextField()
    avatar_url = models.TextField()
    team_on = models.ForeignKey(Team, related_name='users_on', blank=True, null=True, on_delete=models.CASCADE)
    team_requested = models.ForeignKey(Team, related_name='users_requested', blank=True, null=True, on_delete=models.CASCADE)

    def __str__(self):
        return self.first_name + ' ' + self.last_name

    class Meta:
        verbose_name_plural = "User Profiles"


def create_profile(sender, **kw):
    print('!!!!')
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
