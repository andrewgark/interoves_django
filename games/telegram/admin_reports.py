"""Calendar-based Telegram activity reports for the site administrator."""

from datetime import date, datetime, time, timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from games.models import PlayerCompletedGame, PlayerStartedGame
from games.telegram.models import TelegramAdminReport
from games.telegram.notify import _join_lines, send_admin_message, telegram_admin_configured

GAME_LABELS = {
    'raddle': '🪜 «Лесенки»',
    'word_salad': '🥗 «Салатики»',
    'alphabet': '🔤 «Алфавитки»',
}
METRIC_LABELS = {
    'players': 'Всего уникальных игроков',
    'ladder': GAME_LABELS['raddle'],
    'salad': GAME_LABELS['word_salad'],
    'alphabet': GAME_LABELS['alphabet'],
    'new_users': 'Новые пользователи',
    'started': 'Начатые игры',
    'completed': 'Завершённые игры',
}


def _local_boundary(day, at=time.min):
    return timezone.make_aware(datetime.combine(day, at), timezone.get_current_timezone())


def report_periods(now=None):
    """Return (kind, current, comparison periods) using the project's local TZ."""
    now = timezone.localtime(now or timezone.now())
    today = now.date()
    if now.weekday() == 0:
        start_day = today - timedelta(days=7)
        kind = TelegramAdminReport.REPORT_WEEKLY
        previous = (start_day - timedelta(days=7), start_day)
    else:
        start_day = today - timedelta(days=1)
        kind = TelegramAdminReport.REPORT_DAILY
        previous = (start_day - timedelta(days=1), start_day)
    current = (start_day, today if kind == 'weekly' else start_day + timedelta(days=1))
    if kind == 'weekly':
        comparisons = [previous]
    else:
        comparisons = [previous, (start_day - timedelta(days=7), start_day - timedelta(days=6))]
    return kind, tuple(_local_boundary(d) for d in current), [tuple(_local_boundary(d) for d in p) for p in comparisons]


def _actor_counts(qs):
    row = qs.aggregate(
        registered=Count('user_id', filter=Q(user_id__isnull=False), distinct=True),
        anonymous=Count('anon_key', filter=Q(anon_key__isnull=False) & ~Q(anon_key=''), distinct=True),
    )
    return row['registered'] or 0, row['anonymous'] or 0


def collect_report_stats(since, until):
    started = PlayerStartedGame.objects.filter(started_at__gte=since, started_at__lt=until)
    completed = PlayerCompletedGame.objects.filter(completed_at__gte=since, completed_at__lt=until)
    registered, anonymous = _actor_counts(started)
    result = {
        'players': registered + anonymous,
        'registered_players': registered,
        'anonymous_players': anonymous,
        'started': started.count(),
        'completed': completed.count(),
        'new_users': User.objects.filter(date_joined__gte=since, date_joined__lt=until).count(),
    }
    # ``ladder``/``salad`` are canonical.  Include the legacy aliases so
    # historical rows remain visible in comparisons after the naming change.
    for key, game_kinds in (
        ('ladder', ('ladder', 'raddle')),
        ('salad', ('salad', 'word_salad')),
        ('alphabet', ('alphabet',)),
    ):
        reg, anon = _actor_counts(started.filter(game_kind__in=game_kinds))
        result[key] = reg + anon
        result[key + '_registered'] = reg
        result[key + '_anonymous'] = anon
    return result


def _number(value):
    return '{:,}'.format(value).replace(',', ' ')


def format_comparison(value, baseline):
    delta = value - baseline
    if not delta:
        return '⚪️ 0'
    if baseline:
        percent = abs(delta) / abs(baseline) * 100
        percent_text = '{:.1f}'.format(percent).rstrip('0').rstrip('.')
        percent_text = percent_text.replace('.', ',')
        suffix = ' ({}{}%)'.format('+' if delta > 0 else '−', percent_text)
    else:
        suffix = ' (н/д)'
    return '{} {}{}'.format('🟢📈' if delta > 0 else '🔴📉', '+' + _number(delta) if delta > 0 else '−' + _number(abs(delta)), suffix)


def build_admin_report(*, now=None):
    kind, current, comparisons = report_periods(now)
    current_stats = collect_report_stats(*current)
    comparison_stats = [collect_report_stats(*period) for period in comparisons]
    title = 'Недельный отчёт' if kind == TelegramAdminReport.REPORT_WEEKLY else 'Дневной отчёт'
    start_date = timezone.localtime(current[0]).strftime('%d.%m.%Y')
    end_date = (timezone.localtime(current[1]) - timedelta(seconds=1)).strftime('%d.%m.%Y')
    period_text = start_date if start_date == end_date else '{} — {}'.format(start_date, end_date)

    def comparison_line(key):
        values = [format_comparison(current_stats[key], baseline[key]) for baseline in comparison_stats]
        if kind == TelegramAdminReport.REPORT_DAILY:
            return 'вчера / неделя: {} / {}'.format(*values)
        return 'пред. неделя: {}'.format(values[0])

    lines = ['<b>📊 {} · {}</b>'.format(title, period_text), '']
    lines.append('<b>Игроки</b>')
    lines.append('{} всего · {} зарегистрированных · {} анонимов'.format(
        _number(current_stats['players']),
        _number(current_stats['registered_players']),
        _number(current_stats['anonymous_players']),
    ))
    lines.append('Новые пользователи: {}'.format(_number(current_stats['new_users'])))
    lines.append('')
    lines.append('<b>Игры</b>')
    for key in ('ladder', 'salad', 'alphabet'):
        lines.append('{}: {} · {}'.format(METRIC_LABELS[key], _number(current_stats[key]), comparison_line(key)))
    lines.append('')
    lines.append('<b>Итого</b>')
    lines.append('Начали: {} · {}'.format(_number(current_stats['started']), comparison_line('started')))
    lines.append('Завершили: {} · {}'.format(_number(current_stats['completed']), comparison_line('completed')))
    comparison_legend = 'вчера / неделя' if kind == TelegramAdminReport.REPORT_DAILY else 'пред. неделя'
    lines.extend(['', 'Сравнения: {} · 🟢 рост · 🔴 снижение · ⚪️ без изменений'.format(comparison_legend)])
    return _join_lines(lines), kind, current


def process_admin_report_tick(*, now=None):
    now = timezone.localtime(now or timezone.now())
    if now.hour != 0 or not (25 <= now.minute <= 29) or not telegram_admin_configured():
        return {'sent': 0, 'skipped': 1}
    text, kind, (start, end) = build_admin_report(now=now)
    try:
        with transaction.atomic():
            marker = TelegramAdminReport.objects.create(
                report_type=kind, period_start=start, period_end=end,
            )
    except IntegrityError:
        return {'sent': 0, 'skipped': 1}
    if send_admin_message(text, force=True):
        return {'sent': 1, 'skipped': 0}
    marker.delete()
    return {'sent': 0, 'skipped': 0}
