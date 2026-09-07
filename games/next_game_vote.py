"""One-off September 2026 donation vote for the next daily Inter Oves game.

Voting is 7–27 September 2026, Europe/Moscow. The winner is the candidate
with the highest support in frozen EUR. This is not a generic feature-voting
system.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from games.models import (
    NextGameVoteAdjustment,
    NextGameVoteCampaignState,
    NextGameVoteEvent,
    NextGameVoteGoalMapping,
    Profile,
    StatisticsEvent,
)

logger = logging.getLogger(__name__)

MOSCOW = ZoneInfo('Europe/Moscow')
CAMPAIGN_ID = 'next-game-2026-10'
CAMPAIGN_STATE_PK = 1

VOTE_START = datetime(2026, 9, 7, 0, 0, 0, tzinfo=MOSCOW)
VOTE_END = datetime(2026, 9, 27, 23, 59, 59, tzinfo=MOSCOW)

PHASE_UPCOMING = 'upcoming'
PHASE_LIVE = 'live'
PHASE_CLOSED = 'closed'

CANDIDATE_REDACTLE = NextGameVoteGoalMapping.CANDIDATE_REDACTLE
CANDIDATE_CRYPTIC = NextGameVoteGoalMapping.CANDIDATE_CRYPTIC
CANDIDATE_LOGIC = NextGameVoteGoalMapping.CANDIDATE_LOGIC
CANDIDATE_SLUGS = (
    CANDIDATE_REDACTLE,
    CANDIDATE_CRYPTIC,
    CANDIDATE_LOGIC,
)

COUNTED_EVENT_NAME = 'new_donation'
IGNORED_DONATION_EVENTS = frozenset({'recurrent_donation', 'cancelled_donation'})
DONATION_EVENTS = frozenset({COUNTED_EVENT_NAME}) | IGNORED_DONATION_EVENTS

# Frozen campaign rates. Round numbers, unchanged until 27 Sep 2026.
FX_SOURCE = 'fixed'
DEFAULT_RUB_PER_EUR = Decimal('100')
DEFAULT_USD_PER_EUR = Decimal('1.16')

SUPPORTED_CURRENCIES = frozenset({'EUR', 'RUB', 'USD'})

# Tribute `amount` is treated as the donor-chosen gross donation in smallest
# currency units (same convention as digital-product and club webhooks), before
# Tribute's platform fee. If a future payload exposes only a net field, document
# the change here rather than silently mixing net and gross.
AMOUNT_FIELD_SEMANTICS = 'gross_donation_before_tribute_fee'

SETTINGS_BY_CANDIDATE = {
    CANDIDATE_REDACTLE: {
        'url': 'TRIBUTE_NEXT_GAME_VOTE_REDACTLE_URL',
        'donation_request_id': 'TRIBUTE_NEXT_GAME_VOTE_REDACTLE_DONATION_REQUEST_ID',
        'key': 'REDACTLE',
    },
    CANDIDATE_CRYPTIC: {
        'url': 'TRIBUTE_NEXT_GAME_VOTE_CRYPTIC_URL',
        'donation_request_id': 'TRIBUTE_NEXT_GAME_VOTE_CRYPTIC_DONATION_REQUEST_ID',
        'key': 'CRYPTIC',
    },
    CANDIDATE_LOGIC: {
        'url': 'TRIBUTE_NEXT_GAME_VOTE_LOGIC_PUZZLES_URL',
        'donation_request_id': 'TRIBUTE_NEXT_GAME_VOTE_LOGIC_PUZZLES_DONATION_REQUEST_ID',
        'key': 'LOGIC_PUZZLES',
    },
}

CANDIDATE_META = {
    CANDIDATE_REDACTLE: {
        'slug': CANDIDATE_REDACTLE,
        'name': 'Redactle',
        'tag': 'Угадай статью Википедии слово за словом',
        'cta': 'Поддержать Redactle',
        'preview': 'redactle',
    },
    CANDIDATE_CRYPTIC: {
        'slug': CANDIDATE_CRYPTIC,
        'name': 'Криптик',
        'tag': 'Одна хитрая словесная загадка в день',
        'cta': 'Поддержать Криптик',
        'preview': 'cryptic',
    },
    CANDIDATE_LOGIC: {
        'slug': CANDIDATE_LOGIC,
        'name': 'Логические пазлы',
        'tag': 'Новая сеточная головоломка каждый день',
        'cta': 'Поддержать логические пазлы',
        'preview': 'logic',
    },
}


class VotePayloadError(ValueError):
    pass


@dataclass(frozen=True)
class VoteProcessResult:
    event: NextGameVoteEvent
    duplicate: bool = False


def rub_per_eur() -> Decimal:
    raw = getattr(settings, 'NEXT_GAME_VOTE_RUB_PER_EUR', None)
    if raw in (None, ''):
        return DEFAULT_RUB_PER_EUR
    return Decimal(str(raw))


def usd_per_eur() -> Decimal:
    raw = getattr(settings, 'NEXT_GAME_VOTE_USD_PER_EUR', None)
    if raw in (None, ''):
        return DEFAULT_USD_PER_EUR
    return Decimal(str(raw))


def fx_rate_to_eur(currency: str) -> Decimal:
    code = (currency or '').upper()
    if code == 'EUR':
        return Decimal('1')
    if code == 'RUB':
        return Decimal('1') / rub_per_eur()
    if code == 'USD':
        return Decimal('1') / usd_per_eur()
    raise VotePayloadError('Unsupported currency')


def original_minor_to_eur_cents(amount_minor: int, currency: str) -> int:
    major = Decimal(amount_minor) / Decimal(100)
    eur = (major * fx_rate_to_eur(currency)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return int(eur * 100)


def format_eur_cents(cents: int) -> str:
    negative = cents < 0
    cents = abs(int(cents))
    major, minor = divmod(cents, 100)
    formatted = '{:,}'.format(major).replace(',', '\u00a0')
    if minor:
        text = '{},{:02d}'.format(formatted, minor)
    else:
        text = formatted
    if negative:
        return '−€{}'.format(text)
    return '€{}'.format(text)


def cents_to_eur_major(cents: int) -> Decimal:
    return (Decimal(int(cents)) / Decimal(100)).quantize(Decimal('0.01'))


def eur_major_to_cents(value) -> int:
    eur = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return int(eur * 100)


def campaign_now(now=None):
    if now is None:
        now = timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, timezone.utc)
    return now


def campaign_phase(now=None) -> str:
    now = campaign_now(now)
    if now < VOTE_START:
        return PHASE_UPCOMING
    if now <= VOTE_END:
        return PHASE_LIVE
    return PHASE_CLOSED


def donation_in_voting_period(created_at) -> bool:
    if created_at is None:
        return False
    if timezone.is_naive(created_at):
        created_at = timezone.make_aware(created_at, timezone.utc)
    return VOTE_START <= created_at <= VOTE_END


def _setting(name: str) -> str:
    return str(getattr(settings, name, '') or '').strip()


def configured_web_url(slug: str) -> str:
    mapping = NextGameVoteGoalMapping.objects.filter(candidate=slug).first()
    if mapping and mapping.web_url:
        return mapping.web_url.strip()
    names = SETTINGS_BY_CANDIDATE[slug]
    return _setting(names['url'])


def configured_donation_request_id(slug: str) -> str:
    mapping = NextGameVoteGoalMapping.objects.filter(candidate=slug).first()
    if mapping and mapping.donation_request_id:
        return str(mapping.donation_request_id).strip()
    names = SETTINGS_BY_CANDIDATE[slug]
    return _setting(names['donation_request_id'])


def goal_id_mapping() -> dict[str, str]:
    """donation_request_id → candidate. Empty or colliding IDs are omitted."""
    by_id = {}
    collisions = set()
    for slug in CANDIDATE_SLUGS:
        raw_id = configured_donation_request_id(slug)
        if not raw_id:
            continue
        if raw_id in by_id and by_id[raw_id] != slug:
            collisions.add(raw_id)
            continue
        by_id[raw_id] = slug
    for raw_id in collisions:
        by_id.pop(raw_id, None)
    return by_id


def mapping_is_ready() -> bool:
    mapping = goal_id_mapping()
    return len(mapping) == 3 and set(mapping.values()) == set(CANDIDATE_SLUGS)


def mapping_blocker_reason() -> str:
    ids = {slug: configured_donation_request_id(slug) for slug in CANDIDATE_SLUGS}
    filled = [value for value in ids.values() if value]
    if len(filled) < 3:
        return (
            'donation_request_id is not configured for every candidate. '
            'Set env/admin mapping after three test payments into the Tribute Goals.'
        )
    if len(set(filled)) < 3:
        return (
            'donation_request_id does not distinguish the three Tribute Goals. '
            'Do not guess the destination; set official totals via admin adjustments '
            'until Tribute provides a distinct identifier.'
        )
    return ''


def candidate_for_donation_request_id(donation_request_id) -> str:
    if donation_request_id in (None, ''):
        return ''
    return goal_id_mapping().get(str(donation_request_id), '')


def _payload_donation_name(payload: dict) -> str:
    for key in ('donation_name', 'donationName', 'goal_name'):
        value = payload.get(key)
        if value not in (None, ''):
            return str(value).strip()
    return ''


def _payload_web_link(payload: dict) -> str:
    for key in ('web_app_link', 'webAppLink', 'web_link', 'link'):
        value = payload.get(key)
        if value not in (None, ''):
            return str(value).strip()
    return ''


def _startapp_token(url: str) -> str:
    if not url:
        return ''
    parsed = urlparse(url)
    token = (parse_qs(parsed.query).get('startapp') or [''])[0].strip()
    if token:
        return token
    parts = [part for part in parsed.path.split('/') if part]
    if len(parts) >= 2 and parts[0] == 'g':
        return parts[1]
    return ''


def candidate_from_payload(payload: dict) -> str:
    """Resolve a vote candidate from Tribute donation fields."""
    slug = candidate_for_donation_request_id(_payload_donation_request_id(payload))
    if slug:
        return slug
    name = _payload_donation_name(payload).casefold()
    if name:
        for candidate_slug, meta in CANDIDATE_META.items():
            if name in {meta['name'].casefold(), candidate_slug}:
                return candidate_slug
    link = _payload_web_link(payload)
    link_token = _startapp_token(link)
    if link:
        for candidate_slug in CANDIDATE_SLUGS:
            configured = configured_web_url(candidate_slug)
            if configured and configured in link:
                return candidate_slug
            token = _startapp_token(configured)
            if token and (token == link_token or token in link):
                return candidate_slug
    return ''


def remember_donation_request_id(slug: str, donation_request_id: str) -> None:
    donation_request_id = str(donation_request_id or '').strip()
    if slug not in CANDIDATE_SLUGS or not donation_request_id:
        return
    existing = configured_donation_request_id(slug)
    if existing:
        return
    mapping, created = NextGameVoteGoalMapping.objects.get_or_create(
        candidate=slug,
        defaults={
            'donation_request_id': donation_request_id,
            'web_url': configured_web_url(slug),
            'note': 'Filled from Tribute webhook',
        },
    )
    if created:
        return
    if mapping.donation_request_id:
        return
    mapping.donation_request_id = donation_request_id
    mapping.save(update_fields=['donation_request_id', 'updated_at'])


def _parse_optional_int(value, field: str, *, required=False) -> int | None:
    if value in (None, ''):
        if required:
            raise VotePayloadError('Missing {}'.format(field))
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise VotePayloadError('Invalid {}'.format(field)) from exc
    return parsed


def _parse_amount_minor(value) -> int:
    if value in (None, ''):
        raise VotePayloadError('Missing amount')
    if isinstance(value, bool) or isinstance(value, float):
        raise VotePayloadError('Invalid amount')
    if isinstance(value, int):
        if value < 0:
            raise VotePayloadError('Invalid amount')
        return value
    raw = str(value).strip()
    if not raw.isdigit():
        raise VotePayloadError('Invalid amount')
    return int(raw)


def _parse_event_datetime(value):
    if not value and value != 0:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        raw = str(value).strip()
        if raw.isdigit():
            try:
                return datetime.fromtimestamp(int(raw), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return parsed


def _payload_donation_id(payload: dict) -> str:
    for key in ('donation_id', 'payment_id', 'id'):
        value = payload.get(key)
        if value not in (None, ''):
            return str(value)[:64]
    return ''


def _payload_donation_request_id(payload: dict) -> str:
    for key in ('donation_request_id', 'donationRequestId'):
        value = payload.get(key)
        if value not in (None, ''):
            return str(value)[:64]
    return ''


def _idempotency_key(event_name: str, envelope: dict, payload: dict) -> str:
    donation_id = _payload_donation_id(payload)
    if donation_id:
        return '{}:{}'.format(event_name, donation_id)[:255]
    created = str(envelope.get('created_at') or '')
    request_id = _payload_donation_request_id(payload)
    user_id = payload.get('telegram_user_id')
    if user_id in (None, ''):
        user_id = payload.get('trb_user_id') or payload.get('user_id') or ''
    amount = payload.get('amount')
    currency = str(payload.get('currency') or '').strip().lower()
    period = str(payload.get('period') or '').strip().lower()
    raw = '{}|{}|{}|{}|{}|{}|{}'.format(
        event_name, created, request_id, user_id, amount, currency, period,
    )
    if len(raw) <= 255:
        return raw
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _safe_excerpt(envelope: dict, payload: dict, *, donation_created_at=None) -> dict:
    created = donation_created_at.isoformat() if donation_created_at is not None else envelope.get('created_at')
    return {
        'event_name': envelope.get('name'),
        'created_at': created,
        'donation_request_id': _payload_donation_request_id(payload),
        'donation_id': _payload_donation_id(payload),
        'amount': payload.get('amount'),
        'currency': payload.get('currency'),
        'period': payload.get('period'),
        'telegram_user_id': payload.get('telegram_user_id'),
        'trb_user_id': payload.get('trb_user_id') or payload.get('user_id'),
    }


def _match_user(telegram_user_id):
    if telegram_user_id is None:
        return None
    profile = (
        Profile.objects.select_related('user')
        .filter(telegram_user_id=telegram_user_id, telegram_verified=True)
        .first()
    )
    if profile is None:
        return None
    return profile.user


def campaign_state() -> NextGameVoteCampaignState:
    state, _created = NextGameVoteCampaignState.objects.get_or_create(pk=CAMPAIGN_STATE_PK)
    return state


def live_candidate_totals() -> dict[str, int]:
    totals = {slug: 0 for slug in CANDIDATE_SLUGS}
    rows = (
        NextGameVoteEvent.objects.filter(include_in_scoreboard=True, excluded=False)
        .values('candidate')
        .annotate(total=Sum('normalized_amount_eur_cents'))
    )
    for row in rows:
        slug = row['candidate']
        if slug in totals:
            totals[slug] += int(row['total'] or 0)
    adj_rows = NextGameVoteAdjustment.objects.values('candidate').annotate(total=Sum('amount_eur_cents'))
    for row in adj_rows:
        slug = row['candidate']
        if slug in totals:
            totals[slug] += int(row['total'] or 0)
    return totals


def score_breakdown() -> dict:
    webhook = {slug: 0 for slug in CANDIDATE_SLUGS}
    rows = (
        NextGameVoteEvent.objects.filter(include_in_scoreboard=True, excluded=False)
        .values('candidate')
        .annotate(total=Sum('normalized_amount_eur_cents'))
    )
    for row in rows:
        slug = row['candidate']
        if slug in webhook:
            webhook[slug] = int(row['total'] or 0)
    adjustments = {slug: 0 for slug in CANDIDATE_SLUGS}
    adj_rows = NextGameVoteAdjustment.objects.values('candidate').annotate(total=Sum('amount_eur_cents'))
    for row in adj_rows:
        slug = row['candidate']
        if slug in adjustments:
            adjustments[slug] = int(row['total'] or 0)
    live = {slug: webhook[slug] + adjustments[slug] for slug in CANDIDATE_SLUGS}
    return {
        'webhook': webhook,
        'adjustments': adjustments,
        'live': live,
        'rows': [
            {
                'slug': slug,
                'name': CANDIDATE_META[slug]['name'],
                'webhook_cents': webhook[slug],
                'adjustment_cents': adjustments[slug],
                'live_cents': live[slug],
                'webhook_display': format_eur_cents(webhook[slug]),
                'adjustment_display': format_eur_cents(adjustments[slug]),
                'live_display': format_eur_cents(live[slug]),
            }
            for slug in CANDIDATE_SLUGS
        ],
    }


def _leader_slugs(totals: dict[str, int]) -> list[str]:
    max_cents = max(totals.values()) if totals else 0
    if max_cents <= 0:
        return []
    return [slug for slug, cents in totals.items() if cents == max_cents]


def _serialize_snapshot(totals: dict[str, int]) -> dict:
    leaders = _leader_slugs(totals)
    total = sum(totals.values())
    return {
        'campaign_id': CAMPAIGN_ID,
        'totals_eur_cents': totals,
        'total_eur_cents': total,
        'leader_slugs': leaders,
        'winner_slug': leaders[0] if len(leaders) == 1 else '',
        'is_tie': len(leaders) > 1,
        'fx': {
            'source': FX_SOURCE,
            'rub_per_eur': str(rub_per_eur()),
            'usd_per_eur': str(usd_per_eur()),
        },
    }


def refresh_snapshot(*, frozen_by=None, freeze_reason='', now=None) -> NextGameVoteCampaignState:
    now = campaign_now(now)
    totals = live_candidate_totals()
    snapshot = _serialize_snapshot(totals)
    with transaction.atomic():
        state = (
            NextGameVoteCampaignState.objects.select_for_update()
            .filter(pk=CAMPAIGN_STATE_PK)
            .first()
        )
        if state is None:
            state = NextGameVoteCampaignState(pk=CAMPAIGN_STATE_PK)
        state.snapshot = snapshot
        state.winner_slug = snapshot['winner_slug']
        state.is_tie = snapshot['is_tie']
        state.mapping_blocker = not mapping_is_ready()
        if freeze_reason:
            state.frozen_at = now
            state.frozen_by = frozen_by
            state.freeze_reason = freeze_reason
        state.save()
    return state


def ensure_frozen(*, now=None) -> NextGameVoteCampaignState:
    now = campaign_now(now)
    state = campaign_state()
    if state.frozen_at is not None:
        return state
    if campaign_phase(now) != PHASE_CLOSED and not state.freeze_reason:
        return state
    reason = state.freeze_reason or NextGameVoteCampaignState.FREEZE_DEADLINE
    return refresh_snapshot(freeze_reason=reason, now=now)


def freeze_manually(*, user=None, now=None) -> NextGameVoteCampaignState:
    return refresh_snapshot(
        frozen_by=user,
        freeze_reason=NextGameVoteCampaignState.FREEZE_MANUAL,
        now=now,
    )


def process_donation_event(envelope: dict) -> VoteProcessResult:
    if not isinstance(envelope, dict):
        raise VotePayloadError('envelope must be an object')
    event_name = str(envelope.get('name') or '').strip()
    payload = envelope.get('payload')
    if event_name not in DONATION_EVENTS:
        raise VotePayloadError('Unsupported event')
    if not isinstance(payload, dict):
        payload = {}

    donation_created_at = _parse_event_datetime(envelope.get('created_at'))
    if payload.get('created_at'):
        payload_created = _parse_event_datetime(payload.get('created_at'))
        if payload_created is not None:
            donation_created_at = payload_created

    idempotency_key = _idempotency_key(event_name, envelope, payload)
    excerpt = _safe_excerpt(envelope, payload, donation_created_at=donation_created_at)
    donation_request_id = _payload_donation_request_id(payload)
    tribute_donation_id = _payload_donation_id(payload)

    with transaction.atomic():
        event, created = NextGameVoteEvent.objects.get_or_create(
            idempotency_key=idempotency_key,
            defaults={
                'event_name': event_name,
                'donation_request_id': donation_request_id,
                'tribute_donation_id': tribute_donation_id,
                'donation_created_at': donation_created_at,
                'raw_payload': envelope,
                'payload_excerpt': excerpt,
                'result': NextGameVoteEvent.RESULT_MALFORMED,
            },
        )
        if not created:
            logger.info(
                'next_game_vote_duplicate event=%s key=%s',
                event_name, idempotency_key[:80],
            )
            return VoteProcessResult(event, duplicate=True)

        try:
            amount_minor = _parse_amount_minor(payload.get('amount'))
            currency = str(payload.get('currency') or '').strip().upper()
            period = str(payload.get('period') or '').strip().lower()[:32]
            telegram_user_id = _parse_optional_int(payload.get('telegram_user_id'), 'telegram_user_id')
            trb_user_id = str(payload.get('trb_user_id') or payload.get('user_id') or '')[:128]
        except VotePayloadError:
            event.result = NextGameVoteEvent.RESULT_MALFORMED
            event.save(update_fields=['result'])
            logger.warning('next_game_vote_malformed event=%s', event_name)
            return VoteProcessResult(event)

        event.original_amount_minor = amount_minor
        event.original_currency = currency[:3]
        event.period = period
        event.telegram_user_id = telegram_user_id
        event.trb_user_id = trb_user_id
        event.matched_user = _match_user(telegram_user_id)
        event.candidate = candidate_from_payload(payload)

        if event_name != COUNTED_EVENT_NAME:
            event.result = NextGameVoteEvent.RESULT_IGNORED_EVENT
            event.save()
            return VoteProcessResult(event)

        if currency not in SUPPORTED_CURRENCIES:
            event.result = NextGameVoteEvent.RESULT_UNKNOWN_CURRENCY
            event.save()
            logger.warning('next_game_vote_unknown_currency currency=%s', currency)
            return VoteProcessResult(event)

        event.fx_rate_to_eur = fx_rate_to_eur(currency)
        event.normalized_amount_eur_cents = original_minor_to_eur_cents(amount_minor, currency)

        if not event.candidate:
            event.result = NextGameVoteEvent.RESULT_UNMAPPED
            event.save()
            logger.warning(
                'next_game_vote_unmapped donation_request_id=%s mapping_ready=%s',
                donation_request_id, mapping_is_ready(),
            )
            return VoteProcessResult(event)

        state = campaign_state()
        after_manual_freeze = (
            state.frozen_at is not None
            and state.freeze_reason == NextGameVoteCampaignState.FREEZE_MANUAL
            and donation_created_at is not None
            and donation_created_at > state.frozen_at
        )
        if after_manual_freeze or not donation_in_voting_period(donation_created_at):
            event.result = NextGameVoteEvent.RESULT_OUTSIDE_PERIOD
            event.save()
            return VoteProcessResult(event)

        event.result = NextGameVoteEvent.RESULT_COUNTED
        event.include_in_scoreboard = True
        event.analytics_goal_queued_at = timezone.now()
        event.save()
        remember_donation_request_id(event.candidate, donation_request_id)

        StatisticsEvent.record(
            'next_game_vote_payment',
            user=event.matched_user,
            candidate=event.candidate,
            currency=event.original_currency,
            normalized_amount_eur=str(
                (Decimal(event.normalized_amount_eur_cents) / Decimal(100)).quantize(Decimal('0.01'))
            ),
        )
        if state.frozen_at is not None:
            refresh_snapshot()
        elif campaign_phase() == PHASE_CLOSED:
            ensure_frozen()
        return VoteProcessResult(event)


def remap_unmapped_events() -> int:
    """Apply donation_request_id mapping to events stored before IDs were known."""
    mapping = goal_id_mapping()
    if not mapping:
        return 0
    updated = 0
    qs = NextGameVoteEvent.objects.filter(
        result=NextGameVoteEvent.RESULT_UNMAPPED,
        excluded=False,
        event_name=COUNTED_EVENT_NAME,
    )
    for event in qs:
        slug = mapping.get(event.donation_request_id)
        if not slug:
            continue
        event.candidate = slug
        if (
            event.original_currency in SUPPORTED_CURRENCIES
            and donation_in_voting_period(event.donation_created_at)
        ):
            event.result = NextGameVoteEvent.RESULT_COUNTED
            event.include_in_scoreboard = True
            if event.analytics_goal_queued_at is None:
                event.analytics_goal_queued_at = timezone.now()
        event.save()
        updated += 1
    if updated and campaign_state().frozen_at is not None:
        refresh_snapshot()
    return updated


def maybe_process_tribute_donation(envelope: dict) -> VoteProcessResult | None:
    """Return a result when the envelope is a donation event; otherwise None."""
    if not isinstance(envelope, dict):
        return None
    event_name = str(envelope.get('name') or '').strip()
    if event_name not in DONATION_EVENTS:
        return None
    payload = envelope.get('payload') if isinstance(envelope.get('payload'), dict) else {}
    request_id = _payload_donation_request_id(payload)
    mapped = bool(candidate_from_payload(payload))
    if mapped or not mapping_is_ready():
        return process_donation_event(envelope)
    # Mapping is complete and this donation belongs to some other Tribute goal.
    return None


def exclude_event(event: NextGameVoteEvent, *, user=None, reason='') -> NextGameVoteEvent:
    event.excluded = True
    event.excluded_reason = (reason or '').strip()
    event.excluded_by = user
    event.excluded_at = timezone.now()
    event.include_in_scoreboard = False
    event.save(update_fields=[
        'excluded', 'excluded_reason', 'excluded_by', 'excluded_at', 'include_in_scoreboard',
    ])
    if campaign_state().frozen_at is not None:
        refresh_snapshot()
    return event


def add_adjustment(*, candidate: str, amount_eur_cents: int, comment: str, user=None, refresh=True) -> NextGameVoteAdjustment:
    comment = (comment or '').strip()
    if not comment:
        raise VotePayloadError('Adjustment comment is required')
    if candidate not in CANDIDATE_SLUGS:
        raise VotePayloadError('Unknown candidate')
    adjustment = NextGameVoteAdjustment.objects.create(
        candidate=candidate,
        amount_eur_cents=int(amount_eur_cents),
        comment=comment,
        created_by=user,
    )
    if refresh and campaign_state().frozen_at is not None:
        refresh_snapshot()
    return adjustment


def set_candidate_totals(*, totals_eur_cents: dict[str, int], comment: str, user=None) -> list[NextGameVoteAdjustment]:
    """Set public totals by writing audit deltas. Tribute events are not edited."""
    comment = (comment or '').strip()
    if not comment:
        raise VotePayloadError('Adjustment comment is required')
    created = []
    current = live_candidate_totals()
    for slug in CANDIDATE_SLUGS:
        if slug not in totals_eur_cents:
            continue
        target = int(totals_eur_cents[slug])
        if target < 0:
            raise VotePayloadError('Totals cannot be negative')
        delta = target - current[slug]
        if delta == 0:
            continue
        created.append(add_adjustment(
            candidate=slug,
            amount_eur_cents=delta,
            comment=comment,
            user=user,
            refresh=False,
        ))
    if created and campaign_state().frozen_at is not None:
        refresh_snapshot()
    return created


def _percent(cents: int, total: int) -> int | None:
    if total <= 0:
        return None
    return int((Decimal(cents) * Decimal(100) / Decimal(total)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def official_totals(*, now=None) -> dict[str, int]:
    now = campaign_now(now)
    state = campaign_state()
    if campaign_phase(now) == PHASE_CLOSED:
        state = ensure_frozen(now=now)
    if state.frozen_at is not None and state.snapshot:
        stored = state.snapshot.get('totals_eur_cents') or {}
        return {slug: int(stored.get(slug) or 0) for slug in CANDIDATE_SLUGS}
    return live_candidate_totals()


def scoreboard(*, now=None) -> dict:
    now = campaign_now(now)
    phase = campaign_phase(now)
    if phase == PHASE_CLOSED:
        ensure_frozen(now=now)
    state = campaign_state()
    if state.frozen_at is not None:
        phase = PHASE_CLOSED
    totals = official_totals(now=now)
    total = sum(totals.values())
    leaders = _leader_slugs(totals)
    unique_leader = leaders[0] if len(leaders) == 1 else ''
    candidates = []
    for slug in CANDIDATE_SLUGS:
        meta = CANDIDATE_META[slug]
        cents = totals[slug]
        percent = _percent(cents, total)
        candidates.append({
            **meta,
            'eur_cents': cents,
            'eur_display': format_eur_cents(cents),
            'percent': percent,
            'bar_percent': percent if percent is not None else 0,
            'web_url': configured_web_url(slug),
            'is_leader': slug == unique_leader,
            'is_tied_leader': slug in leaders and len(leaders) > 1,
        })
    winner = None
    if state.frozen_at is not None and unique_leader:
        winner = CANDIDATE_META[unique_leader]
    return {
        'phase': phase,
        'totals': totals,
        'total_eur_cents': total,
        'total_eur_display': format_eur_cents(total),
        'leaders': leaders,
        'unique_leader': unique_leader,
        'is_zero': total <= 0,
        'is_tie': len(leaders) > 1,
        'winner': winner,
        'frozen': state.frozen_at is not None,
        'frozen_at': state.frozen_at,
        'mapping_ready': mapping_is_ready(),
        'mapping_blocker_reason': mapping_blocker_reason(),
        'candidates': candidates,
        'deadline_iso': VOTE_END.isoformat(),
        'start_iso': VOTE_START.isoformat(),
        'countdown_target_iso': VOTE_END.isoformat(),
    }
