from games.support.services.feed import distinct_game_ids_for_actor, get_activity_feed


def build_team_context(team, *, feed_kwargs):
    from games.models import Game

    game_ids = distinct_game_ids_for_actor(team=team)
    games = list(Game.objects.filter(id__in=game_ids).order_by('-start_time'))
    return {
        'actor_kind': 'team',
        'actor_title': team.visible_name or team.name,
        'actor_subtitle': 'Команда · {}'.format(team.name),
        'team': team,
        'members': list(team.roster_profiles),
        'flags': _team_flags(team),
        'games': games,
        'feed': get_activity_feed(team=team, **feed_kwargs),
    }


def build_user_context(user, *, feed_kwargs):
    from games.models import Game

    profile = getattr(user, 'profile', None)
    game_ids = distinct_game_ids_for_actor(user=user)
    games = list(Game.objects.filter(id__in=game_ids).order_by('-start_time'))
    if profile:
        title = '{} {}'.format(profile.first_name, profile.last_name).strip()
    else:
        title = user.username
    return {
        'actor_kind': 'user',
        'actor_title': title or user.username,
        'actor_subtitle': 'User #{} · {}'.format(user.pk, user.username),
        'user': user,
        'profile': profile,
        'members': [],
        'flags': _user_flags(user, profile),
        'games': games,
        'feed': get_activity_feed(user=user, **feed_kwargs),
    }


def build_anon_context(anon_key, *, feed_kwargs):
    from games.models import Attempt, Game, HiddenAnonKey

    game_ids = distinct_game_ids_for_actor(anon_key=anon_key)
    games = list(Game.objects.filter(id__in=game_ids).order_by('-start_time'))
    first_seen = (
        Attempt.manager.filter(anon_key=anon_key)
        .order_by('time')
        .values_list('time', flat=True)
        .first()
    )
    hidden = HiddenAnonKey.objects.filter(anon_key=anon_key).first()
    tail = anon_key[-8:] if len(anon_key) >= 8 else anon_key
    return {
        'actor_kind': 'anon',
        'actor_title': 'Аноним ··{}'.format(tail),
        'actor_subtitle': anon_key,
        'anon_key': anon_key,
        'first_seen': first_seen,
        'hidden_note': hidden.note if hidden else None,
        'members': [],
        'flags': _anon_flags(hidden),
        'games': games,
        'feed': get_activity_feed(anon_key=anon_key, **feed_kwargs),
    }


def build_game_context(game, *, feed_kwargs):
    from django.urls import reverse

    from games.models import Attempt, GameTaskGroup, HintAttempt, Task

    task_ids = list(
        Task.objects.filter(task_group__game_links__game_id=game.id).values_list('id', flat=True)
    )
    pending_count = Attempt.manager.filter(game=game, status='Pending').count()
    hint_count = HintAttempt.objects.filter(hint__task_id__in=task_ids).count() if task_ids else 0
    task_groups = list(
        GameTaskGroup.objects.filter(game=game)
        .select_related('task_group')
        .order_by('number')
    )
    return {
        'game': game,
        'task_groups': task_groups,
        'pending_count': pending_count,
        'hint_count': hint_count,
        'feed': get_activity_feed(game_id=game.id, **feed_kwargs),
        'admin_game_url': reverse('admin:games_game_change', args=[game.pk]),
        'site_game_url': '/games/{}/'.format(game.id),
    }


def _team_flags(team):
    flags = []
    if team.is_tester:
        flags.append('tester')
    if team.is_hidden:
        flags.append('hidden')
    flags.append('tickets:{}'.format(team.tickets))
    return flags


def _user_flags(user, profile):
    flags = []
    if user.is_staff:
        flags.append('staff')
    if user.is_superuser:
        flags.append('superuser')
    if profile and profile.team_on_id:
        flags.append('team_on:{}'.format(profile.team_on_id))
    return flags


def _anon_flags(hidden):
    flags = []
    if hidden:
        flags.append('hidden')
    return flags
