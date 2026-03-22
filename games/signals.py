from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from games.models import Game, Profile, SocialAccount, Attempt, Task
from games.recheck import recheck_queue_from_next, recheck_full


def create_profile(sender, **kw):
    social_account = kw["instance"]
    print(social_account.extra_data)
    if kw["created"]:
        user = social_account.user

        first_name = social_account.extra_data.get('first_name', '')
        if not first_name:
            first_name = social_account.extra_data.get('given_name', '')

        last_name = social_account.extra_data.get('last_name', '')
        if not last_name:
            last_name = social_account.extra_data.get('family_name', '')

        avatar_url = social_account.extra_data.get('photo_medium', '')
        if not avatar_url:
            avatar_url = social_account.extra_data.get('picture', '')

        vk_id = social_account.extra_data.get('screen_name')
        if vk_id != '':
            vk_url = 'vk.com/{}'.format(vk_id)
        else:
            vk_url = ''

        profile = Profile(
            user=user,
            first_name=first_name,
            last_name=last_name,
            avatar_url=avatar_url,
            vk_url=vk_url,
            email=social_account.extra_data.get('email', '')
        )
        profile.save()


def recheck_after_saving_wall_attempt(sender, **kw):
    attempt = kw["instance"]
    if attempt.task.task_type != "wall":
        return
    if kw["created"]:
        return
    recheck_queue_from_next(None, attempt.id)    


def recheck_after_saving_wall_task(sender, **kw):
    task = kw["instance"]
    if task.task_type != "wall":
        return
    if kw["created"]:
        return
    recheck_full(None, task=task)    


@receiver(pre_save, sender=Game)
def game_cache_old_for_access_notify(sender, instance, **kwargs):
    if not instance.pk:
        instance._game_old_snapshot = None
        return
    try:
        instance._game_old_snapshot = Game.objects.get(pk=instance.pk)
    except Game.DoesNotExist:
        instance._game_old_snapshot = None


@receiver(post_save, sender=Game)
def game_notify_registered_play_access(sender, instance, created, **kwargs):
    if created:
        return
    old = getattr(instance, '_game_old_snapshot', None)
    if old is None:
        return
    from games.views.track import (
        notify_registered_users_game_lifecycle_changed,
        notify_registered_users_play_access_changed,
    )

    notify_registered_users_play_access_changed(old, instance)
    notify_registered_users_game_lifecycle_changed(old, instance)


post_save.connect(create_profile, sender=SocialAccount, dispatch_uid="socialaccount-profilecreation-signal")
# post_save.connect(recheck_after_saving_wall_attempt, sender=Attempt, dispatch_uid="wallattempt-recheck-signal")
# post_save.connect(recheck_after_saving_wall_task, sender=Task, dispatch_uid="walltask-recheck-signal")
