"""Personal notifications about tournament games a player's team registered for.

The «Игры» group is opt-in (off by default on both channels): the public
channel and game chats already announce these moments. The minute sweep
(``sweep``) sends «starts in an hour» and «results»; registration and
organiser broadcasts call ``notify_registered`` directly.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger('application')

STARTS_SOON = timedelta(hours=1)
# Results wait for pending checks; after this a stale game is left alone.
RESULTS_WINDOW = timedelta(hours=6)
RESULTS_DELAY = timedelta(minutes=5)
MARKER_TTL = 12 * 3600


def team_member_ids(team) -> list[int]:
    from games.notifications.prefs import with_any_channel

    return with_any_channel('games', team.roster_profiles.values_list('user_id', flat=True))


def registered_member_ids(game) -> list[int]:
    from games.models import Profile, Registration
    from games.notifications.prefs import with_any_channel

    team_ids = Registration.objects.filter(game=game, team__is_hidden=False).values_list('team_id', flat=True)
    user_ids = (
        Profile.objects.filter(team_memberships__team_id__in=list(team_ids), user__is_active=True)
        .values_list('user_id', flat=True).distinct()
    )
    return with_any_channel('games', user_ids)


def notify_registered(game, kind_key: str, *, actor=None, **data) -> int:
    from games.notifications.service import notify

    sent = 0
    for user_id in registered_member_ids(game):
        if notify(user_id, kind_key, actor=actor, game_id=game.id, **data) is not None:
            sent += 1
    return sent


def notify_team_registered(team, game, *, actor=None) -> int:
    from games.notifications.service import notify

    sent = 0
    for user_id in team_member_ids(team):
        if notify(user_id, 'game.registered', actor=actor, game_id=game.id, team_id=team.pk) is not None:
            sent += 1
    return sent


def _eligible(qs):
    return qs.filter(is_ready=True, is_testing=False, is_tournament=True)


def _starts_soon(now) -> int:
    from games.models import Game

    sent = 0
    for game in _eligible(Game.objects.filter(start_time__gt=now, start_time__lte=now + STARTS_SOON)):
        if not cache.add('notifications:game_soon:{}'.format(game.id), 1, MARKER_TTL):
            continue
        sent += notify_registered(game, 'game.starts_soon')
    return sent


def _team_places(game) -> dict:
    """{team_pk: (place, teams_count)} from the tournament results table."""
    from games.site.pages.results.compute import _load_game_results_data

    data = _load_game_results_data(game, 'tournament')
    places = data.get('team_to_place') or {}
    total = len(data.get('teams_sorted') or places)
    out = {}
    for team, place in places.items():
        pk = getattr(team, 'pk', None)
        if pk is not None and place:
            out[pk] = (int(place), total)
    return out


def _results(now) -> int:
    from games.models import Game, Profile, Registration
    from games.notifications.prefs import with_any_channel
    from games.notifications.service import notify
    from games.ops.telegram.channels.scheduling import _game_has_pending_attempts

    sent = 0
    games = _eligible(Game.objects.filter(end_time__lte=now - RESULTS_DELAY, end_time__gt=now - RESULTS_WINDOW))
    for game in games:
        marker = 'notifications:game_results:{}'.format(game.id)
        if cache.get(marker):
            continue
        if not Registration.objects.filter(game=game).exists():
            cache.set(marker, 1, MARKER_TTL)
            continue
        if _game_has_pending_attempts(game):
            continue
        places = _team_places(game)
        cache.set(marker, 1, MARKER_TTL)
        for reg in Registration.objects.filter(game=game, team__is_hidden=False).select_related('team'):
            place, total = places.get(reg.team_id, (None, None))
            member_ids = Profile.objects.filter(
                team_memberships__team=reg.team, user__is_active=True,
            ).values_list('user_id', flat=True)
            for user_id in with_any_channel('games', member_ids):
                if notify(
                    user_id, 'game.results', game_id=game.id, team_id=reg.team_id, place=place, teams=total,
                ) is not None:
                    sent += 1
    return sent


def _offers_published(now) -> int:
    """Tell authors of accepted offers when their edition goes live."""
    from django.utils.module_loading import import_string

    from games.notifications.catalog import OFFER_GAMES
    from games.notifications.daily import current_edition
    from games.notifications.service import notify

    sent = 0
    for game_id, spec in OFFER_GAMES.items():
        edition = current_edition(game_id, now)
        if edition is None:
            continue
        marker = 'notifications:offer_published:{}:{}'.format(game_id, edition.number)
        if cache.get(marker):
            continue
        model = import_string(spec[0])
        offers = model.objects.filter(
            status='accepted', accepted_link__game_id=game_id, accepted_link__number=str(edition.number),
        )
        for offer in offers:
            if offer.user_id and notify(
                offer.user_id, 'offer.published', game=game_id, offer_id=offer.pk, number=edition.number,
            ) is not None:
                sent += 1
        cache.set(marker, 1, 2 * 24 * 3600)
    return sent


def sweep(*, now=None) -> dict:
    now = now or timezone.now()
    stats = {}
    for name, step in (('starts_soon', _starts_soon), ('results', _results), ('offers_published', _offers_published)):
        try:
            stats[name] = step(now)
        except Exception:
            logger.exception('event=game_notifications_failed step=%s', name)
            stats[name] = -1
    return stats
