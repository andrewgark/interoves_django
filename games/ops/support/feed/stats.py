from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional

from django.urls import reverse
from django.utils import timezone

from games.telegram.digest import collect_daily_digest_stats


@dataclass(frozen=True)
class TopGameRow:
    game_id: str
    game_label: str
    game_url: str
    primary_value: int
    secondary_value: int


@dataclass(frozen=True)
class SupportStats:
    since: object
    until: object
    hours: int
    attempts_total: int
    attempts_by_status: dict
    active_users: int
    active_teams: int
    active_anon: int
    hint_total: int
    hint_users: int
    top_games_attempts: List[TopGameRow]
    top_games_users: List[TopGameRow]
    registrations: int
    new_accounts: int
    tickets_pending: int
    tickets_accepted: int
    tickets_revenue: int
    tickets_revenue_amd: int
    bugs_total: int
    bugs_pending: int
    corporate_orders: int
    pending_bugs_now: int
    pending_tickets_now: int
    stuck_tickets_now: int


def _top_rows(raw_rows, *, primary_key: str, secondary_key: str, game_labels) -> List[TopGameRow]:
    rows = []
    for row in raw_rows:
        game_id = row['game_id']
        rows.append(TopGameRow(
            game_id=game_id,
            game_label=game_labels.get(game_id, game_id or '—'),
            game_url=reverse('support:game', kwargs={'game_id': game_id}),
            primary_value=row[primary_key],
            secondary_value=row[secondary_key],
        ))
    return rows


def collect_support_stats(*, hours: int = 24, top_limit: int = 15) -> SupportStats:
    from games.models import Game

    since = timezone.now() - timedelta(hours=hours)
    stats = collect_daily_digest_stats(since, top_limit=top_limit)
    top_attempts = stats['top_games_attempts']
    top_users = stats['top_games_users']
    game_ids = {
        row['game_id']
        for row in top_attempts + top_users
        if row.get('game_id')
    }
    games = Game.objects.filter(pk__in=game_ids).only(
        'id', 'name', 'outside_name', 'no_html_name',
    )
    game_labels = {
        game.id: game.get_no_html_name() or game.outside_name or game.name or game.id
        for game in games
    }
    return SupportStats(
        since=stats['since'],
        until=stats['until'],
        hours=hours,
        attempts_total=stats['attempts_total'],
        attempts_by_status=stats['attempts_by_status'],
        active_users=stats['active_users'],
        active_teams=stats['active_teams'],
        active_anon=stats['active_anon'],
        hint_total=stats['hint_total'],
        hint_users=stats['hint_users'],
        top_games_attempts=_top_rows(
            top_attempts, primary_key='attempts', secondary_key='users', game_labels=game_labels,
        ),
        top_games_users=_top_rows(
            top_users, primary_key='users', secondary_key='attempts', game_labels=game_labels,
        ),
        registrations=stats['registrations'],
        new_accounts=stats['new_accounts'],
        tickets_pending=stats['tickets_pending'],
        tickets_accepted=stats['tickets_accepted'],
        tickets_revenue=stats['tickets_revenue'],
        tickets_revenue_amd=stats['tickets_revenue_amd'],
        bugs_total=stats['bugs_total'],
        bugs_pending=stats['bugs_pending'],
        corporate_orders=stats['corporate_orders'],
        pending_bugs_now=stats['pending_bugs_now'],
        pending_tickets_now=stats['pending_tickets_now'],
        stuck_tickets_now=stats['stuck_tickets_now'],
    )
