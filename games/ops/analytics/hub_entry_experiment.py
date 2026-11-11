"""Hub A/B test: today's puzzle vs a newcomer-suited one for first-time players.

Ads and /start/ already send newcomers to ``/<game>/first/`` (a free ~3★
puzzle, games.daily.entry), but the hub — where most organic newcomers land —
still links today's puzzles, which may be 5★. A first game finished matters:
newcomers who finished their first game returned the next week about 2.4×
as often (36% vs 15%, Sep 21 – Oct 26, 2026); 64% of unfinished first games
were left within 30 seconds of active play.

First-time players see the hub in an arm that is a hash of their browser
or account id; the arm is kept in a signed cookie so the page stays the same:

- ``today``: the hub as before (control);
- ``first``: the onboarding banner and the daily cards link ``/<game>/first/``.

Nothing is written on a hub view: bots and visitors who leave never become
rows (the first version enrolled on view and got ~3,200 "players" in a day
and a half, 19 of whom started a game). A player is enrolled when their very
first game starts and the cookie is present: ``hub_entry_variant`` and
``hub_entry_viewed_at`` (the first hub view) go onto their
``PlayerAnalyticsState`` and are carried through sign-up. Results:
``manage.py report_hub_entry_experiment`` and the admin Telegram report.
"""

import contextvars
import hashlib
import logging
from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core import signing
from django.utils import timezone

from games.models import PlayerAnalyticsState
from games.ops.analytics.persistence import create_or_reread_analytics_row

logger = logging.getLogger('games.analytics.hub_entry')

HUB_ENTRY_VARIANTS = ('today', 'first')
CONTROL_VARIANT = 'today'
HUB_ENTRY_COOKIE_NAME = 'interoves_hub_entry'
HUB_ENTRY_COOKIE_MAX_AGE = 31622400
_SIGNING_SALT = 'interoves.hub_entry.v1'

_current_entry: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    'interoves_hub_entry', default=None,
)


def _seed_variant(seed: str) -> str:
    digest = hashlib.sha256(('hub-entry:' + seed).encode('utf-8')).hexdigest()
    return HUB_ENTRY_VARIANTS[int(digest, 16) % len(HUB_ENTRY_VARIANTS)]


def _read_cookie(request) -> dict | None:
    raw = (getattr(request, 'COOKIES', None) or {}).get(HUB_ENTRY_COOKIE_NAME)
    if not raw:
        return None
    try:
        value = signing.loads(raw, salt=_SIGNING_SALT)
    except signing.BadSignature:
        return None
    if not isinstance(value, dict) or value.get('v') not in HUB_ENTRY_VARIANTS:
        return None
    return value


def hub_entry_arm(request, *, user=None, anon_key=None) -> tuple[str, bool]:
    """(arm shown to this first-time visitor, whether the cookie must be set). No writes."""
    entry = _read_cookie(request)
    if entry is not None:
        return entry['v'], False
    if user is not None and getattr(user, 'is_authenticated', False):
        seed = f'user:{user.pk}'
    elif anon_key:
        seed = f'anon:{anon_key}'
    else:
        return CONTROL_VARIANT, False
    return _seed_variant(seed), True


def set_hub_entry_cookie(response, variant: str) -> None:
    value = {'v': variant, 'at': int(datetime.now(dt_timezone.utc).timestamp())}
    response.set_cookie(
        HUB_ENTRY_COOKIE_NAME,
        signing.dumps(value, salt=_SIGNING_SALT),
        max_age=HUB_ENTRY_COOKIE_MAX_AGE,
        path='/',
        samesite='Lax',
        secure=bool(getattr(settings, 'SESSION_COOKIE_SECURE', False)),
        httponly=True,
    )


def bind_hub_entry(request):
    """Expose this request's hub arm cookie to analytics writes; returns the token."""
    try:
        entry = _read_cookie(request)
    except Exception:
        logger.exception('hub entry cookie read failed')
        entry = None
    return _current_entry.set(entry)


def release_hub_entry(token) -> None:
    _current_entry.reset(token)


def record_hub_entry(actor: dict, *, started_game) -> None:
    """Enroll a player whose first game just started after seeing the hub. Never raises."""
    entry = _current_entry.get()
    if not entry or actor.get('team') is not None:
        return
    from django.db import transaction

    from games.models import PlayerStartedGame

    try:
        with transaction.atomic():
            if PlayerStartedGame.objects.filter(**actor).exclude(pk=started_game.pk).exists():
                return
            state, _ = create_or_reread_analytics_row(PlayerAnalyticsState, lookup=actor)
            try:
                seen_at = datetime.fromtimestamp(int(entry['at']), tz=dt_timezone.utc)
            except (KeyError, TypeError, ValueError, OverflowError):
                seen_at = timezone.now()
            PlayerAnalyticsState.objects.filter(pk=state.pk, hub_entry_variant='').update(
                hub_entry_variant=entry['v'], hub_entry_viewed_at=min(seen_at, started_game.started_at),
            )
    except Exception:
        logger.exception('hub entry enrollment failed')


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
    """Per-arm counts for enrolled players (those who began a game after the hub).

    ``players``: began a game after the first hub view (rows written by the
    first, enroll-on-view version without any start are skipped);
    ``finished_first``: finished that first game; ``returned``: began a game on
    a later Moscow day within RETURN_WINDOW_DAYS of it (counted only for
    players whose window has closed: ``return_eligible``).
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from django.db.models import Exists, OuterRef

    from games.models import PlayerCompletedGame, PlayerStartedGame

    moscow = ZoneInfo('Europe/Moscow')
    now = now or timezone.now()
    summary = {
        variant: {'players': 0, 'finished_first': 0, 'return_eligible': 0, 'returned': 0}
        for variant in HUB_ENTRY_VARIANTS
    }
    started_after_view = PlayerStartedGame.objects.filter(
        team__isnull=True, is_backfilled=False, started_at__gte=OuterRef('hub_entry_viewed_at'),
    )
    states = PlayerAnalyticsState.objects.filter(
        hub_entry_variant__in=HUB_ENTRY_VARIANTS, team__isnull=True, hub_entry_viewed_at__isnull=False,
    ).filter(
        Exists(started_after_view.filter(user_id=OuterRef('user_id'), anon_key__isnull=True))
        | Exists(started_after_view.filter(anon_key=OuterRef('anon_key'), user__isnull=True))
    )
    for state in states.iterator():
        if state.user_id:
            actor = {'user_id': state.user_id, 'team__isnull': True, 'anon_key__isnull': True}
        elif state.anon_key:
            actor = {'anon_key': state.anon_key, 'team__isnull': True, 'user__isnull': True}
        else:
            continue
        starts = list(
            PlayerStartedGame.objects.filter(
                started_at__gte=state.hub_entry_viewed_at, is_backfilled=False, **actor,
            ).order_by('started_at').values_list('game_instance_id', 'started_at')
        )
        if not starts:
            continue
        row = summary[state.hub_entry_variant]
        row['players'] += 1
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
