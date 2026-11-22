"""Blue «not finished yet» dots for daily editions.

A dot says there is something new to play, so it is only for players who
have finished at least one game: a first-time visitor would see one on
every edition and every game at once.
"""


def finished_instances(instance_ids, *, team=None, user=None, anon_key=None):
    """The ``instance_ids`` this actor has finished, or None for a newcomer.

    None means: no identity, or no finished game at all yet; show no dots.
    One query for a returning player, a second only when none of the given
    editions is finished.
    """
    from games.models import PlayerCompletedGame
    from games.site.session.replay import actor_kwargs

    actor = actor_kwargs(team=team, user=user, anon_key=anon_key)
    if actor is None:
        return None
    completed = PlayerCompletedGame.objects.filter(**actor)
    finished = set(
        completed.filter(game_instance_id__in=list(instance_ids))
        .values_list('game_instance_id', flat=True)
    )
    if not finished and not completed.exists():
        return None
    return finished


def request_actor(request) -> dict:
    """The daily-game actor of a request: the logged-in user, else the browser's anonymous key.

    The same rule as games.site.views.daily.common.resolve_actor.
    """
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return {'user': user}
    from games.ops.analytics.identity import gameplay_anon_key
    return {'anon_key': gameplay_anon_key(request)}


def instance_id(game, placement) -> str:
    """game_instance_id_for_task_group() without loading the task group."""
    return '{}:{}'.format(game.id, placement.task_group_id)


def unfinished_placements(game, placements, *, team=None, user=None, anon_key=None) -> set:
    """Pks of the given placements this actor has not finished.

    Empty for a newcomer, so the dots stay hidden until a first finished game.
    """
    placements = [placement for placement in placements if placement is not None]
    if not placements:
        return set()
    finished = finished_instances(
        {instance_id(game, placement) for placement in placements},
        team=team, user=user, anon_key=anon_key,
    )
    if finished is None:
        return set()
    return {
        placement.pk for placement in placements
        if instance_id(game, placement) not in finished
    }


def unfinished_latest_game_ids(*, user=None, anon_key=None) -> frozenset:
    """Daily games whose newest released edition this player has not finished.

    One query per page over the hub's cached placement links.
    """
    from games.models import Game
    from games.sections.hub import DAILY_NAV_SECTION_IDS
    from games.site.pages.hub import link_cache

    games = link_cache.cached(
        ('daily_nav_games',),
        lambda: tuple(Game.objects.filter(id__in=DAILY_NAV_SECTION_IDS)),
    )
    game_by_instance = {}
    for game in games:
        links = link_cache.visible_game_links(game)
        if links:
            game_by_instance[instance_id(game, links[0])] = game.id
    if not game_by_instance:
        return frozenset()
    finished = finished_instances(game_by_instance, user=user, anon_key=anon_key)
    if finished is None:
        return frozenset()
    return frozenset(
        game_id for instance, game_id in game_by_instance.items() if instance not in finished
    )
