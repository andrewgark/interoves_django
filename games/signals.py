
from django.db.models.signals import post_save
from games.models import Profile, SocialAccount, Attempt, Task
from games.recheck import recheck_queue_from_next, recheck_full


def create_profile(sender, **kw):
    social_account = kw["instance"]
    if kw["created"]:
        user = social_account.user
        profile = Profile(
            user=user,
            first_name=social_account.extra_data['first_name'],
            last_name=social_account.extra_data['last_name'],
            avatar_url=social_account.extra_data['photo_medium'],
            vk_url='vk.com/{}'.format(social_account.extra_data['screen_name']),
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


post_save.connect(create_profile, sender=SocialAccount, dispatch_uid="socialaccount-profilecreation-signal")
post_save.connect(recheck_after_saving_wall_attempt, sender=Attempt, dispatch_uid="wallattempt-recheck-signal")
post_save.connect(recheck_after_saving_wall_task, sender=Task, dispatch_uid="walltask-recheck-signal")
