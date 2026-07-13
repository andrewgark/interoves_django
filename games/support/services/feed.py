from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional

from django.utils import timezone


@dataclass(frozen=True)
class FeedItem:
    time: object
    kind: str
    actor_kind: str
    actor_label: str
    actor_url: str
    game_id: Optional[str]
    game_url: Optional[str]
    task_id: Optional[int]
    task_number: Optional[str]
    task_group_label: Optional[str]
    status: Optional[str]
    points: Optional[str]
    detail: str
    object_id: int
    chain_url: Optional[str] = None


def preview_text(text: str, *, max_len: int = 100) -> str:
    raw = (text or '').replace('\n', ' ').strip()
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 1] + '…'


def actor_label_for_attempt(attempt) -> str:
    if attempt.team_id:
        team = attempt.team
        return (team.visible_name or team.name) if team else attempt.team_id
    if attempt.user_id:
        user = attempt.user
        if user and hasattr(user, 'profile') and user.profile:
            return '{} {}'.format(user.profile.first_name, user.profile.last_name).strip() or user.username
        return user.username if user else 'user#{}'.format(attempt.user_id)
    if attempt.anon_key:
        tail = attempt.anon_key[-8:] if len(attempt.anon_key) >= 8 else attempt.anon_key
        return 'Аноним ··{}'.format(tail)
    return '—'


def actor_kind_for_attempt(attempt) -> str:
    if attempt.team_id:
        return 'team'
    if attempt.user_id:
        return 'user'
    if attempt.anon_key:
        return 'anon'
    return 'unknown'


def actor_url_for_attempt(attempt) -> str:
    from games.support.services.search import _anon_url, _team_url, _user_url

    if attempt.team_id:
        return _team_url(attempt.team_id)
    if attempt.user_id:
        return _user_url(attempt.user_id)
    if attempt.anon_key:
        return _anon_url(attempt.anon_key)
    from django.urls import reverse
    return reverse('support:hub')


def actor_label_for_hint_attempt(ha) -> str:
    if ha.team_id:
        team = ha.team
        return (team.visible_name or team.name) if team else ha.team_id
    if ha.user_id:
        user = ha.user
        if user and hasattr(user, 'profile') and user.profile:
            return '{} {}'.format(user.profile.first_name, user.profile.last_name).strip() or user.username
        return user.username if user else 'user#{}'.format(ha.user_id)
    if ha.anon_key:
        tail = ha.anon_key[-8:] if len(ha.anon_key) >= 8 else ha.anon_key
        return 'Аноним ··{}'.format(tail)
    return '—'


def actor_kind_for_hint_attempt(ha) -> str:
    if ha.team_id:
        return 'team'
    if ha.user_id:
        return 'user'
    if ha.anon_key:
        return 'anon'
    return 'unknown'


def actor_url_for_hint_attempt(ha) -> str:
    from games.support.services.search import _anon_url, _team_url, _user_url

    if ha.team_id:
        return _team_url(ha.team_id)
    if ha.user_id:
        return _user_url(ha.user_id)
    if ha.anon_key:
        return _anon_url(ha.anon_key)
    from django.urls import reverse
    return reverse('support:hub')


def _game_url(game_id: Optional[str]) -> Optional[str]:
    if not game_id:
        return None
    from games.support.services.search import _game_url as url_for_game
    return url_for_game(game_id)


def _actor_attempt_filter(*, team=None, user=None, anon_key=None):
    from games.models import Attempt

    qs = Attempt.manager.all()
    if team is not None:
        return qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
    if user is not None:
        return qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
    if anon_key:
        return qs.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
    return qs


def _actor_hint_filter(*, team=None, user=None, anon_key=None):
    from games.models import HintAttempt

    qs = HintAttempt.objects.all()
    if team is not None:
        return qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
    if user is not None:
        return qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
    if anon_key:
        return qs.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
    return qs


def _task_ids_for_game(game_id: str):
    from games.models import Task

    return Task.objects.filter(task_group__game_links__game_id=game_id).values_list('id', flat=True)


def get_activity_feed(
    *,
    team=None,
    user=None,
    anon_key=None,
    game_id: Optional[str] = None,
    kind: str = 'all',
    status: Optional[str] = None,
    hours: Optional[int] = None,
    limit: int = 100,
) -> List[FeedItem]:
    from games.models import Attempt, HintAttempt

    since = None
    if hours:
        since = timezone.now() - timedelta(hours=hours)

    items: List[FeedItem] = []

    if kind in ('all', 'attempts'):
        attempt_qs = _actor_attempt_filter(team=team, user=user, anon_key=anon_key)
        if game_id:
            attempt_qs = Attempt.manager.filter(game_id=game_id)
            if team is not None:
                attempt_qs = attempt_qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
            elif user is not None:
                attempt_qs = attempt_qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
            elif anon_key:
                attempt_qs = attempt_qs.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
        if status:
            attempt_qs = attempt_qs.filter(status=status)
        if since:
            attempt_qs = attempt_qs.filter(time__gte=since)
        attempt_qs = attempt_qs.select_related('team', 'user', 'user__profile', 'task', 'task__task_group', 'game')
        attempt_qs = attempt_qs.order_by('-time')[:limit]

        for attempt in attempt_qs:
            task = attempt.task
            chain_url = None
            if task and task.task_type in ('wall', 'replacements_lines', 'raddle'):
                from django.urls import reverse
                chain_url = reverse('support:chain', kwargs={'attempt_id': attempt.pk})
            items.append(FeedItem(
                time=attempt.time,
                kind='attempt',
                actor_kind=actor_kind_for_attempt(attempt),
                actor_label=actor_label_for_attempt(attempt),
                actor_url=actor_url_for_attempt(attempt),
                game_id=attempt.game_id,
                game_url=_game_url(attempt.game_id),
                task_id=attempt.task_id,
                task_number=task.number if task else None,
                task_group_label=(task.task_group.label if task and task.task_group else None),
                status=attempt.status,
                points=str(attempt.points) if attempt.points is not None else None,
                detail=preview_text(attempt.text),
                object_id=attempt.pk,
                chain_url=chain_url,
            ))

    if kind in ('all', 'hints'):
        hint_qs = _actor_hint_filter(team=team, user=user, anon_key=anon_key)
        if game_id:
            task_ids = list(_task_ids_for_game(game_id))
            hint_qs = HintAttempt.objects.filter(hint__task_id__in=task_ids)
            if team is not None:
                hint_qs = hint_qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
            elif user is not None:
                hint_qs = hint_qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
            elif anon_key:
                hint_qs = hint_qs.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
        if since:
            hint_qs = hint_qs.filter(time__gte=since)
        hint_qs = hint_qs.select_related(
            'team', 'user', 'user__profile', 'hint', 'hint__task', 'hint__task__task_group',
        )
        hint_qs = hint_qs.order_by('-time')[:limit]

        for ha in hint_qs:
            hint = ha.hint
            task = hint.task if hint else None
            penalty = ''
            if hint and hint.points_penalty:
                penalty = '−{}'.format(hint.points_penalty)
            real = '' if ha.is_real_request else ' (не реальный запрос)'
            items.append(FeedItem(
                time=ha.time,
                kind='hint',
                actor_kind=actor_kind_for_hint_attempt(ha),
                actor_label=actor_label_for_hint_attempt(ha),
                actor_url=actor_url_for_hint_attempt(ha),
                game_id=game_id,
                game_url=_game_url(game_id),
                task_id=task.pk if task else None,
                task_number=task.number if task else None,
                task_group_label=(task.task_group.label if task and task.task_group else None),
                status='Hint',
                points=penalty or None,
                detail='Подсказка #{}{}'.format(hint.number if hint else '?', real),
                object_id=ha.pk,
            ))

    items.sort(key=lambda item: item.time or timezone.now(), reverse=True)
    return items[:limit]


def distinct_game_ids_for_actor(*, team=None, user=None, anon_key=None) -> List[str]:
    from games.models import Attempt

    qs = _actor_attempt_filter(team=team, user=user, anon_key=anon_key)
    return list(
        qs.exclude(game_id__isnull=True)
        .values_list('game_id', flat=True)
        .distinct()
        .order_by('-game_id')
    )
