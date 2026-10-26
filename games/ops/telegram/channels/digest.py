from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import Count, Q, Sum
from django.utils import timezone

from games.models import Attempt, BugReport, CorporateGameOrder, Game, HintAttempt, Registration, TicketRequest
from games.ticket_service import stuck_pending_ticket_count
from games.telegram.notify import _escape, _join_lines

DIGEST_TOP_GAMES = 5


def digest_period(since=None):
    since = since or (timezone.now() - timedelta(days=1))
    until = timezone.now()
    return since, until


def _format_period(since, until) -> str:
    fmt = '%d.%m %H:%M'
    return '{} — {}'.format(
        timezone.localtime(since).strftime(fmt),
        timezone.localtime(until).strftime(fmt),
    )


def _attempts_qs(since):
    return Attempt.manager.filter(time__gte=since, skip=False)


def _hint_requests_qs(since):
    return HintAttempt.objects.filter(time__gte=since, is_real_request=True)


def _game_label(game_id: str) -> str:
    if not game_id:
        return '—'
    game = Game.objects.filter(pk=game_id).only('id', 'name', 'no_html_name', 'outside_name').first()
    if game is None:
        return _escape(game_id)
    return _escape(game.get_no_html_name())


def _top_games_by_attempts(since, *, limit=DIGEST_TOP_GAMES) -> list[dict]:
    rows = (
        _attempts_qs(since)
        .filter(game_id__isnull=False)
        .values('game_id')
        .annotate(
            attempts=Count('id'),
            users=Count('user_id', filter=Q(user_id__isnull=False), distinct=True),
            teams=Count('team_id', filter=Q(team_id__isnull=False), distinct=True),
        )
        .order_by('-attempts', 'game_id')[:limit]
    )
    return list(rows)


def _top_games_by_users(since, *, limit=DIGEST_TOP_GAMES) -> list[dict]:
    rows = (
        _attempts_qs(since)
        .filter(game_id__isnull=False, user_id__isnull=False)
        .values('game_id')
        .annotate(users=Count('user_id', distinct=True), attempts=Count('id'))
        .order_by('-users', '-attempts', 'game_id')[:limit]
    )
    return list(rows)


def _format_top_games(rows, *, primary_key: str, secondary_key: str, primary_label: str, secondary_label: str) -> list[str]:
    if not rows:
        return ['—']
    lines = []
    for index, row in enumerate(rows, start=1):
        lines.append('{}. {} — {} {} · {} {}'.format(
            index,
            _game_label(row['game_id']),
            row[primary_key],
            primary_label,
            row[secondary_key],
            secondary_label,
        ))
    return lines


def collect_daily_digest_stats(since=None, *, top_limit=DIGEST_TOP_GAMES) -> dict:
    since, until = digest_period(since)
    attempts = _attempts_qs(since)
    hints = _hint_requests_qs(since)

    attempt_summary = attempts.aggregate(
        attempts_total=Count('id'),
        active_users=Count(
            'user_id', filter=Q(user_id__isnull=False), distinct=True,
        ),
        active_teams=Count(
            'team_id',
            filter=Q(team_id__isnull=False, team__is_tester=False),
            distinct=True,
        ),
        active_anon=Count(
            'anon_key',
            filter=Q(anon_key__isnull=False) & ~Q(anon_key=''),
            distinct=True,
        ),
    )
    attempts_by_status = {
        row['status']: row['n']
        for row in attempts.values('status').annotate(n=Count('id'))
    }

    hint_summary = hints.aggregate(
        hint_total=Count('id'),
        hint_users=Count(
            'user_id', filter=Q(user_id__isnull=False), distinct=True,
        ),
    )

    tickets = TicketRequest.objects.filter(time__gte=since)
    ticket_summary = tickets.aggregate(
        tickets_pending=Count('id', filter=Q(status='Pending')),
        tickets_accepted=Count('id', filter=Q(status='Accepted')),
        tickets_revenue=Sum('money', filter=Q(status='Accepted', currency='RUB')),
        tickets_revenue_amd=Sum('money', filter=Q(status='Accepted', currency='AMD')),
    )

    bugs = BugReport.objects.filter(time__gte=since)
    bug_summary = bugs.aggregate(
        bugs_total=Count('id'),
        bugs_pending=Count('id', filter=Q(status='Pending')),
    )

    return {
        'since': since,
        'until': until,
        'attempts_total': attempt_summary['attempts_total'],
        'attempts_by_status': attempts_by_status,
        'active_users': attempt_summary['active_users'],
        'active_teams': attempt_summary['active_teams'],
        'active_anon': attempt_summary['active_anon'],
        'hint_total': hint_summary['hint_total'],
        'hint_users': hint_summary['hint_users'],
        'top_games_attempts': _top_games_by_attempts(since, limit=top_limit),
        'top_games_users': _top_games_by_users(since, limit=top_limit),
        'registrations': Registration.objects.filter(time__gte=since).count(),
        'new_accounts': User.objects.filter(date_joined__gte=since).count(),
        'tickets_pending': ticket_summary['tickets_pending'],
        'tickets_accepted': ticket_summary['tickets_accepted'],
        'tickets_revenue': ticket_summary['tickets_revenue'] or 0,
        'tickets_revenue_amd': ticket_summary['tickets_revenue_amd'] or 0,
        'bugs_total': bug_summary['bugs_total'],
        'bugs_pending': bug_summary['bugs_pending'],
        'corporate_orders': CorporateGameOrder.objects.filter(created_at__gte=since).count(),
        'pending_bugs_now': BugReport.objects.filter(status='Pending').count(),
        'pending_tickets_now': TicketRequest.objects.filter(status='Pending').count(),
        'stuck_tickets_now': stuck_pending_ticket_count(),
    }


def build_daily_digest(since=None) -> str:
    stats = collect_daily_digest_stats(since)
    status = stats['attempts_by_status']
    status_bits = []
    for key, label in (('Ok', '✓'), ('Wrong', '✗'), ('Partial', '~'), ('Pending', '…')):
        count = status.get(key, 0)
        if count:
            status_bits.append('{} {}'.format(label, count))
    status_line = ' · '.join(status_bits) if status_bits else '—'

    lines = [
        '<b>Дайджест за 24 часа</b>',
        _escape(_format_period(stats['since'], stats['until'])),
        '',
        '<b>Посылки</b>',
        'Всего: {}'.format(stats['attempts_total']),
        status_line,
        'Активных: {} пользователей · {} команд · {} анонимов'.format(
            stats['active_users'], stats['active_teams'], stats['active_anon'],
        ),
        'Подсказки: {} запросов · {} пользователей'.format(stats['hint_total'], stats['hint_users']),
        '',
        '<b>Топ игр · посылки</b>',
        *_format_top_games(
            stats['top_games_attempts'],
            primary_key='attempts',
            secondary_key='users',
            primary_label='посылок',
            secondary_label='пользов.',
        ),
        '',
        '<b>Топ игр · игроки</b>',
        *_format_top_games(
            stats['top_games_users'],
            primary_key='users',
            secondary_key='attempts',
            primary_label='пользов.',
            secondary_label='посылок',
        ),
        '',
        '<b>Регистрации и оплаты</b>',
        'Регистрации на игры: {}'.format(stats['registrations']),
        'Новые аккаунты: {}'.format(stats['new_accounts']),
        'Билеты: {} pending · {} accepted · {} RUB · {} ֏'.format(
            stats['tickets_pending'],
            stats['tickets_accepted'],
            stats['tickets_revenue'],
            stats['tickets_revenue_amd'],
        ),
        '',
        '<b>Модерация</b>',
        'Баг-репорты за сутки: {} ({} pending)'.format(stats['bugs_total'], stats['bugs_pending']),
        'Корпоративные заявки: {}'.format(stats['corporate_orders']),
        'Сейчас в очереди: 🐞 {} · 🎫 {}'.format(
            stats['pending_bugs_now'],
            stats['pending_tickets_now'],
        ),
    ]
    if stats['stuck_tickets_now']:
        lines.extend([
            '⚠️ Зависшие билеты (pending + payment id &gt; 30 мин): <b>{}</b>'.format(
                stats['stuck_tickets_now'],
            ),
        ])
    lines.extend([
        '',
        'Сгенерировано: {}'.format(timezone.localtime(stats['until']).strftime('%d.%m.%Y %H:%M')),
    ])
    return _join_lines(lines)
