"""«Only friends / only my team» filters for results tables.

A scope narrows the visible rows but never re-ranks them: places stay the
global ones, so «#57 of 1200» keeps meaning the same thing with a filter on.

Scopes are identified in the query string as ``scope=friends`` or
``scope=team:<public_id>`` and resolve to a set of results-actor identities
``(actor_type, actor_key)`` matching ``DailyResultProjection``.
"""

from dataclasses import dataclass

from games.models import Profile, ProfileTeamMembership, Team

SCOPE_PARAM = 'scope'
# The last choice is remembered in a cookie (set by the chips' script) and
# applies when the URL has no ``scope``; ``scope=all`` clears it explicitly.
SCOPE_COOKIE = 'results_scope'
ALL = 'all'
FRIENDS = 'friends'
TEAM_PREFIX = 'team:'


def selected_value(request):
    raw = request.GET.get(SCOPE_PARAM)
    if raw is None:
        raw = request.COOKIES.get(SCOPE_COOKIE, '')
    raw = (raw or '').strip()
    return '' if raw == ALL else raw


@dataclass(frozen=True)
class ResultsScope:
    key: str
    label: str
    identities: frozenset

    def contains(self, actor):
        identity = actor_identity(actor)
        return identity is not None and identity in self.identities


def actor_identity(actor):
    """``(actor_type, actor_key)`` for a results row actor, or None for anons."""
    if getattr(actor, 'is_team_results_row', False):
        return ('team', str(actor.pk))
    user_id = getattr(actor, 'user_id', None)
    if user_id is not None:
        return ('user', str(user_id))
    return None


def _member_teams(user):
    return list(
        Team.objects.filter(member_links__profile__user=user)
        .exclude(public_id=None).order_by('visible_name', 'name').distinct()
    )


def _team_identities(team):
    member_ids = ProfileTeamMembership.objects.filter(team=team).values_list('profile__user_id', flat=True)
    return frozenset({('team', str(team.pk))} | {('user', str(uid)) for uid in member_ids})


def resolve(request):
    """The scope selected in the request, or None (all rows)."""
    raw = selected_value(request)
    user = request.user
    if not raw or not getattr(user, 'is_authenticated', False):
        return None
    if raw == FRIENDS:
        from games.community.friends import friend_user_ids

        ids = friend_user_ids(user.pk) | {user.pk}
        return ResultsScope(FRIENDS, 'Друзья', frozenset(('user', str(uid)) for uid in ids))
    if raw.startswith(TEAM_PREFIX):
        team = Team.objects.filter(
            public_id=raw[len(TEAM_PREFIX):], member_links__profile__user=user,
        ).first()
        if team is not None:
            return ResultsScope(raw, team.visible_name or team.name, _team_identities(team))
    return None


def scope_options(request, url_for_scope):
    """Chips for the filter row; empty for anonymous visitors.

    ``url_for_scope(value)`` builds the page URL with ``scope`` set to value.
    """
    user = request.user
    if not getattr(user, 'is_authenticated', False) or not Profile.objects.filter(user=user).exists():
        return []
    scope = resolve(request)
    current = scope.key if scope is not None else ''
    options = [
        {'label': 'Все', 'icon': 'ph-globe-simple', 'value': ALL, 'active': not current, 'url': url_for_scope(ALL)},
        {'label': 'Друзья', 'icon': 'ph-handshake', 'value': FRIENDS, 'active': current == FRIENDS, 'url': url_for_scope(FRIENDS)},
    ]
    for team in _member_teams(user):
        value = TEAM_PREFIX + team.public_id
        options.append({
            'label': team.visible_name or team.name, 'icon': 'ph-users', 'value': value,
            'active': current == value, 'url': url_for_scope(value),
        })
    return options


def query_url(request, **overrides):
    """Current path with GET params replaced; progressive-load params reset."""
    params = request.GET.copy()
    for key in ('page', 'partial', 'loaded'):
        params.pop(key, None)
    for key, value in overrides.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value
    query = params.urlencode()
    return request.path + ('?' + query if query else '')


def sql_filter(scope, column_type='actor_type', column_key='actor_key'):
    """``(sql, params)`` restricting rows to the scope's identities."""
    by_type = {}
    for actor_type, actor_key in scope.identities:
        by_type.setdefault(actor_type, []).append(actor_key)
    if not by_type:
        return '1 = 0', []
    clauses, params = [], []
    for actor_type in sorted(by_type):
        keys = sorted(by_type[actor_type])
        clauses.append('({} = %s AND {} IN ({}))'.format(column_type, column_key, ', '.join(['%s'] * len(keys))))
        params.extend([actor_type, *keys])
    return '(' + ' OR '.join(clauses) + ')', params
