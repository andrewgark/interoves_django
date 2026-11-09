"""Hub A/B test: today's puzzle vs a newcomer-suited one for first-time players.

Ads and /start/ already send newcomers to ``/<game>/first/`` (a free ~3★
puzzle, games.daily.entry), but the hub — where most organic newcomers land —
still links today's puzzles, which may be 5★. A first game finished matters:
newcomers who finished their first game returned the next week about 2.4×
as often (36% vs 15%, Sep 21 – Oct 26, 2026); 64% of unfinished first games
were left within 30 seconds of active play.

First-time players are split on their first hub view and keep their arm:

- ``today``: the hub as before (control);
- ``first``: the onboarding banner and the daily cards link ``/<game>/first/``.

The arm is stored on ``PlayerAnalyticsState.hub_entry_variant`` and carried
through sign-up, so finishing and return rates per arm are compared on
backend rows (``manage.py report_hub_entry_experiment``).
"""

import hashlib

from django.utils import timezone

from games.models import PlayerAnalyticsState
from games.ops.analytics.persistence import create_or_reread_analytics_row

HUB_ENTRY_VARIANTS = ('today', 'first')
CONTROL_VARIANT = 'today'


def _seed_variant(seed: str) -> str:
    digest = hashlib.sha256(('hub-entry:' + seed).encode('utf-8')).hexdigest()
    return HUB_ENTRY_VARIANTS[int(digest, 16) % len(HUB_ENTRY_VARIANTS)]


def hub_entry_variant(*, user=None, anon_key=None) -> str:
    """The first-time player's arm; assigned and stored on the first call."""
    if user is not None and getattr(user, 'is_authenticated', False):
        lookup = {'team': None, 'user': user, 'anon_key': None}
        seed = f'user:{user.pk}'
    elif anon_key:
        lookup = {'team': None, 'user': None, 'anon_key': str(anon_key)}
        seed = f'anon:{anon_key}'
    else:
        return CONTROL_VARIANT
    state, _ = create_or_reread_analytics_row(PlayerAnalyticsState, lookup=lookup)
    if state.hub_entry_variant in HUB_ENTRY_VARIANTS:
        return state.hub_entry_variant
    variant = _seed_variant(seed)
    PlayerAnalyticsState.objects.filter(pk=state.pk, hub_entry_variant='').update(
        hub_entry_variant=variant, hub_entry_viewed_at=timezone.now(),
    )
    state.refresh_from_db(fields=['hub_entry_variant'])
    return state.hub_entry_variant or variant


def apply_newcomer_entry_links(daily_hub_cards) -> None:
    """Point the hub's daily cards at the newcomer puzzle instead of today's."""
    from games.daily.entry import NEWCOMER_GAME_IDS
    from games.sections.hub import ONBOARDING_GAME_META
    from games.sections.paths import section_first_path

    for card in daily_hub_cards:
        game_id = card.get('id')
        if game_id not in NEWCOMER_GAME_IDS or not card.get('play_url'):
            continue
        card['play_url'] = section_first_path(game_id)
        card['cta_label'] = (ONBOARDING_GAME_META.get(game_id) or {}).get('cta_label') or card.get('cta_label')
        # The card no longer points at today's number: no "today" styling and
        # no solved mark/progress fetched for it.
        card['is_today'] = False
        card['cta_number'] = None
        card['is_solved'] = False
        card['load_task_group_progress'] = False
        card['progress_embedded'] = False


RETURN_WINDOW_DAYS = 7


def experiment_summary(*, now=None) -> dict:
    """Per-arm counts for first-time players assigned on the hub.

    ``started``: began any game after the first hub view; ``finished_first``:
    finished the first game begun after it; ``returned``: began a game on a
    later Moscow day within RETURN_WINDOW_DAYS of that first start (counted
    only for players whose window has closed: ``return_eligible``).
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from games.models import PlayerCompletedGame, PlayerStartedGame

    moscow = ZoneInfo('Europe/Moscow')
    now = now or timezone.now()
    summary = {
        variant: {'assigned': 0, 'started': 0, 'finished_first': 0, 'return_eligible': 0, 'returned': 0}
        for variant in HUB_ENTRY_VARIANTS
    }
    states = PlayerAnalyticsState.objects.filter(
        hub_entry_variant__in=HUB_ENTRY_VARIANTS, team__isnull=True,
    ).exclude(hub_entry_viewed_at=None)
    for state in states.iterator():
        if state.user_id:
            actor = {'user_id': state.user_id, 'team__isnull': True, 'anon_key__isnull': True}
        elif state.anon_key:
            actor = {'anon_key': state.anon_key, 'team__isnull': True, 'user__isnull': True}
        else:
            continue
        row = summary[state.hub_entry_variant]
        row['assigned'] += 1
        starts = list(
            PlayerStartedGame.objects.filter(
                started_at__gte=state.hub_entry_viewed_at, is_backfilled=False, **actor,
            ).order_by('started_at').values_list('game_instance_id', 'started_at')
        )
        if not starts:
            continue
        row['started'] += 1
        first_instance, first_at = starts[0]
        if PlayerCompletedGame.objects.filter(game_instance_id=first_instance, **actor).exists():
            row['finished_first'] += 1
        if first_at + timedelta(days=RETURN_WINDOW_DAYS + 1) > now:
            continue
        row['return_eligible'] += 1
        first_day = first_at.astimezone(moscow).date()
        deadline = first_at + timedelta(days=RETURN_WINDOW_DAYS + 1)
        if any(first_day < at.astimezone(moscow).date() and at < deadline for _, at in starts[1:]):
            row['returned'] += 1
    return summary
