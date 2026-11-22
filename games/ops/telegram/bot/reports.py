"""Calendar-based Telegram activity reports for the site administrator."""

import logging
from datetime import datetime, time, timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from games.club.metrics import PROVIDER_TRIBUTE, PROVIDER_YOOKASSA, club_period_stats, club_snapshot
from games.daily.catalog.censorly import CENSORLY_GAME_ID
from games.models import PlayerCompletedGame, PlayerStartedGame
from games.ops.telegram.bot.notify import _join_lines, send_admin_message, telegram_admin_configured
from games.ops.telegram.models import TelegramAdminReport

logger = logging.getLogger('application')

GAMES = (
    # report key, emoji + title, game kind in the votes helper
    ('ladder', '🪜 Лесенка', 'ladder'),
    ('salad', '🥗 Салатик', 'salad'),
    ('alphabet', '🔤 Алфавитка', 'alphabet'),
    ('censorly', '📰 Цензурка', 'censorly'),
)
MONTHS_RU = (
    '', 'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)
WEEKDAYS_RU = ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье')
WEEKDAYS_RU_DATIVE = ('понедельнику', 'вторнику', 'среде', 'четвергу', 'пятнице', 'субботе', 'воскресенью')
WEEKDAYS_RU_LAST_WEEK = (
    'прошлому понедельнику', 'прошлому вторнику', 'прошлой среде', 'прошлому четвергу',
    'прошлой пятнице', 'прошлой субботе', 'прошлому воскресенью',
)


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
        ('censorly', (CENSORLY_GAME_ID,)),
    ):
        reg, anon = _actor_counts(started.filter(game_kind__in=game_kinds))
        result[key] = reg + anon
        result[key + '_registered'] = reg
        result[key + '_anonymous'] = anon
    return result


def _number(value):
    return '{:,}'.format(value).replace(',', '\u202f')


def format_comparison(value, baseline):
    """Relative change, short: ``↑12%``, ``↓3%``, ``=``; ``новое`` from zero."""
    if value == baseline:
        return '='
    if not baseline:
        return 'новое'
    percent = round(abs(value - baseline) / abs(baseline) * 100)
    arrow = '↑' if value > baseline else '↓'
    return '{}{}%'.format(arrow, percent) if percent else '{}&lt;1%'.format(arrow)


def _signed(delta):
    if not delta:
        return '±0'
    return '+{}'.format(_number(delta)) if delta > 0 else '−{}'.format(_number(abs(delta)))


def _day_label(day):
    return '{} {}'.format(day.day, MONTHS_RU[day.month])


def _period_title(kind, current):
    start = timezone.localtime(current[0]).date()
    end = (timezone.localtime(current[1]) - timedelta(seconds=1)).date()
    if kind == TelegramAdminReport.REPORT_WEEKLY:
        if start.month == end.month:
            span = '{}–{}'.format(start.day, _day_label(end))
        else:
            span = '{} — {}'.format(_day_label(start), _day_label(end))
        return '<b>📊 Неделя {}</b>'.format(span), 'к прошлой неделе'
    weekday = start.weekday()
    title = '<b>📊 {}, {}</b>'.format(WEEKDAYS_RU[weekday].capitalize(), _day_label(start))
    return title, 'к {} · к {}'.format(WEEKDAYS_RU_DATIVE[(weekday - 1) % 7], WEEKDAYS_RU_LAST_WEEK[weekday])


def _deltas(key, current, baselines):
    return ' · '.join(format_comparison(current[key], baseline[key]) for baseline in baselines)


def _votes_by_game(current, weekly):
    from games.ops.analytics.retention_report import puzzle_votes

    try:
        rows = puzzle_votes(*current, per_game=7 if weekly else 1)
    except Exception:
        logger.exception('admin report: puzzle votes failed')
        return {}
    votes = {}
    for kind, number, likes, dislikes, low in rows:
        if weekly and not low:
            continue
        votes.setdefault(kind, []).append(
            '№{} 👍{} 👎{}{}'.format(number, likes, dislikes, ' 🔴' if low else ''),
        )
    return votes


def club_report_lines(current, comparisons, kind):
    snapshot = club_snapshot(current[1])
    previous_snapshot = club_snapshot(comparisons[0][1])
    period = club_period_stats(*current)
    period_name = 'за неделю' if kind == TelegramAdminReport.REPORT_WEEKLY else 'за день'

    lines = ['<b>👑 Клуб · подписок: {}</b> ({} {})'.format(
        _number(snapshot.total), _signed(snapshot.total - previous_snapshot.total), period_name,
    )]
    gift_parts = ['выданы {}'.format(_number(snapshot.gift_admin))]
    if snapshot.gift_purchased:
        gift_parts.append('куплены {}'.format(_number(snapshot.gift_purchased)))
    lines.append('Оплачены {} (ЮKassa {} · Tribute {}) · подарки {} ({}) · вручную {}'.format(
        _number(snapshot.paid), _number(snapshot.paid_yookassa), _number(snapshot.paid_tribute),
        _number(snapshot.gifts), ' · '.join(gift_parts), _number(snapshot.manual),
    ))
    lines.append('{}: новых оплат {} (ЮKassa {} · Tribute {}) · продлений {} · '
                 'отключили автопродление {} · активировано подарков {}'.format(
                     period_name.capitalize(),
                     _number(period.first_payments_total),
                     _number(period.first_payments[PROVIDER_YOOKASSA]),
                     _number(period.first_payments[PROVIDER_TRIBUTE]),
                     _number(period.renewals_total), _number(period.auto_renew_cancellations),
                     _number(period.gifts_claimed),
                 ))
    if snapshot.paid_ending_soon:
        lines.append('⚠️ Истекают за 7 дней без автопродления: {}'.format(_number(snapshot.paid_ending_soon)))
    return lines


def _retention_lines(current, weekly):
    from games.ops.analytics import retention_report

    # The daily report keeps the two headline numbers; the week gets flows,
    # sources and the hub experiment.
    builders = (
        (lambda: retention_report.regulars_lines(current[1]), 2 if weekly else 1),
        (lambda: retention_report.newcomer_lines(current[1]), None if weekly else 1),
    )
    if weekly:
        builders += ((retention_report.hub_experiment_lines, None),)
    lines = []
    for builder, keep in builders:
        try:
            lines.extend(builder()[:keep])
        except Exception:
            logger.exception('admin report: retention section failed')
    return lines


def build_admin_report(*, now=None):
    kind, current, comparisons = report_periods(now)
    weekly = kind == TelegramAdminReport.REPORT_WEEKLY
    stats = collect_report_stats(*current)
    baselines = [collect_report_stats(*period) for period in comparisons]
    title, legend = _period_title(kind, current)

    started, completed = stats['started'], stats['completed']
    lines = [
        title,
        'Изменения: {}'.format(legend),
        '',
        '<b>👥 Игроки: {}</b> · {}'.format(_number(stats['players']), _deltas('players', stats, baselines)),
        'С аккаунтом {} · анонимы {} · новых аккаунтов {}'.format(
            _number(stats['registered_players']), _number(stats['anonymous_players']),
            _number(stats['new_users']),
        ),
        'Доиграли {} из {} начатых{}'.format(
            _number(completed), _number(started),
            ' ({}%)'.format(round(100 * completed / started)) if started else '',
        ),
        '',
        '<b>🎮 По играм</b>',
    ]
    votes = _votes_by_game(current, weekly)
    for key, label, vote_kind in GAMES:
        line = '{}: {} · {}'.format(label, _number(stats[key]), _deltas(key, stats, baselines))
        if votes.get(vote_kind):
            line += ' · ' + ', '.join(votes[vote_kind])
        lines.append(line)
    if weekly and votes:
        lines.append('🔴 — меньше половины лайков среди 10+ оценок')

    lines.append('')
    lines.extend(club_report_lines(current, comparisons, kind))

    retention = _retention_lines(current, weekly)
    if retention:
        lines.extend(['', '<b>📈 Удержание</b>', *retention])

    from games.gameplay.offline_telemetry import report_lines as offline_lines

    offline = offline_lines(*current)
    if offline:
        lines.extend(['', *offline])
    return _join_lines(lines), kind, current


STALE_BUG_DAYS = 7
STALE_BUGS_SHOWN = 15
STALE_BUG_WINDOW_DAYS = 60


def _stale_bug_queryset(now):
    """Open ordinary bug reports older than a week whose last word is the player's."""
    from django.db.models import OuterRef, Subquery

    from games.models import BugReport, BugReportMessage

    last_role = (
        BugReportMessage.objects.filter(report=OuterRef('pk'))
        .order_by('-created_at', '-pk').values('author_role')[:1]
    )
    # Censorly match reports are settled with buttons, not with a reply.
    return (
        BugReport.objects
        .filter(
            report_type=BugReport.REPORT_TYPE_BUG,
            status__in=(BugReport.STATUS_PENDING, BugReport.STATUS_REVIEWED),
            time__lt=now - timedelta(days=STALE_BUG_DAYS),
        )
        .annotate(last_role=Subquery(last_role))
        .exclude(last_role=BugReportMessage.ROLE_ADMIN)
    )


def stale_bug_reports(*, now=None):
    """(reports of the last STALE_BUG_WINDOW_DAYS, count of older ones) still waiting for an answer."""
    now = now or timezone.now()
    queryset = _stale_bug_queryset(now)
    window_start = now - timedelta(days=STALE_BUG_WINDOW_DAYS)
    recent = list(
        queryset.filter(time__gte=window_start)
        .select_related('user', 'user__profile', 'team').order_by('time')
    )
    return recent, queryset.filter(time__lt=window_start).count()


def build_stale_bugs_message(*, now=None) -> str | None:
    from games.ops.telegram.bot.notify import _bug_report_reporter, _escape
    from games.ops.telegram.links import bug_report_support_url, support_url

    now = now or timezone.now()
    stale, older = stale_bug_reports(now=now)
    if not stale and not older:
        return None
    lines = [
        '<b>🐞 Баги без ответа больше {} дней: {}</b>'.format(STALE_BUG_DAYS, len(stale) + older),
        'Открыты, последнее слово за автором. Ответить можно реплаем на сообщение о баге или в /support.',
        '',
    ]
    for report in stale[:STALE_BUGS_SHOWN]:
        days = (now - report.time).days
        lines.append('<a href="{}">#{}</a> · {} дн. · {} · {}'.format(
            _escape(bug_report_support_url(report)), report.pk, days,
            _bug_report_reporter(report), _escape(' '.join((report.text or '').split())[:60]),
        ))
    if len(stale) > STALE_BUGS_SHOWN:
        lines.append('… и ещё {}'.format(len(stale) - STALE_BUGS_SHOWN))
    if older:
        lines.append('Ещё {} старше {} дней — их видно в /support.'.format(older, STALE_BUG_WINDOW_DAYS))
    lines.extend(['', '<a href="{}">Очередь в /support</a>'.format(_escape(support_url('/support/pending/')))])
    return _join_lines(lines)


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
        if kind == TelegramAdminReport.REPORT_WEEKLY:
            # Sent once with the weekly report: the report marker guards both.
            try:
                stale = build_stale_bugs_message(now=now)
                if stale:
                    send_admin_message(stale, force=True)
            except Exception:
                logger.exception('admin report: stale bugs message failed')
        return {'sent': 1, 'skipped': 0}
    marker.delete()
    return {'sent': 0, 'skipped': 0}
