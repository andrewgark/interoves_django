import shlex
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db import connection
from django.db.models import Max
from django.utils import timezone

from games.access import game_has_ended, game_has_started
from games.models import (
    Attempt,
    BugReport,
    CorporateGameOrder,
    Game,
    GameTaskGroup,
    Registration,
    Task,
    TicketRequest,
)
from games.recheck import recheck_team_task_all_chronological
from games.telegram.config import (
    admin_is_muted,
    announce_chat_ids,
    clear_admin_mute,
    set_admin_mute,
)
from games.telegram.game_urls import admin_url, game_site_url
from games.telegram.notify import REGISTRATION_MILESTONES, _escape, _join_lines, send_admin_message


def _help_text() -> str:
    return _join_lines([
        '<b>Admin mode — команды</b>',
        '',
        '/status — health, деплой, очереди',
        '/pending — pending баги, билеты, корпоратив',
        '/game &lt;id&gt; — сводка по игре',
        '/stuck &lt;id&gt; [мин] — команды без прогресса',
        '/preflight &lt;id&gt; — чеклист перед стартом',
        '/extend &lt;id&gt; &lt;мин&gt; — продлить end_time',
        '/broadcast &lt;id&gt; &lt;текст&gt; — сообщение игрокам на сайте',
        '/recheck &lt;attempt_id&gt; — перепроверить посылки',
        '/deploy — версия деплоя',
        '/db — размер БД и фоновые миграции',
        '/infra — webhook и чаты бота',
        '/digest — дайджест за сутки',
        '/mute &lt;мин&gt; — заглушить рутину',
        '/unmute — включить уведомления',
        '',
        'Чат-мод: бот шлёт анонсы в группы из TELEGRAM_ANNOUNCE_CHAT_IDS.',
        'Включить анонсы игры: tags.telegram_announce = true в админке.',
    ])


def handle_admin_command(text: str) -> str:
    text = (text or '').strip()
    if not text:
        return 'Пустая команда. /help'

    if text.startswith('/'):
        parts = text.split(maxsplit=1)
        command = parts[0].lower().split('@')[0]
        args_text = parts[1] if len(parts) > 1 else ''
    else:
        command = text.lower()
        args_text = ''

    try:
        args = shlex.split(args_text)
    except ValueError as exc:
        return 'Ошибка разбора аргументов: {}'.format(exc)

    handlers = {
        '/start': _cmd_help,
        '/help': _cmd_help,
        '/status': _cmd_status,
        '/pending': _cmd_pending,
        '/game': _cmd_game,
        '/stuck': _cmd_stuck,
        '/preflight': _cmd_preflight,
        '/extend': _cmd_extend,
        '/broadcast': _cmd_broadcast,
        '/recheck': _cmd_recheck,
        '/deploy': _cmd_deploy,
        '/db': _cmd_db,
        '/infra': _cmd_infra,
        '/digest': _cmd_digest,
        '/mute': _cmd_mute,
        '/unmute': _cmd_unmute,
    }
    handler = handlers.get(command)
    if handler is None:
        return 'Неизвестная команда. /help'
    return handler(args)


def _cmd_help(_args) -> str:
    return _help_text()


def _get_game(game_id: str) -> Game | None:
    if not game_id:
        return None
    return Game.objects.filter(pk=game_id).first()


def _cmd_status(_args) -> str:
    from django.conf import settings
    from games.telegram.api import get_webhook_info

    pending_bugs = BugReport.objects.filter(status='Pending').count()
    pending_tickets = TicketRequest.objects.filter(status='Pending').count()
    pending_orders = CorporateGameOrder.objects.filter(email_sent=False).count()

    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        db_ok = False

    webhook = get_webhook_info() or {}
    mute = 'да' if admin_is_muted() else 'нет'
    version = getattr(settings, 'SITE_DEPLOY_VERSION', '') or '—'

    return _join_lines([
        '<b>Status</b>',
        '',
        'Deploy: <code>{}</code>'.format(_escape(version)),
        'DB: {}'.format('OK' if db_ok else 'FAIL'),
        'Admin mute: {}'.format(mute),
        'Pending: 🐞 {} · 🎫 {} · 🏢 {}'.format(pending_bugs, pending_tickets, pending_orders),
        'Announce chats: {}'.format(len(announce_chat_ids())),
        'Webhook: {}'.format(_escape(webhook.get('url') or '—')),
    ])


def _cmd_pending(_args) -> str:
    lines = ['<b>Pending</b>', '']

    bugs = BugReport.objects.filter(status='Pending').order_by('-time')[:5]
    if bugs:
        lines.append('<b>Баги:</b>')
        for bug in bugs:
            lines.append('#{} · {} · task #{}'.format(
                bug.pk, _escape(bug.game_id), _escape(getattr(bug.task, 'number', bug.task_id)),
            ))
        extra = BugReport.objects.filter(status='Pending').count() - len(bugs)
        if extra > 0:
            lines.append('… и ещё {}'.format(extra))
        lines.append('')

    tickets = TicketRequest.objects.filter(status='Pending').order_by('-time')[:5]
    if tickets:
        lines.append('<b>Билеты:</b>')
        for ticket in tickets:
            team = getattr(ticket.team, 'visible_name', None) or ticket.team_id or '—'
            lines.append('#{} · {} · {} ₽'.format(ticket.pk, _escape(team), ticket.money))
        lines.append('')

    orders = CorporateGameOrder.objects.order_by('-created_at')[:5]
    if orders:
        lines.append('<b>Корпоратив (последние):</b>')
        for order in orders:
            lines.append('#{} · {}'.format(order.pk, _escape(order.company_name)))

    if len(lines) == 2:
        lines.append('Очереди пусты.')
    return _join_lines(lines)


def _cmd_game(args) -> str:
    if not args:
        return 'Использование: /game &lt;id&gt;'
    game = _get_game(args[0])
    if game is None:
        return 'Игра «{}» не найдена.'.format(_escape(args[0]))

    registrations = Registration.objects.filter(game=game).count()
    pending_bugs = BugReport.objects.filter(game=game, status='Pending').count()
    started = game_has_started(game)
    ended = game_has_ended(game)
    start = timezone.localtime(game.get_visible_start_time()).strftime('%d.%m.%Y %H:%M')
    end = timezone.localtime(game.get_visible_end_time()).strftime('%d.%m.%Y %H:%M')

    return _join_lines([
        '<b>{}</b>'.format(_escape(game.get_no_html_name())),
        '',
        'ID: <code>{}</code>'.format(_escape(game.id)),
        'Старт: {} · Конец: {}'.format(start, end),
        'Статус: {}{}'.format(
            'идёт' if started and not ended else ('завершена' if ended else 'ещё не началась'),
            '' if game.is_ready else ' · ⚠️ not ready',
        ),
        'Регистрации: {}'.format(registrations),
        'Pending баги: {}'.format(pending_bugs),
        '<a href="{}">Сайт</a> · <a href="{}">Админка</a>'.format(
            _escape(game_site_url(game)),
            admin_url('/admin/games/game/{}/change/'.format(game.id)),
        ),
    ])


def _cmd_stuck(args) -> str:
    if not args:
        return 'Использование: /stuck &lt;game_id&gt; [minutes=30]'
    game = _get_game(args[0])
    if game is None:
        return 'Игра не найдена.'
    minutes = int(args[1]) if len(args) > 1 else 30
    cutoff = timezone.now() - timedelta(minutes=minutes)

    task_ids = list(
        Task.objects.filter(task_group__game_links__game=game, is_removed=False)
        .values_list('pk', flat=True)
        .distinct()
    )
    if not task_ids:
        return 'У игры нет заданий.'

    recent_ok_teams = set(
        Attempt.objects.filter(
            game=game, task_id__in=task_ids, status='Ok', time__gte=cutoff,
        ).exclude(team__isnull=True).values_list('team_id', flat=True)
    )

    stuck = []
    for reg in Registration.objects.filter(game=game).select_related('team'):
        team = reg.team
        if team is None or team.is_hidden or team.is_tester:
            continue
        if team.name in recent_ok_teams:
            continue
        last_attempt = Attempt.objects.filter(game=game, team=team).aggregate(last=Max('time'))['last']
        stuck.append((team.visible_name or team.name, last_attempt))

    stuck.sort(key=lambda row: row[1] or datetime.min.replace(tzinfo=dt_timezone.utc))
    lines = [
        '<b>Застряли · {}</b>'.format(_escape(game.get_no_html_name())),
        'Нет Ok-посылок за последние {} мин.'.format(minutes),
        '',
    ]
    for name, last_attempt in stuck[:15]:
        when = timezone.localtime(last_attempt).strftime('%H:%M') if last_attempt else '—'
        lines.append('• {} (последняя посылка {})'.format(_escape(name), when))
    if len(stuck) > 15:
        lines.append('… и ещё {}'.format(len(stuck) - 15))
    if not stuck:
        lines.append('Все активные команды с недавним прогрессом.')
    return _join_lines(lines)


def _cmd_preflight(args) -> str:
    if not args:
        return 'Использование: /preflight &lt;game_id&gt;'
    game = _get_game(args[0])
    if game is None:
        return 'Игра не найдена.'

    links = GameTaskGroup.objects.filter(game=game).count()
    tasks = Task.objects.filter(task_group__game_links__game=game, is_removed=False).distinct()
    task_count = tasks.count()
    without_checker = tasks.filter(checker__isnull=True, task_group__checker__isnull=True).count()
    hidden = Task.objects.filter(task_group__game_links__game=game, is_removed=True).distinct().count()
    registrations = Registration.objects.filter(game=game).count()

    issues = []
    if not game.is_ready:
        issues.append('is_ready = False')
    if links == 0:
        issues.append('нет кругов (GameTaskGroup)')
    if task_count == 0:
        issues.append('нет видимых заданий')
    if without_checker:
        issues.append('{} заданий без чекера'.format(without_checker))
    if registrations == 0:
        issues.append('нет регистраций')

    lines = [
        '<b>Preflight · {}</b>'.format(_escape(game.get_no_html_name())),
        '',
        'Кругов: {} · Заданий: {} · Скрытых: {}'.format(links, task_count, hidden),
        'Регистрации: {}'.format(registrations),
        'Announce chat: {}'.format('on' if (game.tags or {}).get('telegram_announce') else 'off'),
    ]
    if issues:
        lines.extend(['', '⚠️ ' + '; '.join(issues)])
    else:
        lines.extend(['', '✅ Критичных проблем не видно.'])
    return _join_lines(lines)


def _cmd_extend(args) -> str:
    if len(args) < 2:
        return 'Использование: /extend &lt;game_id&gt; &lt;minutes&gt;'
    game = _get_game(args[0])
    if game is None:
        return 'Игра не найдена.'
    try:
        minutes = int(args[1])
    except ValueError:
        return 'minutes должны быть числом.'
    if minutes <= 0 or minutes > 24 * 60:
        return 'minutes: от 1 до 1440.'

    old_end = game.get_visible_end_time()
    game.end_time = old_end + timedelta(minutes=minutes)
    if game.visible_end_time is not None:
        game.visible_end_time = game.visible_end_time + timedelta(minutes=minutes)
    game.save()

    new_end = timezone.localtime(game.get_visible_end_time()).strftime('%d.%m.%Y %H:%M')
    return 'Конец игры «{}» продлён на {} мин.\nНовое время: {}'.format(
        _escape(game.get_no_html_name()), minutes, new_end,
    )


def _cmd_broadcast(args) -> str:
    if len(args) < 2:
        return 'Использование: /broadcast &lt;game_id&gt; &lt;текст&gt;'
    game = _get_game(args[0])
    if game is None:
        return 'Игра не найдена.'
    message = ' '.join(args[1:]).strip()
    if not message:
        return 'Пустой текст.'

    from games.views.track import _broadcast_game_track_event_commit

    _broadcast_game_track_event_commit(
        game.id,
        'admin.broadcast',
        {'message': message, 'game_id': game.id},
    )
    return 'Broadcast отправлен в track.game.{}: {}'.format(_escape(game.id), _escape(message[:200]))


def _cmd_recheck(args) -> str:
    if not args:
        return 'Использование: /recheck &lt;attempt_id&gt;'
    try:
        attempt_id = int(args[0])
    except ValueError:
        return 'attempt_id должен быть числом.'

    try:
        recheck_team_task_all_chronological(None, attempt_id)
    except Exception as exc:
        return 'Recheck failed: {}'.format(_escape(exc))
    return 'Recheck запущен для attempt #{}.'.format(attempt_id)


def _cmd_deploy(_args) -> str:
    from django.conf import settings

    version = getattr(settings, 'SITE_DEPLOY_VERSION', '') or '—'
    return _join_lines([
        '<b>Deploy</b>',
        '',
        'SITE_DEPLOY_VERSION: <code>{}</code>'.format(_escape(version)),
        '<a href="{}">/meta/deploy-version/</a>'.format(admin_url('/meta/deploy-version/')),
    ])


def _cmd_db(_args) -> str:
    from games.models import Attempt as AttemptModel, ChainTaskState

    lines = ['<b>Database</b>', '', 'Engine: {}'.format(_escape(connection.vendor))]

    if connection.vendor == 'mysql':
        schema = connection.settings_dict.get('NAME', '')
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name, ROUND((data_length + index_length) / 1024 / 1024, 1)
                FROM information_schema.tables
                WHERE table_schema = %s AND table_name IN ('games_attempt', 'games_registration')
                """,
                [schema],
            )
            for table_name, size_mb in cursor.fetchall():
                lines.append('{}: {} MB'.format(table_name, size_mb))

    lines.extend([
        '',
        'Attempts: {}'.format(AttemptModel.objects.count()),
        'ChainTaskState: {}'.format(ChainTaskState.objects.count()),
    ])
    return _join_lines(lines)


def _cmd_infra(_args) -> str:
    from django.conf import settings
    from games.telegram.api import get_webhook_info
    from games.telegram.config import admin_chat_id

    webhook = get_webhook_info() or {}
    announce = announce_chat_ids()
    return _join_lines([
        '<b>Infra</b>',
        '',
        'Admin chat: <code>{}</code>'.format(_escape(admin_chat_id())),
        'Announce chats: {}'.format(', '.join('<code>{}</code>'.format(_escape(c)) for c in announce) or '—'),
        'Webhook URL: {}'.format(_escape(webhook.get('url') or '—')),
        'Pending updates: {}'.format(webhook.get('pending_update_count', '—')),
        'Secret configured: {}'.format('yes' if settings.TELEGRAM_WEBHOOK_SECRET else 'no'),
    ])


def _cmd_digest(_args) -> str:
    from games.telegram.digest import build_daily_digest

    return build_daily_digest()


def _cmd_mute(args) -> str:
    if not args:
        return 'Использование: /mute &lt;minutes&gt;'
    try:
        minutes = int(args[0])
    except ValueError:
        return 'minutes должны быть числом.'
    if minutes <= 0:
        return 'minutes > 0'
    set_admin_mute(minutes)
    return 'Рутинные admin-уведомления заглушены на {} мин.'.format(minutes)


def _cmd_unmute(_args) -> str:
    clear_admin_mute()
    return 'Admin-уведомления снова включены.'


def registration_milestone_reached(old_count: int, new_count: int) -> int | None:
    for milestone in REGISTRATION_MILESTONES:
        if old_count < milestone <= new_count:
            return milestone
    return None
