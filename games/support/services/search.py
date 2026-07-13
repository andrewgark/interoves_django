from dataclasses import dataclass

from django.urls import reverse


@dataclass(frozen=True)
class SearchHit:
    kind: str
    label: str
    detail: str
    url: str


def _team_url(team_name: str) -> str:
    return reverse('support:actor_team', kwargs={'team_name': team_name})


def _user_url(user_id: int) -> str:
    return reverse('support:actor_user', kwargs={'user_id': user_id})


def _anon_url(anon_key: str) -> str:
    return reverse('support:actor_anon', kwargs={'anon_key': anon_key})


def _game_url(game_id: str) -> str:
    return reverse('support:game', kwargs={'game_id': game_id})


def parse_search_query(raw: str):
    """
    Returns (kind, value) where kind is one of
    team|user|anon|game|attempt|task|auto or None for empty.
    """
    text = (raw or '').strip()
    if not text:
        return None, ''
    lower = text.lower()
    prefixes = (
        ('team:', 'team'),
        ('команда:', 'team'),
        ('user:', 'user'),
        ('юзер:', 'user'),
        ('anon:', 'anon'),
        ('анон:', 'anon'),
        ('game:', 'game'),
        ('игра:', 'game'),
        ('attempt:', 'attempt'),
        ('посылка:', 'attempt'),
        ('task:', 'task'),
        ('задание:', 'task'),
    )
    for prefix, kind in prefixes:
        if lower.startswith(prefix):
            return kind, text[len(prefix):].strip()
    if text.isdigit():
        return 'auto', text
    if '@' in text:
        return 'user', text
    return 'auto', text


def search(query: str, *, limit: int = 25):
    from django.contrib.auth.models import User
    from django.db.models import Q

    from games.models import Attempt, Game, Task, Team

    kind, value = parse_search_query(query)
    if kind is None:
        return []

    hits = []

    def add(hit: SearchHit):
        if len(hits) < limit:
            hits.append(hit)

    if kind in ('team', 'auto'):
        teams = Team.objects.filter(
            Q(name__icontains=value) | Q(visible_name__icontains=value)
        ).order_by('name')[:limit]
        for team in teams:
            add(SearchHit(
                kind='team',
                label=team.visible_name or team.name,
                detail='Команда · {}'.format(team.name),
                url=_team_url(team.name),
            ))

    if kind in ('user', 'auto') and len(hits) < limit:
        users = User.objects.filter(
            Q(username__icontains=value)
            | Q(email__icontains=value)
            | Q(profile__first_name__icontains=value)
            | Q(profile__last_name__icontains=value)
        ).select_related('profile').order_by('username')[:limit]
        if kind == 'user' and value.isdigit():
            users = User.objects.filter(pk=int(value)).select_related('profile')
        elif kind == 'user' and '@' in value:
            users = User.objects.filter(email__iexact=value).select_related('profile')
        for user in users:
            profile = getattr(user, 'profile', None)
            if profile:
                label = '{} {}'.format(profile.first_name, profile.last_name).strip()
                detail = 'User #{} · {}'.format(user.pk, user.username)
            else:
                label = user.username
                detail = 'User #{}'.format(user.pk)
            add(SearchHit(
                kind='user',
                label=label or user.username,
                detail=detail,
                url=_user_url(user.pk),
            ))

    if kind in ('anon', 'auto') and len(hits) < limit and len(value) >= 4:
        from games.models import Attempt as AttemptModel

        anon_keys = (
            AttemptModel.manager.filter(anon_key__icontains=value)
            .exclude(anon_key__isnull=True)
            .values_list('anon_key', flat=True)
            .distinct()[:limit]
        )
        for anon_key in anon_keys:
            tail = anon_key[-8:] if len(anon_key) >= 8 else anon_key
            add(SearchHit(
                kind='anon',
                label='Аноним ··{}'.format(tail),
                detail=anon_key,
                url=_anon_url(anon_key),
            ))

    if kind in ('game', 'auto') and len(hits) < limit:
        if kind == 'game':
            games = Game.objects.filter(id__iexact=value).order_by('-start_time')
        else:
            games = Game.objects.filter(
                Q(id__icontains=value) | Q(name__icontains=value) | Q(outside_name__icontains=value)
            ).order_by('-start_time')
        for game in games.distinct()[:limit]:
            add(SearchHit(
                kind='game',
                label=game.outside_name or game.name or game.id,
                detail='Игра · {}'.format(game.id),
                url=_game_url(game.id),
            ))

    if kind == 'attempt' and value.isdigit():
        attempt = Attempt.manager.filter(pk=int(value)).select_related('team', 'user', 'game', 'task').first()
        if attempt:
            add(SearchHit(
                kind='attempt',
                label='Посылка #{}'.format(attempt.pk),
                detail=_attempt_detail(attempt),
                url=_attempt_actor_url(attempt),
            ))

    if kind == 'task' and value.isdigit():
        task = Task.objects.filter(pk=int(value)).select_related('task_group').first()
        if task:
            add(SearchHit(
                kind='task',
                label='Задание #{} · {}'.format(task.pk, task.number),
                detail=task.task_group.label or str(task.task_group_id),
                url=_game_url_for_task(task),
            ))

    if kind == 'auto' and value.isdigit() and len(hits) < limit:
        attempt = Attempt.manager.filter(pk=int(value)).select_related('team', 'user', 'game', 'task').first()
        if attempt:
            add(SearchHit(
                kind='attempt',
                label='Посылка #{}'.format(attempt.pk),
                detail=_attempt_detail(attempt),
                url=_attempt_actor_url(attempt),
            ))

    return hits[:limit]


def _attempt_detail(attempt) -> str:
    parts = []
    if attempt.game_id:
        parts.append(attempt.game_id)
    if attempt.task_id:
        parts.append('task {}'.format(attempt.task.number if attempt.task else attempt.task_id))
    parts.append(attempt.status)
    return ' · '.join(parts)


def _attempt_actor_url(attempt) -> str:
    if attempt.team_id:
        return _team_url(attempt.team_id)
    if attempt.user_id:
        return _user_url(attempt.user_id)
    if attempt.anon_key:
        return _anon_url(attempt.anon_key)
    return reverse('support:hub')


def _game_url_for_task(task) -> str:
    from games.models import GameTaskGroup

    link = GameTaskGroup.objects.filter(task_group=task.task_group).select_related('game').first()
    if link:
        return _game_url(link.game_id)
    return reverse('support:hub')
