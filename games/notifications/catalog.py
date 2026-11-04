"""Every notification kind: how it folds, renders and stays valid.

Rendering happens on read, so each kind re-reads its objects by id.
"""

from __future__ import annotations

from datetime import timedelta

from django.urls import reverse

from games.notifications.kinds import (
    GROUP_CLUB,
    GROUP_FRIENDS,
    GROUP_GAMES,
    GROUP_OFFERS,
    GROUP_SUPPORT,
    NotificationKind,
    register,
)

# ---- friends ---------------------------------------------------------------


def _friend_sender(notification):
    from games.models import Profile

    return Profile.objects.filter(
        user_id=notification.actor_id, is_hidden=False, user__is_active=True,
    ).first()


def _friend_request_valid(notification) -> bool:
    from games.community.friends import RELATION_INCOMING, relation

    if notification.actor_id is None or _friend_sender(notification) is None:
        return False
    return relation(notification.user, notification.actor_id) == RELATION_INCOMING


def _friend_request_render(notification):
    sender = _friend_sender(notification)
    if sender is None:
        return None
    return {
        'title': '{} хочет добавить вас в друзья'.format(sender.display_name or 'Игрок'),
        'body': '',
        'url': reverse('new_friends'),
        'actor_url': reverse('new_player_profile', kwargs={'public_id': sender.public_id}),
    }


def _friend_request_telegram(notification):
    from games.ops.telegram.bot.user_notices import format_friend_request_notice

    sender = _friend_sender(notification)
    if sender is None:
        return None
    return {'text': format_friend_request_notice(sender)}


FRIEND_REQUEST = register(NotificationKind(
    key='friend.request',
    group=GROUP_FRIENDS,
    group_key=lambda data: str(data['from_user_id']),
    render=_friend_request_render,
    render_telegram=_friend_request_telegram,
    is_valid=_friend_request_valid,
    # Cancelling and re-sending must not turn the bot into a way to ping someone.
    telegram_cooldown=timedelta(days=7),
    mark_read_on=('new_friends', 'ui_friends'),
))

# ---- support ---------------------------------------------------------------


def _support_message(notification):
    from games.models import BugReportMessage

    return (
        BugReportMessage.objects
        .select_related('report', 'report__task', 'report__task__task_group', 'report__game', 'report__game__project')
        .filter(pk=notification.payload.get('message_id'))
        .first()
    )


def _support_reply_render(notification):
    from games.site.pages.profile.feedback import profile_reports_path

    message = _support_message(notification)
    if message is None:
        return None
    text = message.text or ''
    return {
        'title': 'Ответ по вашему сообщению о проблеме',
        'body': text if len(text) <= 200 else text[:199].rstrip() + '…',
        'url': profile_reports_path(report_id=message.report_id),
    }


def _support_reply_telegram(notification):
    from games.ops.telegram.bot.notify import format_bug_report_admin_reply_message

    message = _support_message(notification)
    if message is None:
        return None
    return {'text': format_bug_report_admin_reply_message(message)}


SUPPORT_REPLY = register(NotificationKind(
    key='support.reply',
    group=GROUP_SUPPORT,
    group_key=lambda data: str(data['report_id']),
    render=_support_reply_render,
    render_telegram=_support_reply_telegram,
    is_valid=lambda notification: _support_message(notification) is not None,
    telegram_delay=timedelta(0),
    # Each new answer in the thread is news.
    telegram_on_merge=True,
))

# ---- club ------------------------------------------------------------------


def _club_active_render(_notification):
    return {
        'title': 'Клубная подписка Inter Oves активна',
        'body': 'Приглашение в закрытый чат клуба отправили вам в Telegram.',
        'url': reverse('new_subscription'),
    }


def _club_active_telegram(_notification):
    from games.ops.telegram.transport.config import club_invite_url

    invite_url = club_invite_url()
    if not invite_url:
        return None
    return {
        'text': (
            '✅ <b>Клубная подписка Inter Oves активна!</b>\n\n'
            'Вступите в закрытый чат клуба Inter Oves Elite:\n{}'.format(invite_url)
        ),
    }


CLUB_ACTIVE = register(NotificationKind(
    key='club.active',
    group=GROUP_CLUB,
    group_key=lambda data: str(data['subscription_id']),
    render=_club_active_render,
    render_telegram=_club_active_telegram,
    telegram_delay=timedelta(0),
    urgent=True,
    mark_read_on=('new_subscription', 'ui_subscription'),
))


def _club_renewal_failed_valid(notification) -> bool:
    """Stale once a later charge went through or the subscription is gone."""
    from games.models import ClubSubscription, ClubYooKassaPayment

    subscription = ClubSubscription.objects.filter(pk=notification.payload.get('subscription_id')).first()
    if subscription is None:
        return False
    failed = ClubYooKassaPayment.objects.filter(pk=notification.payload.get('payment_id')).first()
    later = ClubYooKassaPayment.objects.filter(
        club_subscription=subscription, status=ClubYooKassaPayment.STATUS_SUCCEEDED,
    )
    if failed is not None:
        later = later.filter(created_at__gt=failed.created_at)
    return not later.exists()


def _club_renewal_failed_render(_notification):
    return {
        'title': 'Не получилось продлить клубную подписку',
        'body': 'Банк отклонил списание за следующий месяц. Проверьте карту или продлите подписку вручную.',
        'url': reverse('new_subscription'),
    }


def _club_renewal_failed_telegram(notification):
    from games.ops.telegram.links import site_base_url

    item = _club_renewal_failed_render(notification)
    return {
        'text': '⚠️ <b>{}</b>\n\n{}\n\n<a href="{}">Страница подписки</a>'.format(
            item['title'], item['body'], site_base_url() + item['url'],
        ),
    }


CLUB_RENEWAL_FAILED = register(NotificationKind(
    key='club.renewal_failed',
    group=GROUP_CLUB,
    group_key=lambda data: str(data['subscription_id']),
    render=_club_renewal_failed_render,
    render_telegram=_club_renewal_failed_telegram,
    is_valid=_club_renewal_failed_valid,
    telegram_delay=timedelta(0),
    mark_read_on=('new_subscription', 'ui_subscription'),
))

# ---- friends: accepted -----------------------------------------------------


def _friend_accepted_valid(notification) -> bool:
    from games.community.friends import RELATION_FRIENDS, relation

    if notification.actor_id is None or _friend_sender(notification) is None:
        return False
    return relation(notification.user, notification.actor_id) == RELATION_FRIENDS


def _friend_accepted_render(notification):
    friend = _friend_sender(notification)
    if friend is None:
        return None
    return {
        'title': '{} теперь у вас в друзьях'.format(friend.display_name or 'Игрок'),
        'body': 'Сравнивайте результаты: в таблицах есть фильтр «Друзья».',
        'url': reverse('new_player_profile', kwargs={'public_id': friend.public_id}),
    }


def _friend_accepted_telegram(notification):
    import html

    from games.ops.telegram.links import site_base_url

    friend = _friend_sender(notification)
    if friend is None:
        return None
    page = site_base_url() + reverse('new_player_profile', kwargs={'public_id': friend.public_id})
    return {
        'text': '🤝 <b>{}</b> теперь у вас в друзьях на Интеровесе.\n\n<a href="{}">Страница игрока</a>'.format(
            html.escape(friend.display_name or 'Игрок', quote=False), page,
        ),
    }


FRIEND_ACCEPTED = register(NotificationKind(
    key='friend.accepted',
    group=GROUP_FRIENDS,
    group_key=lambda data: str(data['friend_user_id']),
    render=_friend_accepted_render,
    render_telegram=_friend_accepted_telegram,
    is_valid=_friend_accepted_valid,
    telegram_cooldown=timedelta(days=7),
))

# ---- offers ----------------------------------------------------------------

# game -> (model path, author list page url name, list anchors offers by id, «ваш …» in the accusative)
# Link the HTML list page, never ``*_detail``: that one is the JSON save API.
OFFER_GAMES = {
    'salad': ('games.puzzles.salad.models.WordSaladOffer', 'new_create_salad', True, 'ваш салатик'),
    'ladder': ('games.puzzles.raddle.models.LadderOffer', 'new_create_ladder', True, 'вашу лесенку'),
    'alphabetty': ('games.puzzles.alphabetty.models.AlphabettyOffer', 'new_create_alphabetty', True, 'вашу алфавитку'),
    'censorly': ('games.puzzles.censorly.models.CensorlyOffer', 'new_create_censorly', False, 'вашу цензурку'),
}


def _offer(notification):
    from django.utils.module_loading import import_string

    spec = OFFER_GAMES.get(notification.payload.get('game'))
    if spec is None:
        return None
    return import_string(spec[0]).objects.filter(pk=notification.payload.get('offer_id')).first()


def _offer_url(notification) -> str:
    spec = OFFER_GAMES[notification.payload['game']]
    url = reverse(spec[1])
    if spec[2]:
        url += '#offer-{}'.format(notification.payload['offer_id'])
    return url


def _offer_status_is(status):
    def check(notification) -> bool:
        offer = _offer(notification)
        return offer is not None and offer.status == status
    return check


def _offer_number(offer) -> str:
    link = getattr(offer, 'accepted_link', None)
    return str(link.number) if link is not None and link.number else ''


def _offer_render(title_format, *, with_note):
    def render(notification):
        offer = _offer(notification)
        if offer is None:
            return None
        noun = OFFER_GAMES[notification.payload['game']][3]
        body = ''
        if with_note:
            body = (getattr(offer, 'admin_note', '') or '').strip()
        else:
            number = _offer_number(offer)
            body = 'Выйдет в выпуске №{}.'.format(number) if number else 'Спасибо, что придумали!'
        if len(body) > 200:
            body = body[:199].rstrip() + '…'
        return {'title': title_format.format(Noun=noun[:1].upper() + noun[1:]), 'body': body, 'url': _offer_url(notification)}
    return render


def _offer_telegram(render):
    def telegram(notification):
        import html

        from games.ops.telegram.links import site_base_url

        item = render(notification)
        if item is None:
            return None
        lines = ['🧩 <b>{}</b>'.format(html.escape(item['title'], quote=False))]
        if item['body']:
            lines += ['', html.escape(item['body'], quote=False)]
        lines += ['', '<a href="{}">Открыть</a>'.format(site_base_url() + item['url'])]
        return {'text': '\n'.join(lines)}
    return telegram


_offer_accepted_render = _offer_render('{Noun} приняли', with_note=False)
_offer_returned_render = _offer_render('{Noun} вернули на доработку', with_note=True)
_offer_rejected_render = _offer_render('{Noun} не взяли', with_note=True)

OFFER_ACCEPTED = register(NotificationKind(
    key='offer.accepted',
    group=GROUP_OFFERS,
    group_key=lambda data: '{}:{}'.format(data['game'], data['offer_id']),
    render=_offer_accepted_render,
    render_telegram=_offer_telegram(_offer_accepted_render),
    is_valid=_offer_status_is('accepted'),
))

OFFER_RETURNED = register(NotificationKind(
    key='offer.returned',
    group=GROUP_OFFERS,
    group_key=lambda data: '{}:{}'.format(data['game'], data['offer_id']),
    render=_offer_returned_render,
    render_telegram=_offer_telegram(_offer_returned_render),
    is_valid=_offer_status_is('draft'),
    telegram_on_merge=True,
))

OFFER_REJECTED = register(NotificationKind(
    key='offer.rejected',
    group=GROUP_OFFERS,
    group_key=lambda data: '{}:{}'.format(data['game'], data['offer_id']),
    render=_offer_rejected_render,
    render_telegram=_offer_telegram(_offer_rejected_render),
    is_valid=_offer_status_is('rejected'),
))


def notify_offer(kind_key: str, game: str, offer) -> None:
    """Hook for the offer workflows (games.club.offers.*, console censorly)."""
    from games.notifications.service import notify

    if getattr(offer, 'user_id', None):
        notify(offer.user_id, kind_key, game=game, offer_id=offer.pk)


def notifies_author(kind_key: str, game: str):
    """Decorate an offer workflow step that returns the offer; the author hears about it."""
    import functools

    def decorate(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            offer = func(*args, **kwargs)
            notify_offer(kind_key, game, offer)
            return offer
        return wrapper
    return decorate


# ---- games (opt-in group) --------------------------------------------------


def _game(notification):
    from games.models import Game

    return Game.objects.filter(id=notification.payload.get('game_id')).first()


def _game_name(game) -> str:
    return game.get_no_html_name() if hasattr(game, 'get_no_html_name') else str(game.pk)


def _game_clock(notification, at) -> str:
    from games.notifications.prefs import user_timezone

    months = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
              'сентября', 'октября', 'ноября', 'декабря')
    local = at.astimezone(user_timezone(getattr(notification.user, 'profile', None)))
    return '{} {} в {}'.format(local.day, months[local.month - 1], local.strftime('%H:%M'))


def _game_path(game, suffix='') -> str:
    from games.ops.telegram.links import game_play_path

    return game_play_path(game) + suffix


def _game_telegram(render, icon):
    def telegram(notification):
        import html

        from games.ops.telegram.links import site_base_url

        item = render(notification)
        if item is None:
            return None
        lines = ['{} <b>{}</b>'.format(icon, html.escape(item['title'], quote=False))]
        if item['body']:
            lines += ['', html.escape(item['body'], quote=False)]
        lines += ['', '<a href="{}">Открыть</a>'.format(site_base_url() + item['url'])]
        return {'text': '\n'.join(lines)}
    return telegram


def _team_name(notification) -> str:
    from games.models import Team

    team = Team.objects.filter(pk=notification.payload.get('team_id')).first()
    return (team.visible_name or team.name) if team is not None else ''


def _game_registered_valid(notification) -> bool:
    from games.models import Registration

    return Registration.objects.filter(
        game_id=notification.payload.get('game_id'), team_id=notification.payload.get('team_id'),
    ).exists()


def _game_registered_render(notification):
    game = _game(notification)
    if game is None:
        return None
    team = _team_name(notification)
    title = 'Команда «{}» зарегистрирована на «{}»'.format(team, _game_name(game)) if team else (
        'Ваша команда зарегистрирована на «{}»'.format(_game_name(game))
    )
    body = 'Старт {}.'.format(_game_clock(notification, game.start_time)) if game.start_time else ''
    return {'title': title, 'body': body, 'url': _game_path(game)}


GAME_REGISTERED = register(NotificationKind(
    key='game.registered',
    group=GROUP_GAMES,
    group_key=lambda data: '{}:{}'.format(data['game_id'], data['team_id']),
    render=_game_registered_render,
    render_telegram=_game_telegram(_game_registered_render, '📝'),
    is_valid=_game_registered_valid,
    once=True,
))


def _game_starts_soon_valid(notification) -> bool:
    from django.utils import timezone as dj_timezone

    game = _game(notification)
    return game is not None and game.end_time is not None and dj_timezone.now() < game.end_time


def _game_starts_soon_render(notification):
    game = _game(notification)
    if game is None:
        return None
    body = 'Старт {}.'.format(_game_clock(notification, game.start_time)) if game.start_time else ''
    return {'title': 'Скоро начнётся «{}»'.format(_game_name(game)), 'body': body, 'url': _game_path(game)}


GAME_STARTS_SOON = register(NotificationKind(
    key='game.starts_soon',
    group=GROUP_GAMES,
    group_key=lambda data: str(data['game_id']),
    render=_game_starts_soon_render,
    render_telegram=_game_telegram(_game_starts_soon_render, '⏰'),
    is_valid=_game_starts_soon_valid,
    telegram_delay=timedelta(0),
    urgent=True,
    once=True,
))


def _game_results_render(notification):
    game = _game(notification)
    if game is None:
        return None
    place = notification.payload.get('place')
    teams = notification.payload.get('teams')
    team = _team_name(notification)
    if place:
        body = '{} — {} место{}.'.format(
            '«{}»'.format(team) if team else 'Ваша команда', place, ' из {}'.format(teams) if teams else '',
        )
    else:
        body = 'Таблица результатов готова.'
    return {
        'title': 'Итоги «{}»'.format(_game_name(game)),
        'body': body,
        'url': _game_path(game, 'tournament-results/'),
    }


GAME_RESULTS = register(NotificationKind(
    key='game.results',
    group=GROUP_GAMES,
    group_key=lambda data: '{}:{}'.format(data['game_id'], data.get('team_id') or ''),
    render=_game_results_render,
    render_telegram=_game_telegram(_game_results_render, '🏁'),
    once=True,
))


def _game_broadcast_render(notification):
    game = _game(notification)
    if game is None:
        return None
    text = str(notification.payload.get('text') or '')
    return {
        'title': 'Организаторы «{}»'.format(_game_name(game)),
        'body': text if len(text) <= 300 else text[:299].rstrip() + '…',
        'url': _game_path(game),
    }


GAME_BROADCAST = register(NotificationKind(
    key='game.broadcast',
    group=GROUP_GAMES,
    # Every message is its own notification.
    group_key=lambda data: '{}:{}'.format(data['game_id'], data['message_id']),
    render=_game_broadcast_render,
    render_telegram=_game_telegram(_game_broadcast_render, '📣'),
    telegram_delay=timedelta(0),
    urgent=True,
))


# ---- offers: published -----------------------------------------------------

OFFER_NOMINATIVE = {
    'salad': 'ваш салатик', 'ladder': 'ваша лесенка', 'alphabetty': 'ваша алфавитка', 'censorly': 'ваша цензурка',
}


def _offer_published_render(notification):
    offer = _offer(notification)
    if offer is None:
        return None
    from games.models import Game
    from games.task_group.navigation import play_url_for_task_group

    number = notification.payload.get('number')
    game = Game.objects.filter(id=notification.payload['game']).first()
    if game is None:
        return None
    return {
        'title': 'Сегодня в выпуске №{} — {}'.format(number, OFFER_NOMINATIVE[notification.payload['game']]),
        'body': 'Посмотрите, как его решают: результаты появятся в таблице.',
        'url': play_url_for_task_group(game, number),
    }


OFFER_PUBLISHED = register(NotificationKind(
    key='offer.published',
    group=GROUP_OFFERS,
    group_key=lambda data: '{}:{}'.format(data['game'], data['offer_id']),
    render=_offer_published_render,
    render_telegram=_offer_telegram(_offer_published_render),
    is_valid=_offer_status_is('accepted'),
    once=True,
))
