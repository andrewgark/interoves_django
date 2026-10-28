"""October–November 2026 donation vote for the next daily game.

Voting runs 28 October–11 November 2026, Europe/Moscow. Порошки starts at
zero. Криптики and Логички keep half of their September support when they
finished strictly behind the September winner. That inherited part is shown
separately from money given in this round.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from games.club.next_game import (
    CANDIDATE_CRYPTIC,
    CANDIDATE_LOGIC,
    COUNTED_EVENT_NAME,
    DONATION_EVENTS,
    SUPPORTED_CURRENCIES,
    VotePayloadError,
    _idempotency_key,
    _parse_amount_minor,
    _parse_event_datetime,
    _payload_donation_name,
    _payload_donation_request_id,
    _payload_web_link,
    _startapp_token,
    campaign_state,
    format_eur_cents,
    fx_rate_to_eur,
    live_candidate_totals,
    original_minor_to_eur_cents,
)
from games.club.next_game import (
    CANDIDATE_META as SEPTEMBER_META,
)
from games.club.next_game import (
    CANDIDATE_SLUGS as SEPTEMBER_SLUGS,
)
from games.models import NextGameAutumnGoalMapping, NextGameAutumnVoteEvent

logger = logging.getLogger('games.next_game_autumn')

MOSCOW = ZoneInfo('Europe/Moscow')
CAMPAIGN_ID = 'next-game-2026-11'

VOTE_START = datetime(2026, 10, 28, 0, 0, 0, tzinfo=MOSCOW)
VOTE_END = datetime(2026, 11, 11, 23, 59, 59, tzinfo=MOSCOW)

PHASE_UPCOMING = 'upcoming'
PHASE_LIVE = 'live'
PHASE_CLOSED = 'closed'

CANDIDATE_POWDERS = 'powders'
CANDIDATE_CRYPTICS = 'cryptic'
CANDIDATE_LOGICS = 'logic'
CANDIDATE_SLUGS = (
    CANDIDATE_POWDERS,
    CANDIDATE_CRYPTICS,
    CANDIDATE_LOGICS,
)

# September slug → this round. Redactle has no seat here, so its money stays
# on the September page.
CARRY_FROM_SEPTEMBER = {
    CANDIDATE_CRYPTIC: CANDIDATE_CRYPTICS,
    CANDIDATE_LOGIC: CANDIDATE_LOGICS,
}
CARRY_COEFFICIENT = Decimal('0.5')

SETTINGS_BY_CANDIDATE = {
    CANDIDATE_POWDERS: 'TRIBUTE_NEXT_GAME_AUTUMN_POWDERS_URL',
    CANDIDATE_CRYPTICS: 'TRIBUTE_NEXT_GAME_AUTUMN_CRYPTIC_URL',
    CANDIDATE_LOGICS: 'TRIBUTE_NEXT_GAME_AUTUMN_LOGIC_URL',
}

def _grid(kind, size, clues):
    """Board spec for the JS: clues are (index, text, tone) on a size×size grid."""
    return json.dumps({
        'kind': kind,
        'size': size,
        'clues': [{'i': i, 't': text, 'tone': tone} for i, text, tone in clues],
    }, ensure_ascii=False)


# Pilot minis; each one was checked by brute force to have a single solution.
# Answers live in static/js/next_game_autumn_vote.js.
LOGIC_EXAMPLES = (
    {
        'key': 'logic-castle',
        'title': 'Castle Wall',
        'rules': 'castle',
        'controls': 'Ведите пальцем или мышью от центра клетки к соседней — появится отрезок петли. Проведите по отрезку ещё раз, чтобы стереть.',
        'grid': _grid('loop', 6, [
            (7, '3→', 'black'),
            (12, '', 'black'),
            (15, '1←', 'white'),
            (25, '1→', 'white'),
            (28, '1↑', 'white'),
        ]),
    },
    {
        'key': 'logic-nurikabe',
        'title': 'Nurikabe',
        'rules': 'nurikabe',
        'controls': 'Левая кнопка мыши закрашивает клетку (море), правая ставит точку (остров), повторное нажатие снимает. На телефоне нажатие переключает: закрашено → точка → пусто. Можно вести по нескольким клеткам.',
        'grid': _grid('shade', 5, [
            (0, '4', 'number'),
            (3, '1', 'number'),
            (7, '3', 'number'),
            (23, '2', 'number'),
        ]),
    },
    {
        'key': 'logic-cave',
        'title': 'Cave',
        'rules': 'cave',
        'controls': 'Левая кнопка мыши закрашивает клетку (стена), правая ставит точку (пещера), повторное нажатие снимает. На телефоне нажатие переключает: закрашено → точка → пусто. Можно вести по нескольким клеткам.',
        'grid': _grid('shade', 5, [
            (0, '4', 'number'),
            (7, '5', 'number'),
            (10, '4', 'number'),
            (11, '5', 'number'),
            (12, '7', 'number'),
            (16, '5', 'number'),
            (24, '3', 'number'),
        ]),
    },
)


CANDIDATE_META = {
    CANDIDATE_POWDERS: {
        'slug': CANDIDATE_POWDERS,
        'name': 'Порошки',
        'tag': 'Закончите стишок-порошок двумя слогами, рифмующимися со второй строкой',
        'cta': 'Поддержать Порошки',
    },
    CANDIDATE_CRYPTICS: {
        'slug': CANDIDATE_CRYPTICS,
        'name': 'Криптики',
        'tag': 'Одна хитрая словесная загадка в день',
        'cta': 'Поддержать Криптики',
    },
    CANDIDATE_LOGICS: {
        'slug': CANDIDATE_LOGICS,
        'name': 'Логички',
        'tag': 'Новая сеточная головоломка каждый день',
        'cta': 'Поддержать Логички',
    },
}


def campaign_phase(now=None) -> str:
    from games.club.next_game import campaign_now

    now = campaign_now(now)
    if now < VOTE_START:
        return PHASE_UPCOMING
    if now <= VOTE_END:
        return PHASE_LIVE
    return PHASE_CLOSED


def half_cents(cents: int) -> int:
    halved = (Decimal(int(cents)) * CARRY_COEFFICIENT).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return int(halved)


def configured_web_url(slug: str) -> str:
    name = SETTINGS_BY_CANDIDATE.get(slug, '')
    return str(getattr(settings, name, '') or '').strip()


def donation_in_voting_period(created_at) -> bool:
    if created_at is None:
        return False
    if timezone.is_naive(created_at):
        created_at = timezone.make_aware(created_at, timezone.utc)
    return VOTE_START <= created_at <= VOTE_END


def _request_id_mapping() -> dict[str, str]:
    rows = NextGameAutumnGoalMapping.objects.exclude(donation_request_id='').values_list(
        'donation_request_id', 'candidate',
    )
    return {str(request_id): slug for request_id, slug in rows if request_id and slug in CANDIDATE_SLUGS}


def _name_matches(donation_name: str, label: str) -> bool:
    folded = donation_name.casefold().strip()
    needle = label.casefold()
    if not folded or not needle:
        return False
    if folded == needle:
        return True
    if not folded.startswith(needle):
        return False
    return not folded[len(needle):len(needle) + 1].isalnum()


def _link_matches(configured: str, link: str) -> bool:
    if not configured or not link:
        return False
    if configured in link:
        return True
    token = _startapp_token(configured)
    link_token = _startapp_token(link)
    return bool(token) and (token == link_token or token in link)


def candidate_from_payload(payload: dict) -> str:
    """Resolve an autumn candidate. September titles and slugs do not match."""
    if not isinstance(payload, dict):
        return ''
    request_id = _payload_donation_request_id(payload)
    mapped = _request_id_mapping().get(request_id, '')
    if mapped:
        return mapped
    link = _payload_web_link(payload)
    if link:
        for slug in CANDIDATE_SLUGS:
            if _link_matches(configured_web_url(slug), link):
                return slug
    name = _payload_donation_name(payload)
    if name:
        for slug, meta in CANDIDATE_META.items():
            if _name_matches(name, meta['name']):
                return slug
    return ''


def remember_donation_request_id(slug: str, donation_request_id: str) -> None:
    donation_request_id = str(donation_request_id or '').strip()
    if slug not in CANDIDATE_SLUGS or not donation_request_id:
        return
    mapping, created = NextGameAutumnGoalMapping.objects.get_or_create(
        candidate=slug,
        defaults={
            'donation_request_id': donation_request_id,
            'web_url': configured_web_url(slug),
        },
    )
    if created or mapping.donation_request_id:
        return
    mapping.donation_request_id = donation_request_id
    mapping.web_url = mapping.web_url or configured_web_url(slug)
    mapping.save(update_fields=['donation_request_id', 'web_url', 'updated_at'])


def fresh_cents() -> dict[str, int]:
    totals = {slug: 0 for slug in CANDIDATE_SLUGS}
    rows = (
        NextGameAutumnVoteEvent.objects.filter(include_in_scoreboard=True, excluded=False)
        .values('candidate')
        .annotate(total=Sum('normalized_amount_eur_cents'))
    )
    for row in rows:
        slug = row['candidate']
        if slug in totals:
            totals[slug] = int(row['total'] or 0)
    return totals


def _donation_created_at(envelope: dict, payload: dict):
    created_at = _parse_event_datetime(envelope.get('created_at'))
    if payload.get('created_at'):
        payload_created = _parse_event_datetime(payload.get('created_at'))
        if payload_created is not None:
            created_at = payload_created
    return created_at


def process_donation_event(envelope: dict):
    if not isinstance(envelope, dict):
        raise VotePayloadError('envelope must be an object')
    event_name = str(envelope.get('name') or '').strip()
    payload = envelope.get('payload')
    if event_name not in DONATION_EVENTS:
        raise VotePayloadError('Unsupported event')
    if not isinstance(payload, dict):
        payload = {}

    slug = candidate_from_payload(payload)
    donation_created_at = _donation_created_at(envelope, payload)
    request_id = _payload_donation_request_id(payload)
    donation_id = ''
    for key in ('donation_id', 'payment_id', 'id'):
        value = payload.get(key)
        if value not in (None, ''):
            donation_id = str(value)[:64]
            break
    idempotency_key = ('autumn:' + _idempotency_key(event_name, envelope, payload))[:255]

    with transaction.atomic():
        event, created = NextGameAutumnVoteEvent.objects.get_or_create(
            idempotency_key=idempotency_key,
            defaults={
                'event_name': event_name,
                'donation_request_id': request_id,
                'tribute_donation_id': donation_id,
                'candidate': slug,
                'donation_created_at': donation_created_at,
                'raw_payload': envelope,
                'result': NextGameAutumnVoteEvent.RESULT_MALFORMED,
            },
        )
        if not created:
            logger.info('next_game_autumn_duplicate event=%s key=%s', event_name, idempotency_key[:80])
            return event

        try:
            amount_minor = _parse_amount_minor(payload.get('amount'))
            currency = str(payload.get('currency') or '').strip().upper()
            period = str(payload.get('period') or '').strip().lower()[:32]
            telegram_raw = payload.get('telegram_user_id')
            telegram_user_id = int(telegram_raw) if telegram_raw not in (None, '') else None
        except (VotePayloadError, TypeError, ValueError):
            event.result = NextGameAutumnVoteEvent.RESULT_MALFORMED
            event.save(update_fields=['result'])
            logger.warning('next_game_autumn_malformed event=%s', event_name)
            return event

        event.original_amount_minor = amount_minor
        event.original_currency = currency[:3]
        event.period = period
        event.telegram_user_id = telegram_user_id
        event.candidate = slug
        if not slug:
            event.result = NextGameAutumnVoteEvent.RESULT_MALFORMED
            event.save()
            return event

        if event_name != COUNTED_EVENT_NAME:
            event.result = NextGameAutumnVoteEvent.RESULT_IGNORED_EVENT
            event.save()
            remember_donation_request_id(slug, request_id)
            return event

        if currency not in SUPPORTED_CURRENCIES:
            event.result = NextGameAutumnVoteEvent.RESULT_UNKNOWN_CURRENCY
            event.save()
            remember_donation_request_id(slug, request_id)
            logger.warning('next_game_autumn_unknown_currency currency=%s', currency)
            return event

        event.fx_rate_to_eur = fx_rate_to_eur(currency)
        event.normalized_amount_eur_cents = original_minor_to_eur_cents(amount_minor, currency)
        if not donation_in_voting_period(donation_created_at):
            event.result = NextGameAutumnVoteEvent.RESULT_OUTSIDE_PERIOD
            event.save()
            remember_donation_request_id(slug, request_id)
            return event

        event.result = NextGameAutumnVoteEvent.RESULT_COUNTED
        event.include_in_scoreboard = True
        event.save()
        remember_donation_request_id(slug, request_id)
        return event


def maybe_process_autumn_donation(envelope: dict):
    """Own the webhook when it is one of this round's three donations."""
    if not isinstance(envelope, dict):
        return None
    event_name = str(envelope.get('name') or '').strip()
    if event_name not in DONATION_EVENTS:
        return None
    payload = envelope.get('payload') if isinstance(envelope.get('payload'), dict) else {}
    if not candidate_from_payload(payload):
        return None
    return process_donation_event(envelope)


def _september_totals():
    state = campaign_state()
    snapshot = state.snapshot or {}
    stored = snapshot.get('totals_eur_cents') if state.frozen_at else None
    if stored:
        return {slug: int(stored.get(slug) or 0) for slug in SEPTEMBER_SLUGS}
    return live_candidate_totals()


def september_context() -> dict:
    """Public September result used as the inherited start of this round."""
    totals = _september_totals()
    top = max(totals.values()) if totals else 0
    leaders = [slug for slug in SEPTEMBER_SLUGS if top > 0 and totals[slug] == top]
    winner_slug = leaders[0] if len(leaders) == 1 else ''
    winner_name = SEPTEMBER_META[winner_slug]['name'] if winner_slug else ''
    return {
        'totals': totals,
        'winner_slug': winner_slug,
        'winner_name': winner_name,
        'is_tie': len(leaders) > 1,
        'leaders': leaders,
    }


def carryover_cents() -> dict[str, int]:
    """Half of September support for candidates who finished strictly behind."""
    carried = {slug: 0 for slug in CANDIDATE_SLUGS}
    september = september_context()
    totals = september['totals']
    top = max(totals.values()) if totals else 0
    if top <= 0:
        return carried
    for september_slug, autumn_slug in CARRY_FROM_SEPTEMBER.items():
        cents = int(totals.get(september_slug) or 0)
        if cents < top:
            carried[autumn_slug] = half_cents(cents)
    return carried


def _percent(cents: int, total: int):
    if total <= 0:
        return None
    return int((Decimal(cents) * Decimal(100) / Decimal(total)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _bar_percent(cents: int, total: int) -> int:
    if total <= 0 or cents <= 0:
        return 0
    return int((Decimal(cents) * Decimal(100) / Decimal(total)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def scoreboard(*, now=None) -> dict:
    from games.club.next_game import campaign_now

    now = campaign_now(now)
    phase = campaign_phase(now)
    carried = carryover_cents()
    fresh = fresh_cents()
    totals = {slug: carried[slug] + fresh[slug] for slug in CANDIDATE_SLUGS}
    total = sum(totals.values())
    top = max(totals.values()) if totals else 0
    leaders = [slug for slug in CANDIDATE_SLUGS if top > 0 and totals[slug] == top]
    unique_leader = leaders[0] if len(leaders) == 1 else ''
    september = september_context()
    candidates = []
    for slug in CANDIDATE_SLUGS:
        cents = totals[slug]
        carry = carried[slug]
        added = fresh[slug]
        candidates.append({
            **CANDIDATE_META[slug],
            'eur_cents': cents,
            'eur_display': format_eur_cents(cents),
            'carry_cents': carry,
            'carry_display': format_eur_cents(carry),
            'fresh_cents': added,
            'fresh_display': format_eur_cents(added),
            'percent': _percent(cents, total),
            'bar_percent': _bar_percent(cents, total),
            'carry_bar_percent': _bar_percent(carry, total),
            'fresh_bar_percent': _bar_percent(added, total),
            'web_url': configured_web_url(slug),
            'is_leader': slug == unique_leader,
            'is_tied_leader': slug in leaders and len(leaders) > 1,
        })
    winner = CANDIDATE_META[unique_leader] if phase == PHASE_CLOSED and unique_leader else None
    return {
        'phase': phase,
        'candidates': candidates,
        'total_eur_cents': total,
        'total_eur_display': format_eur_cents(total),
        'is_zero': total <= 0,
        'is_tie': len(leaders) > 1,
        'winner': winner,
        'deadline_iso': VOTE_END.isoformat(),
        'september_winner_name': september['winner_name'],
        'september_is_tie': september['is_tie'],
        'has_carryover': any(carried.values()),
    }
