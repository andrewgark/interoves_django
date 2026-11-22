"""Daily games whose newest released edition the visitor has not finished.

The navigation marks these games with a blue dot: there is something new to
play. One query per page, over the hub's cached placement links.
"""


def unfinished_latest_game_ids(*, user=None, anon_key=None) -> frozenset:
    from games.models import Game, PlayerCompletedGame
    from games.sections.hub import DAILY_NAV_SECTION_IDS
    from games.site.pages.hub import link_cache
    from games.site.session.replay import actor_kwargs

    games = link_cache.cached(
        ('daily_nav_games',),
        lambda: tuple(Game.objects.filter(id__in=DAILY_NAV_SECTION_IDS)),
    )
    game_by_instance = {}
    for game in games:
        links = link_cache.visible_game_links(game)
        if links:
            # game_instance_id_for_task_group() without loading the task group.
            game_by_instance['{}:{}'.format(game.id, links[0].task_group_id)] = game.id
    if not game_by_instance:
        return frozenset()
    finished = set()
    actor = actor_kwargs(user=user, anon_key=anon_key)
    if actor is not None:
        finished = set(
            PlayerCompletedGame.objects.filter(
                game_instance_id__in=game_by_instance, **actor,
            ).values_list('game_instance_id', flat=True)
        )
    return frozenset(
        game_id for instance, game_id in game_by_instance.items() if instance not in finished
    )
