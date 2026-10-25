"""Invalidate daily statistics when public-participant eligibility changes."""

from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver

from games.models import (
    GameTaskGroup,
    HiddenAnonKey,
    PlayerCompletedGame,
    Profile,
    ProfileTeamMembership,
    TaskGroup,
    Team,
)


def _daily_game_ids():
    from games.daily.registry import DAILY_GAME_REGISTRY

    return tuple(
        definition.game_id
        for definition in DAILY_GAME_REGISTRY.all()
        if definition.capabilities.statistics
    )


def _invalidate_pairs(pairs):
    from games.daily_statistics import invalidate_daily_statistics

    for game_id, task_group_id in set(pairs):
        invalidate_daily_statistics(game_id, task_group_id)


def _actor_pairs(**actor_filter):
    return PlayerCompletedGame.objects.filter(
        result=PlayerCompletedGame.RESULT_SOLVED,
        game_id__in=_daily_game_ids(),
        **actor_filter,
    ).values_list('game_id', 'task_group_id').distinct()


def _invalidate_author_team_pairs(user_id, team_ids):
    team_ids = {team_id for team_id in team_ids if team_id}
    if not user_id or not team_ids:
        return
    _invalidate_pairs(PlayerCompletedGame.objects.filter(
        result=PlayerCompletedGame.RESULT_SOLVED,
        game_id__in=_daily_game_ids(),
        team_id__in=team_ids,
        task_group__authors__user_id=user_id,
    ).values_list('game_id', 'task_group_id').distinct())


@receiver(pre_save, sender=Profile, dispatch_uid='daily-statistics-profile-old-eligibility')
def profile_old_eligibility(sender, instance, **kwargs):
    if not instance.pk:
        instance._daily_statistics_old_eligibility = None
        return
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and not {'is_hidden', 'team_on'}.intersection(update_fields):
        instance._daily_statistics_old_eligibility = None
        return
    instance._daily_statistics_old_eligibility = Profile.objects.filter(
        pk=instance.pk,
    ).values('is_hidden', 'team_on_id').first()


@receiver(post_save, sender=Profile, dispatch_uid='daily-statistics-profile-eligibility')
def profile_eligibility_changed(sender, instance, created, **kwargs):
    old = getattr(instance, '_daily_statistics_old_eligibility', None)
    if created:
        if instance.is_hidden:
            _invalidate_pairs(_actor_pairs(user_id=instance.user_id))
        return
    if old is None:
        return
    if old['is_hidden'] != instance.is_hidden:
        _invalidate_pairs(_actor_pairs(user_id=instance.user_id))
    if old['team_on_id'] != instance.team_on_id:
        _invalidate_author_team_pairs(
            instance.user_id,
            {old['team_on_id'], instance.team_on_id},
        )


@receiver(pre_save, sender=Team, dispatch_uid='daily-statistics-team-old-eligibility')
def team_old_eligibility(sender, instance, **kwargs):
    if not instance.pk:
        instance._daily_statistics_old_hidden = None
        return
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'is_hidden' not in update_fields:
        instance._daily_statistics_old_hidden = None
        return
    instance._daily_statistics_old_hidden = Team.objects.filter(
        pk=instance.pk,
    ).values_list('is_hidden', flat=True).first()


@receiver(post_save, sender=Team, dispatch_uid='daily-statistics-team-eligibility')
def team_eligibility_changed(sender, instance, created, **kwargs):
    old_hidden = getattr(instance, '_daily_statistics_old_hidden', None)
    if not created and old_hidden is not None and old_hidden != instance.is_hidden:
        _invalidate_pairs(_actor_pairs(team_id=instance.pk))


@receiver(post_save, sender=HiddenAnonKey, dispatch_uid='daily-statistics-anon-hidden')
def hidden_anon_added(sender, instance, created, **kwargs):
    if created:
        _invalidate_pairs(_actor_pairs(anon_key=instance.anon_key))


@receiver(post_delete, sender=HiddenAnonKey, dispatch_uid='daily-statistics-anon-unhidden')
def hidden_anon_removed(sender, instance, **kwargs):
    _invalidate_pairs(_actor_pairs(anon_key=instance.anon_key))


@receiver(m2m_changed, sender=TaskGroup.authors.through, dispatch_uid='daily-statistics-task-authors')
def task_group_authors_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action == 'pre_clear':
        instance._daily_statistics_cleared_author_groups = (
            set(instance.authored_task_groups.values_list('pk', flat=True))
            if reverse else {instance.pk}
        )
        return
    if action not in {'post_add', 'post_remove', 'post_clear'}:
        return
    if reverse:
        task_group_ids = (
            set(pk_set or ()) if action != 'post_clear'
            else getattr(instance, '_daily_statistics_cleared_author_groups', set())
        )
    else:
        task_group_ids = {instance.pk}
    if not task_group_ids:
        return
    _invalidate_pairs(GameTaskGroup.objects.filter(
        game_id__in=_daily_game_ids(),
        task_group_id__in=task_group_ids,
    ).values_list('game_id', 'task_group_id'))


def _membership_changed(profile_id, team_id):
    profile = Profile.objects.filter(pk=profile_id).only('user_id').first()
    if profile is not None:
        _invalidate_author_team_pairs(profile.user_id, {team_id})


@receiver(post_save, sender=ProfileTeamMembership, dispatch_uid='daily-statistics-team-membership-added')
def membership_added(sender, instance, created, **kwargs):
    if created:
        _membership_changed(instance.profile_id, instance.team_id)


@receiver(post_delete, sender=ProfileTeamMembership, dispatch_uid='daily-statistics-team-membership-removed')
def membership_removed(sender, instance, **kwargs):
    _membership_changed(instance.profile_id, instance.team_id)
