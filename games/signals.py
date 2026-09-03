from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from allauth.account.signals import user_signed_up

from games.analytics import queue_pending_goal, signup_goal_payload
from games.analytics_persistence import create_or_reread_analytics_row
from games.models import (
    Attempt,
    ChainTaskState,
    Game,
    GameTaskGroup,
    HintAttempt,
    PlayerAnalyticsState,
    Profile,
    SocialAccount,
    Task,
)
from games.recheck import recheck_queue_from_next, recheck_full


def create_profile(sender, **kw):
    social_account = kw["instance"]
    if not kw["created"]:
        return

    user = social_account.user
    extra = social_account.extra_data or {}
    first_name = extra.get('first_name') or extra.get('given_name') or ''
    last_name = extra.get('last_name') or extra.get('family_name') or ''
    avatar_url = extra.get('photo_medium') or extra.get('picture') or ''
    vk_id = extra.get('screen_name') or ''
    vk_url = 'vk.com/{}'.format(vk_id) if vk_id else ''
    email = extra.get('email') or user.email or ''

    profile, created = Profile.objects.get_or_create(
        user=user,
        defaults={
            'first_name': first_name,
            'last_name': last_name,
            'avatar_url': avatar_url,
            'vk_url': vk_url,
            'email': email,
        },
    )
    if created:
        return

    # Connecting a second login method must never reset user-edited profile
    # fields, timezone, Telegram, or team selection. OAuth data only fills
    # values that are still empty.
    updates = []
    for field, value in (
        ('first_name', first_name),
        ('last_name', last_name),
        ('avatar_url', avatar_url),
        ('vk_url', vk_url),
        ('email', email),
    ):
        if value and not getattr(profile, field):
            setattr(profile, field, value)
            updates.append(field)
    if updates:
        profile.save(update_fields=updates)


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


@receiver(
    post_save,
    sender=Game,
    dispatch_uid='daily-difficulty-publish-schedule-sync',
)
def daily_difficulty_publish_schedule_saved(sender, instance, created, **kwargs):
    """Move difficulty deadlines atomically with a daily schedule change."""
    if created:
        return
    from games.daily_section import schedule_for
    from games.difficulty import SUPPORTED_GAME_IDS, sync_daily_difficulty_schedule

    if instance.pk not in SUPPORTED_GAME_IDS:
        return
    schedule = schedule_for(instance.pk)
    old = getattr(instance, '_game_old_snapshot', None)
    if schedule is None or old is None:
        return
    old_tags = old.tags or {}
    new_tags = instance.tags or {}
    if old_tags.get(schedule.publish_start_tag) == new_tags.get(schedule.publish_start_tag):
        return
    sync_daily_difficulty_schedule(instance)


post_save.connect(create_profile, sender=SocialAccount, dispatch_uid="socialaccount-profilecreation-signal")


def sync_telegram_identity(sender, **kw):
    """Keep the legacy verified Telegram identity useful for OIDC login."""
    account = kw["instance"]
    if account.provider != "telegram":
        return
    try:
        profile = account.user.profile
    except Profile.DoesNotExist:
        return
    extra = account.extra_data or {}
    updates = []
    from games.telegram_oidc import telegram_user_id_from_claims

    telegram_user_id = telegram_user_id_from_claims(extra)
    old_oidc_uid = None
    try:
        old_oidc_uid = int(account.uid)
    except (TypeError, ValueError):
        pass
    if telegram_user_id is not None and (
        profile.telegram_user_id is None
        or (
            profile.telegram_user_id == old_oidc_uid
            and profile.telegram_user_id != telegram_user_id
        )
    ):
        profile.telegram_user_id = telegram_user_id
        profile.telegram_verified = True
        updates.extend(["telegram_user_id", "telegram_verified"])
    if extra.get("preferred_username") and not profile.telegram_username:
        profile.telegram_username = str(extra["preferred_username"])[:64]
        updates.append("telegram_username")
    if extra.get("preferred_username") and not profile.telegram_handle:
        profile.telegram_handle = str(extra["preferred_username"])[:64]
        updates.append("telegram_handle")
    if updates:
        profile.save(update_fields=updates)


post_save.connect(sync_telegram_identity, sender=SocialAccount, dispatch_uid="telegram-socialaccount-identity-sync")
# post_save.connect(recheck_after_saving_wall_attempt, sender=Attempt, dispatch_uid="wallattempt-recheck-signal")
# post_save.connect(recheck_after_saving_wall_task, sender=Task, dispatch_uid="walltask-recheck-signal")


@receiver(user_signed_up)
def analytics_user_signed_up(request, user, sociallogin=None, **kwargs):
    method = 'email'
    try:
        if sociallogin is not None and getattr(sociallogin, 'account', None) is not None:
            method = sociallogin.account.provider or method
    except Exception:
        pass
    state, _ = create_or_reread_analytics_row(
        PlayerAnalyticsState,
        lookup={'user': user, 'team': None, 'anon_key': None},
    )
    if state.signup_at is None:
        signup_at = timezone.now()
        PlayerAnalyticsState.objects.filter(
            pk=state.pk,
            signup_at__isnull=True,
        ).update(
            signup_at=signup_at,
            signup_method=method,
            updated_at=signup_at,
        )
    # signup_at and signup_method form one provenance bundle. A later signal
    # may neither replace the first timestamp nor attach its method to it.
    state.refresh_from_db()
    payload = signup_goal_payload(state)
    if payload is None:
        return
    queue_pending_goal(
        request,
        payload['goal'],
        params=payload['params'],
        key=payload['key'],
        ack=payload['ack'],
    )


def _mark_daily_difficulty_changed(*, task_id=None, game_id=None, task_group_id=None):
    from games.difficulty import mark_game_difficulty_changed
    mark_game_difficulty_changed(
        task_id=task_id,
        game_id=game_id,
        task_group_id=task_group_id,
    )


@receiver(post_save, sender=Attempt, dispatch_uid='daily-difficulty-attempt-dirty')
def daily_difficulty_attempt_saved(sender, instance, **kwargs):
    if not instance.task_id:
        return
    _mark_daily_difficulty_changed(
        task_id=instance.task_id,
        game_id=instance.game_id,
    )


@receiver(post_save, sender=HintAttempt, dispatch_uid='daily-difficulty-hint-dirty')
def daily_difficulty_hint_saved(sender, instance, **kwargs):
    if not instance.is_real_request:
        return
    if instance.hint_id and instance.hint and instance.hint.task_id:
        _mark_daily_difficulty_changed(task_id=instance.hint.task_id)


@receiver(post_save, sender=ChainTaskState, dispatch_uid='daily-difficulty-state-dirty')
def daily_difficulty_state_saved(sender, instance, **kwargs):
    if instance.task_id:
        _mark_daily_difficulty_changed(
            task_id=instance.task_id,
            game_id=instance.game_id,
        )


@receiver(post_save, sender=Task, dispatch_uid='daily-difficulty-task-dirty')
def daily_difficulty_task_saved(sender, instance, created, **kwargs):
    if created or not instance.task_group_id:
        return
    _mark_daily_difficulty_changed(task_group_id=instance.task_group_id)


@receiver(post_save, sender=GameTaskGroup, dispatch_uid='daily-difficulty-placement-ensure')
def daily_difficulty_placement_saved(sender, instance, **kwargs):
    from games.difficulty import SUPPORTED_GAME_IDS, ensure_daily_difficulty_row
    if instance.game_id in SUPPORTED_GAME_IDS:
        ensure_daily_difficulty_row(instance)
