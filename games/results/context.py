"""Helpers for results table display contexts and actor filters."""

from django.utils import timezone

from games.models import ClubSubscription, PersonalResultsParticipant


def results_column_count(task_groups, mode='general'):
    fixed_columns = 4 if mode == 'tournament' else 3
    return fixed_columns + sum(
        group.get_n_tasks_for_results() for group in (task_groups or [])
    )


def empty_results_rows_context():
    return {
        'teams_sorted': [],
        'team_to_list_attempts_info': {},
        'team_to_cells': {},
        'team_to_score': {},
        'team_to_place': {},
        'team_to_max_best_time': {},
    }


def results_actor_filter_types(request):
    raw = request.GET.get('actors')
    if raw is None:
        return {'user', 'team', 'anon'}
    return {
        value.strip() for value in str(raw).split(',') if value.strip()
    } & {'user', 'team', 'anon'}


def results_actor_filter_urls(request):
    """Build links that toggle actor types while preserving other filters."""
    selected = results_actor_filter_types(request)
    definitions = (
        ('team', 'Команды', 'ph-users'),
        ('user', 'Игроки', 'ph-user'),
        ('anon', 'Анонимы', 'ph-detective'),
    )
    result = []
    for kind, label, icon in definitions:
        next_types = set(selected)
        if kind in next_types:
            next_types.remove(kind)
        else:
            next_types.add(kind)
        params = request.GET.copy()
        params.pop('page', None)
        params.pop('partial', None)
        params.pop('loaded', None)
        params['actors'] = ','.join(
            value for value in ('user', 'team', 'anon') if value in next_types
        )
        query = params.urlencode()
        result.append({
            'label': label,
            'icon': icon,
            'active': kind in selected,
            'url': request.path + ('?' + query if query else ''),
            'title': ('Скрыть ' if kind in selected else 'Показать ') + label.lower(),
        })
    return result


def results_actor_kind(actor):
    if getattr(actor, 'is_team_results_row', False):
        return 'team'
    if getattr(actor, 'anon_key', None):
        return 'anon'
    return 'user'


def results_me_participants(request, play_mode, *, anon_key_from_request):
    me_personal = None
    me_anon_participant = None
    if play_mode == 'personal':
        if request.user.is_authenticated:
            me_personal = PersonalResultsParticipant(user=request.user)
        else:
            anon_key = anon_key_from_request(request)
            if anon_key:
                me_anon_participant = PersonalResultsParticipant(anon_key=anon_key)
    return me_personal, me_anon_participant


def club_subscriber_user_ids(actors):
    user_ids = {
        actor.user_id for actor in actors
        if getattr(actor, 'user_id', None) is not None
        and not getattr(actor, 'is_team_results_row', False)
    }
    if not user_ids:
        return set()
    return set(ClubSubscription.objects.filter(
        user_id__in=user_ids,
        paid_until__gt=timezone.now(),
    ).values_list('user_id', flat=True))


def attach_results_club_badges(data):
    actors = data.get('teams_sorted') or []
    subscriber_ids = club_subscriber_user_ids(actors)
    data['team_to_club_subscriber'] = {
        actor: getattr(actor, 'user_id', None) in subscriber_ids
        for actor in actors
    }
    return data
