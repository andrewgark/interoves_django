"""«Друзья сегодня» on the main page: which friends already played today."""

from django.db.models import Q

from games.community import visibility
from games.community.friends import friend_user_ids
from games.models import GameTaskGroup, PlayerCompletedGame, Profile

SHOWN_FRIENDS_PER_GAME = 4


def friends_today(user, daily_cards):
    """Rows ``{title, friends, more, results_url}`` for today's releases.

    ``daily_cards`` are the hub cards; each carries the game and the number
    of its current release (``cta_number``). Only personal play counts: a
    team result is not a friend's own.
    """
    if not getattr(user, 'is_authenticated', False):
        return []
    friend_ids = friend_user_ids(user.pk)
    current = [card for card in daily_cards if card.get('game') is not None and card.get('cta_number')]
    if not friend_ids or not current:
        return []

    release_filter = Q()
    for card in current:
        release_filter |= Q(game_id=card['game'].pk, number=str(card['cta_number']))
    group_by_game = dict(GameTaskGroup.objects.filter(release_filter).values_list('game_id', 'task_group_id'))
    if not group_by_game:
        return []

    played_filter = Q()
    for game_id, group_id in group_by_game.items():
        played_filter |= Q(game_id=game_id, task_group_id=group_id)
    played = {}
    for game_id, user_id in (
        PlayerCompletedGame.objects.filter(played_filter, user_id__in=friend_ids, team__isnull=True)
        .order_by('completed_at').values_list('game_id', 'user_id')
    ):
        played.setdefault(str(game_id), [])
        if user_id not in played[str(game_id)]:
            played[str(game_id)].append(user_id)
    if not played:
        return []

    shown_ids = {uid for ids in played.values() for uid in ids[:SHOWN_FRIENDS_PER_GAME]}
    identities = {
        profile.user_id: visibility.public_identity(profile, user, page_visible=True)
        for profile in Profile.objects.filter(user_id__in=shown_ids, user__is_active=True)
    }
    from games.task_group.navigation import results_url_for_task_group

    rows = []
    for card in current:
        game = card['game']
        ids = played.get(str(game.pk), [])
        people = [identities[uid] for uid in ids[:SHOWN_FRIENDS_PER_GAME] if uid in identities]
        if not people:
            continue
        rows.append({
            # The card title is the plural section name («Лесенки»); the row
            # talks about one release, so use the game's own name.
            'title': game.get_no_html_name() or card.get('title') or '',
            'friends': people,
            'more': max(0, len(ids) - len(people)),
            'results_url': results_url_for_task_group(game, card['cta_number']) + '?scope=friends',
        })
    return rows
