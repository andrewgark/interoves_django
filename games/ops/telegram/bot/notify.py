"""Messages the bot sends to the admin chat.

Conventions shared by every message here:

- a person is ``people.user_html``: their name linked to the /support card,
  their Telegram handle in brackets; teams link to their /support card;
- links go to /support (``links.*_support_url``); the Django admin only
  where /support has no page (donations, corporate orders);
- times are Moscow time (the project's TIME_ZONE), written ``dd.mm.yyyy HH:MM``;
- the text is Russian, raw codes are kept only inside ``<code>``.
"""

import html
import logging
import re
import socket
from typing import Iterable

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from games.models import (
    AlphabettyDictSuggestion,
    AlphabettyOffer,
    BugReport,
    BugReportMessage,
    CensorlyOffer,
    CorporateGameOrder,
    Donation,
    LadderOffer,
    TicketRequest,
    WordSaladOffer,
)
from games.ops.cron.runtime import deployment_environment
from games.ops.telegram.bot.alerts import publish_admin_alert
from games.ops.telegram.bot.people import actor_html, moscow_time, person_name, team_html, user_html
from games.ops.telegram.links import (
    admin_url,
    bug_report_support_url,
    game_site_url,
    game_support_url,
    site_base_url,
    support_url,
    task_play_url,
    task_support_url,
)
from games.ops.telegram.transport.api import send_message, send_message_result, send_photo
from games.ops.telegram.transport.config import (
    admin_chat_id,
    admin_is_muted,
    announce_chat_ids,
    club_invite_url,
    telegram_admin_configured,
    telegram_bot_configured,
)
from games.task_group.titles import task_display_name

logger = logging.getLogger('application')

CONTACT_METHOD_LABELS = dict(CorporateGameOrder.ContactMethod.choices)

SUPPORT_LINK_TEXT = 'Открыть в /support'
DJANGO_ADMIN_LINK_TEXT = 'Открыть в Django-админке'

TICKET_STATUS_LABELS = {
    'Pending': 'ждёт подтверждения',
    'Accepted': 'принята',
    'Rejected': 'отклонена',
}
DONATION_STATUS_LABELS = {
    'Pending': 'ждёт оплаты',
    'Confirmed': 'подтверждён',
    'Rejected': 'отклонён',
}
CLUB_PAYMENT_KIND_LABELS = {
    'initial_monthly': 'первый месячный платёж',
    'recurring_monthly': 'ежемесячное продление',
    'annual': 'годовая оплата',
    'yookassa': 'ЮKassa',
}
CLUB_PROVIDER_LABELS = {
    'yookassa': 'ЮKassa',
    'tribute': 'Tribute',
    'manual': 'вручную',
}

# A Telegram reply to one of these headers becomes an admin reply in the thread.
BUG_REPORT_HEADER_RE = re.compile(r'^\S+\s+(?:Баг|Жалоба на Цензурку|Ответ по багу)\s+#(\d+)')


def telegram_notify_configured() -> bool:
    return telegram_admin_configured()


def _escape(text) -> str:
    if text is None:
        return ''
    return html.escape(str(text), quote=False)


def _join_lines(lines: Iterable[str]) -> str:
    return '\n'.join(line for line in lines if line is not None)


def _link(url: str, text: str) -> str:
    return '<a href="{}">{}</a>'.format(_escape(url), _escape(text))


def _support_link(url: str) -> str:
    return _link(url, SUPPORT_LINK_TEXT)


def _provider_label(provider) -> str:
    return CLUB_PROVIDER_LABELS.get(str(provider or ''), str(provider or '—'))


def send_admin_message(text: str, *, reply_markup: dict | None = None, force: bool = False) -> bool:
    if not telegram_admin_configured():
        logger.debug('Telegram admin notify skipped: bot token or admin chat id is empty')
        return False
    if not force and admin_is_muted():
        logger.debug('Telegram admin notify skipped: muted')
        return False
    return send_message(admin_chat_id(), text, reply_markup=reply_markup)


def send_admin_message_result(
    text: str, *, reply_markup: dict | None = None, force: bool = False,
    reply_to_message_id: int | None = None,
) -> dict | None:
    """Like send_admin_message, but returns the sent message (for its ``message_id``)."""
    if not telegram_admin_configured():
        return None
    if not force and admin_is_muted():
        return None
    return send_message_result(
        admin_chat_id(), text, reply_markup=reply_markup, reply_to_message_id=reply_to_message_id,
    )


def notify_admin_alert_message(text: str, *, dedupe_key: str = '') -> bool:
    """Deliver a previously validated alert from the integrations worker."""
    if not text:
        return False
    cache_key = 'telegram:admin:delivered:{}'.format(dedupe_key) if dedupe_key else ''
    if cache_key and cache.get(cache_key):
        return True
    delivered = send_admin_message(text[:4000], force=True)
    if delivered and cache_key:
        cache.set(cache_key, 1, timeout=300)
    return delivered


def notify_admin_zero_duration_completion(*, game, task_group, actor, timing, completion) -> bool:
    """Queue an alert when a completed daily game has no measurable time.

    This is deliberately sent through the integrations queue: completion has
    already committed by the time this function is called, and Telegram must
    not participate in the gameplay transaction.
    """
    try:
        if isinstance(actor, dict):
            user = actor.get('user')
            team = actor.get('team')
            anon_key = actor.get('anon_key')
        else:
            user = getattr(actor, 'user', None)
            team = getattr(actor, 'team', None)
            anon_key = getattr(actor, 'anon_key', None)
        if team is not None:
            actor_key = 'team:{}'.format(team.pk)
        elif user is not None:
            actor_key = 'user:{}'.format(user.pk)
        else:
            actor_key = 'anon:{}'.format(str(anon_key or '')[:16])
        if not timing:
            timing_label = 'записи таймера нет'
        else:
            timing_label = 'накоплено {} мс'.format(
                timing.get('frozen_ms', timing.get('accumulated_ms', 0)),
            )
        record = completion.get('record') if isinstance(completion, dict) else None
        game_id = str(getattr(game, 'pk', game))
        task_group_id = getattr(task_group, 'pk', task_group)
        instance_id = getattr(record, 'game_instance_id', '') if record else ''
        text = _join_lines([
            '⚠️ <b>Игра завершена со временем 0:00</b>',
            '',
            'Игрок: {}'.format(actor_html(user=user, team=team, anon_key=anon_key)),
            'Игра: {} · группа заданий <code>{}</code>'.format(
                _link(game_support_url(game_id), game_id), _escape(task_group_id),
            ),
            'Таймер: {}'.format(_escape(timing_label)),
            'Прохождение: <code>{}</code>'.format(_escape(instance_id or '—')),
        ])
        return publish_admin_alert(
            alert='zero_duration_completion',
            dedupe_key='telegram.admin_alert:zero_duration_completion:{}:{}:{}'.format(
                game_id, task_group_id, instance_id or actor_key,
            ),
            payload={'text': text},
        )
    except Exception:
        logger.exception('Failed to notify admin about zero-duration completion')
        return False


def _deliver_admin_text(text: str, *, alert: str, delivery: str, dedupe_key: str = '') -> bool:
    if delivery == 'queue':
        return publish_admin_alert(
            alert=alert,
            dedupe_key=dedupe_key,
            payload={'text': text},
        )
    return send_admin_message(text, force=True)


def notify_admin_club_subscription_attempt_failed(
    user, *, provider: str, reason: str, message: str, currency: str = '',
) -> bool:
    """Notify the admin chat when an authenticated Club checkout cannot start."""
    try:
        lines = [
            '⚠️ <b>Не удалось начать клубную подписку</b>',
            '',
            'Пользователь: {}'.format(user_html(user)),
            'Провайдер: {}'.format(_escape(_provider_label(provider))),
            'Причина: <code>{}</code>'.format(_escape(reason)),
        ]
        if currency:
            lines.append('Валюта: {}'.format(_escape(currency.upper())))
        if message:
            lines.append('Сообщение: {}'.format(_escape(message)))
        return publish_admin_alert(
            alert='club_subscription_attempt_failed',
            dedupe_key='telegram.admin_alert:club_subscription_attempt_failed:{}:{}'.format(
                getattr(user, 'pk', 'unknown'), provider,
            ),
            payload={'text': _join_lines(lines)},
        )
    except Exception:
        logger.exception('Failed to notify admin about failed club subscription attempt user=%s', user.pk)
        return False


MERGE_KIND_LABELS = {
    'anonymous': 'перенос игры без входа в профиль',
    'account': 'объединение аккаунтов',
}


def notify_admin_merge_job_failed(*, kind: str, job_id, user_id, error: str = '', step: str = '') -> bool:
    """Queue an admin alert once a merge job has exhausted its retries."""
    try:
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.select_related('profile').filter(pk=user_id).first()
        lines = [
            '⚠️ <b>Не удалась задача: {}</b>'.format(_escape(MERGE_KIND_LABELS.get(kind, kind))),
            '',
            'Пользователь: {}'.format(
                user_html(user) if user is not None else 'id {} (не найден)'.format(_escape(user_id)),
            ),
            'Задача: <code>{}</code>'.format(_escape(job_id)),
        ]
        if step:
            lines.append('Шаг: <code>{}</code>'.format(_escape(step)))
        if error:
            lines.append('Ошибка: <code>{}</code>'.format(_escape(error[:1000])))
        return publish_admin_alert(
            alert='merge_job_failed',
            dedupe_key='telegram.admin_alert:merge_job_failed:{}:{}'.format(kind, job_id),
            payload={'text': _join_lines(lines)},
        )
    except Exception:
        logger.exception('Failed to notify admin about failed %s merge job=%s', kind, job_id)
        return False


OFFLINE_PLAY_ALERT_TITLES = {
    'verdict_divergence': 'Клиент и сервер разошлись в проверке ответа',
    'pack_build_failed': 'Не собралась клиентская проверка задания',
}


INVALID_TASK_DATA_ALERT_THROTTLE_SECONDS = 3600


def notify_admin_invalid_task_data(
    *, source: str, game_id, task_group_id, error, task_id=None,
) -> bool:
    """Queue one alert per task group and hour: stored task data no longer parses.

    Workers ack such messages instead of retrying them, so this alert is the
    only signal that a published task needs fixing in the console.
    """
    try:
        if not cache.add(
            'telegram:admin:task_data_invalid:{}:{}'.format(game_id, task_group_id),
            1, INVALID_TASK_DATA_ALERT_THROTTLE_SECONDS,
        ):
            return False
        text = _join_lines([
            '⚠️ <b>Битые данные задания</b>',
            '',
            'Где: <code>{}</code>'.format(_escape(source)),
            'Игра: {}, группа <code>{}</code>{}'.format(
                _link(game_support_url(game_id), game_id), _escape(task_group_id),
                ', задание <code>{}</code>'.format(_escape(task_id)) if task_id else '',
            ),
            'Ошибка: <code>{}</code>'.format(_escape(str(error)[:500])),
            'Повторы по этой группе подавлены на час.',
        ])
        return publish_admin_alert(
            alert='task_data_invalid',
            dedupe_key='telegram.admin_alert:task_data_invalid:{}:{}'.format(game_id, task_group_id),
            payload={'text': text},
        )
    except Exception:
        logger.exception(
            'Failed to notify admin about invalid task data game=%s task_group=%s',
            game_id, task_group_id,
        )
        return False


def notify_admin_offline_play(*, kind: str, task, game, lines) -> bool:
    """Queue an offline-play alert (docs/offline-play-plan.md); no answers in the text."""
    try:
        game_id = getattr(game, 'id', None)
        text = _join_lines([
            '📴 <b>{}</b>'.format(_escape(OFFLINE_PLAY_ALERT_TITLES.get(kind, kind))),
            '',
            'Игра: {}, задание <code>{}</code> (<code>{}</code>)'.format(
                _link(game_support_url(game_id), game_id) if game_id else '—',
                _escape(task.id), _escape(task.task_type),
            ),
            *lines,
            _support_link(task_support_url(task, game)),
            'Повторы по этому заданию подавлены на час.',
        ])
        return publish_admin_alert(
            alert='offline_play',
            dedupe_key='telegram.admin_alert:offline_play:{}:{}'.format(kind, task.id),
            payload={'text': text},
        )
    except Exception:
        logger.exception('Failed to notify admin about offline play %s task=%s', kind, task.id)
        return False


def _club_amount(amount, currency) -> str:
    from games.club.billing.tribute.config import ClubTributeProduct

    value = ClubTributeProduct.format_amount(amount) if amount is not None else '—'
    return '{} {}'.format(_escape(value), _escape(currency or '')).strip()


def notify_admin_club_renewal_failed(
    subscription_id: int, *, payment_id: int | None = None, reason: str = '',
    delivery: str = 'direct',
) -> bool:
    """Notify the admin chat when a recurring Club charge needs attention."""
    try:
        from games.models import ClubSubscription, ClubYooKassaPayment

        subscription = ClubSubscription.objects.select_related('user', 'user__profile').get(
            pk=subscription_id,
        )
        payment = (
            ClubYooKassaPayment.objects.filter(pk=payment_id).first()
            if payment_id else None
        )
        lines = [
            '⚠️ <b>Не прошло автопродление клубной подписки</b>',
            '',
            'Пользователь: {}'.format(user_html(subscription.user)),
            'Провайдер: {}'.format(_escape(_provider_label(subscription.provider))),
            'Сумма: {}'.format(_club_amount(subscription.amount, subscription.currency)),
        ]
        if payment is not None:
            lines.append('Платёж: #{}'.format(payment.pk))
            if payment.failure_code:
                lines.append('Код ошибки: <code>{}</code>'.format(_escape(payment.failure_code)))
        if reason:
            lines.append('Причина: {}'.format(_escape(reason)))
        if subscription.paid_until:
            lines.append('Доступ до: {}'.format(moscow_time(subscription.paid_until)))
        return _deliver_admin_text(
            _join_lines(lines),
            alert='club_renewal_failed',
            delivery=delivery,
            dedupe_key='telegram.admin_alert:club_renewal_failed:{}:{}'.format(
                subscription_id, payment_id or reason,
            ),
        )
    except Exception:
        logger.exception('Failed to notify admin about renewal failure subscription=%s', subscription_id)
        return False


def notify_admin_site_error(request, *, status_code: int = 500, exception=None) -> bool:
    """Alert the admin chat about a rate-limited server-side error."""
    try:
        path = str(getattr(request, 'path', '') or '/')[:160]
        cache_key = 'telegram:admin:site-error:{}:{}'.format(status_code, path)
        if cache.get(cache_key):
            return False
        resolver_match = getattr(request, 'resolver_match', None)
        route_name = getattr(resolver_match, 'url_name', '') if resolver_match else ''
        method = str(getattr(request, 'method', '') or 'UNKNOWN')[:12]
        user_label = 'аноним'
        try:
            user = getattr(request, 'user', None)
            if getattr(user, 'is_authenticated', False):
                user_label = user_html(user)
        except Exception:
            user_label = 'не удалось определить'
        lines = [
            '🚨 <b>Ошибка сайта {}</b>'.format(status_code),
            '',
            'Время: {}'.format(moscow_time(timezone.now(), '%d.%m.%Y %H:%M:%S')),
            'Запрос: <code>{} {}</code>'.format(_escape(method), _escape(path)),
            'Маршрут: <code>{}</code>'.format(_escape(route_name or '—')),
            'Пользователь: {}'.format(user_label),
            'Тип: {}'.format(
                'необработанное исключение' if exception is not None else 'готовый HTTP-ответ',
            ),
        ]
        timing_game = getattr(request, 'interoves_timing_game_id', '')
        timing_task_group = getattr(request, 'interoves_timing_task_group_id', '')
        timing_action = getattr(request, 'interoves_timing_action', '')
        if timing_game or timing_task_group or timing_action:
            lines.append('Таймер: игра <code>{}</code> · группа <code>{}</code> · действие <code>{}</code>'.format(
                _escape(timing_game or '—'),
                _escape(timing_task_group or '—'),
                _escape(timing_action or '—'),
            ))
        request_id = getattr(request, 'interoves_request_id', '')
        if request_id:
            lines.append('Request ID: <code>{}</code>'.format(_escape(request_id)))
        instance = getattr(settings, 'INSTANCE_ID', '') or socket.gethostname() or 'unknown'
        deploy_version = getattr(settings, 'SITE_DEPLOY_VERSION', '') or 'unknown'
        lines.append('Инстанс: <code>{}</code> · среда <code>{}</code> · версия <code>{}</code>'.format(
            _escape(instance), _escape(deployment_environment()), _escape(deploy_version),
        ))
        duration_ms = getattr(request, '_interoves_elapsed_ms', None)
        if duration_ms is not None:
            lines.append('Время обработки: {} мс'.format(int(round(duration_ms))))
        if exception is not None:
            lines.append('Ошибка: <code>{}</code>'.format(_escape(
                '{}: {}'.format(exception.__class__.__name__, str(exception)[:400]),
            )))
        lines.append('Повторы по этому адресу подавлены на 5 минут.')
        published = publish_admin_alert(
            alert='site_error',
            dedupe_key='telegram.admin_alert:site_error:{}:{}'.format(status_code, path),
            payload={'text': _join_lines(lines)},
        )
        if published:
            cache.set(cache_key, 1, timeout=300)
        return published
    except Exception:
        logger.exception('Failed to notify admin about site error')
        return False


def notify_admin_word_salad_submission_error(
    *, game_id: str, incident_id: str = '', deduplicate: bool = True,
) -> bool:
    """Alert on a custom Salad submission contract failure, without details."""
    try:
        deploy_version = getattr(settings, 'SITE_DEPLOY_VERSION', '') or 'unknown'
        cache_key = 'telegram:admin:word-salad-submit:{}:{}'.format(
            game_id, deploy_version,
        )
        if deduplicate and cache.get(cache_key):
            return True
        text = _join_lines([
            '🚨 <b>Ошибка отправки ответа в авторском салатике</b>',
            '',
            'Причина: <code>missing_or_invalid_offer_share</code>',
            'Игра: <code>{}</code>'.format(_escape(game_id)),
            'Инцидент: <code>{}</code>'.format(_escape(incident_id or '—')),
            'Подробности в логе приложения: '
            '<code>custom scheduled task submission missing valid offer_share</code>.',
            'Повторы подавлены на 5 минут.',
        ])
        delivered = send_admin_message(text, force=True)
        if deduplicate:
            if delivered:
                cache.set(cache_key, 1, timeout=300)
            else:
                cache.delete(cache_key)
        return delivered
    except Exception:
        logger.exception('Failed to notify admin about custom word salad submission')
        return False


def notify_admin_club_subscription(
    subscription_id: int, event_name: str, *, payment_kind: str = '',
    payment_id: int | None = None, delivery: str = 'direct',
) -> bool:
    """Notify the admin chat about a durable Club subscription state change."""
    try:
        from games.models import ClubSubscription

        subscription = ClubSubscription.objects.select_related('user', 'user__profile').get(
            pk=subscription_id,
        )
        if event_name == 'payment.succeeded' and payment_kind == 'recurring_monthly':
            title = '🔄 <b>Продление клубной подписки</b>'
        elif event_name in ('new_subscription', 'payment.succeeded'):
            title = '🟢 <b>Новая клубная подписка</b>'
        elif event_name == 'subscription_cancelled':
            title = '🟡 <b>Автопродление отключено</b>'
        elif event_name == 'cancelled_subscription':
            title = '🔴 <b>Клубная подписка отменена</b>'
        else:
            title = '🔄 <b>Продление клубной подписки</b>'

        lines = [
            title,
            '',
            'Пользователь: {}'.format(user_html(subscription.user)),
            'Провайдер: {}'.format(_escape(_provider_label(subscription.provider))),
            'Сумма: {}'.format(_club_amount(subscription.amount, subscription.currency)),
        ]
        if payment_kind and event_name != 'subscription_cancelled':
            lines.append('Платёж: {}'.format(_escape(CLUB_PAYMENT_KIND_LABELS.get(payment_kind, payment_kind))))
        if subscription.paid_until:
            lines.append('Доступ до: {}'.format(moscow_time(subscription.paid_until)))
        if event_name == 'subscription_cancelled':
            lines.append('Новых списаний не будет.')
        invite_url = club_invite_url()
        if invite_url and event_name in ('new_subscription', 'payment.succeeded'):
            lines.extend([
                '',
                _link(invite_url, 'Ссылка на вступление в Inter Oves Elite'),
            ])
        return _deliver_admin_text(
            _join_lines(lines),
            alert='club_subscription_state',
            delivery=delivery,
            dedupe_key='telegram.admin_alert:club_subscription_state:{}:{}:{}'.format(
                subscription_id, event_name, payment_id or 'state',
            ),
        )
    except Exception:
        logger.exception('Failed to notify admin about club subscription %s', subscription_id)
        return False


def notify_admin_subscription_gift(gift_id: int, alert_type: str, *, delivery: str = 'direct') -> bool:
    """Notify the admin chat that a Club gift was paid for or claimed."""
    try:
        from games.club.billing.gifts.offers import gift_duration_label
        from games.models import ClubEntitlement, SubscriptionGift, SubscriptionGiftPayment

        gift = SubscriptionGift.objects.select_related(
            'purchaser__profile', 'created_by__profile', 'claimed_by__profile',
        ).get(pk=gift_id)
        payment = SubscriptionGiftPayment.objects.filter(gift=gift).first()
        from_label = user_html(gift.purchaser) if gift.purchaser_id else (
            '{} (выдан вручную)'.format(user_html(gift.created_by))
        )
        lines = []
        if alert_type == 'paid':
            lines.extend([
                '🎁 <b>Куплен подарок клубной подписки #{}</b>'.format(gift.pk),
                '',
                'Покупатель: {}'.format(from_label),
                'Срок: {}'.format(_escape(gift_duration_label(gift))),
            ])
            if payment is not None:
                lines.append('Сумма: {} · {}'.format(
                    _club_amount(payment.expected_amount, payment.currency),
                    _escape(_provider_label(payment.provider)),
                ))
            if gift.recipient_telegram_username:
                lines.append('Получатель: @{}'.format(_escape(gift.recipient_telegram_username)))
        elif alert_type == 'claimed':
            entitlement = ClubEntitlement.objects.filter(
                gift=gift, kind=ClubEntitlement.KIND_GIFT,
            ).order_by('-pk').first()
            if entitlement is None or entitlement.ends_at is None:
                access = 'навсегда'
            else:
                access = 'до {}'.format(moscow_time(entitlement.ends_at))
            lines.extend([
                '🎉 <b>Подарок клубной подписки #{} активирован</b>'.format(gift.pk),
                '',
                'Активировал: {}'.format(user_html(gift.claimed_by)),
                'От: {}'.format(from_label),
                'Срок: {}'.format(_escape(gift_duration_label(gift))),
                'Доступ: {}'.format(_escape(access)),
            ])
        else:
            logger.warning('Unknown subscription gift admin alert %s', alert_type)
            return False
        return _deliver_admin_text(
            _join_lines(lines),
            alert='subscription_gift',
            delivery=delivery,
            dedupe_key='telegram.admin_alert:subscription_gift:{}:{}'.format(gift.pk, alert_type),
        )
    except Exception:
        logger.exception('Failed to notify admin about subscription gift %s', gift_id)
        return False


def notify_club_subscription_user(subscription_id: int) -> bool:
    """Tell the subscriber, with the club invite in Telegram, after their first payment."""
    try:
        from games.models import ClubSubscription
        from games.notifications.service import notify

        subscription = ClubSubscription.objects.select_related('user').get(pk=subscription_id)
        return notify(subscription.user, 'club.active', subscription_id=subscription.pk) is not None
    except Exception:
        logger.exception('Failed to send club invite to subscription %s', subscription_id)
        return False


def send_announce_message(
    text: str,
    *,
    reply_markup: dict | None = None,
    chat_ids: Iterable[str] | None = None,
) -> bool:
    if not telegram_bot_configured():
        return False
    target_chat_ids = list(chat_ids) if chat_ids is not None else announce_chat_ids()
    if not target_chat_ids:
        logger.debug('Telegram announce skipped: TELEGRAM_ANNOUNCE_CHAT_IDS is empty')
        return False
    results = []
    for chat_id in target_chat_ids:
        results.append(bool(send_message(chat_id, text, reply_markup=reply_markup)))
    return bool(results) and all(results)


def send_announce_photo(
    photo_bytes: bytes,
    *,
    caption: str = '',
    filename: str = 'photo.png',
    reply_markup: dict | None = None,
    chat_ids: Iterable[str] | None = None,
) -> bool:
    if not telegram_bot_configured():
        return False
    target_chat_ids = list(chat_ids) if chat_ids is not None else announce_chat_ids()
    if not target_chat_ids:
        logger.debug('Telegram announce photo skipped: TELEGRAM_ANNOUNCE_CHAT_IDS is empty')
        return False
    results = []
    for chat_id in target_chat_ids:
        results.append(bool(send_photo(
            chat_id,
            photo_bytes,
            caption=caption,
            filename=filename,
            reply_markup=reply_markup,
        )))
    return bool(results) and all(results)


def send_telegram_message(text: str) -> bool:
    """Backward-compatible alias for admin notifications."""
    return send_admin_message(text)


def _admin_link(path: str) -> str:
    return admin_url(path)


def _contact_method_label(order: CorporateGameOrder) -> str:
    if order.contact_method == CorporateGameOrder.ContactMethod.OTHER and order.contact_other_label:
        return 'Другое ({})'.format(order.contact_other_label)
    return CONTACT_METHOD_LABELS.get(order.contact_method, order.contact_method)


def _bug_report_reporter(report: BugReport) -> str:
    """HTML label of whoever filed the report."""
    return actor_html(
        user=report.user if report.user_id else None,
        team=report.team if report.team_id else None,
        anon_key=report.anon_key,
    )


# --- Inline keyboards -----------------------------------------------------

BUG_STATUS_BUTTONS = (
    ('reviewed', '👀 Просмотрено'),
    ('fixed', '✅ Исправлено'),
    ('dismiss', '✖️ Отклонить'),
)


def bug_report_keyboard(report_id: int) -> dict:
    return {
        'inline_keyboard': [[
            {'text': label, 'callback_data': 'bug:{}:{}'.format(action, report_id)}
            for action, label in BUG_STATUS_BUTTONS
        ]],
    }


def censorly_word_report_keyboard(report_id: int) -> dict:
    return {
        'inline_keyboard': [[
            {'text': '✅ Подтвердить', 'callback_data': 'censorly:confirm:{}'.format(report_id)},
            {'text': '✖️ Отклонить', 'callback_data': 'censorly:reject:{}'.format(report_id)},
            {'text': '🤔 Нужна проверка', 'callback_data': 'censorly:review:{}'.format(report_id)},
        ]],
    }


def ticket_request_keyboard(ticket_id: int) -> dict:
    return {
        'inline_keyboard': [[
            {'text': '✅ Принять', 'callback_data': 'ticket:accept:{}'.format(ticket_id)},
            {'text': '✖️ Отклонить', 'callback_data': 'ticket:reject:{}'.format(ticket_id)},
        ]],
    }


def alphabetty_dict_suggestion_keyboard(suggestion_id: int) -> dict:
    return {
        'inline_keyboard': [
            [
                {'text': '✅ В словарь', 'callback_data': 'abdict:approve:{}'.format(suggestion_id)},
                {
                    'text': '🎯 Для загадывания',
                    'callback_data': 'abdict:answer:{}'.format(suggestion_id),
                },
            ],
            [
                {'text': '✖️ Отклонить', 'callback_data': 'abdict:reject:{}'.format(suggestion_id)},
            ],
        ],
    }


def resolved_keyboard(label: str, domain: str, obj_id: int) -> dict:
    """The keyboard after a decision: one inert button that shows it."""
    return {'inline_keyboard': [[
        {'text': label, 'callback_data': '{}:done:{}'.format(domain, obj_id)},
    ]]}


# --- Alphabetty dictionary suggestions ------------------------------------

def _alphabetty_dict_suggestion_payload(suggestion: AlphabettyDictSuggestion) -> tuple[str, dict]:
    reporter = (
        user_html(suggestion.user) if suggestion.user_id
        else actor_html(anon_key=suggestion.anon_key)
    )
    message = _join_lines([
        '🔤 <b>Слово для словаря Алфавитки</b>',
        '',
        'Слово: <code>{}</code>'.format(_escape(suggestion.word)),
        'Голосов: {}'.format(suggestion.suggest_count),
        'Предложил: {}'.format(reporter),
    ])
    return message, alphabetty_dict_suggestion_keyboard(suggestion.pk)


def format_alphabetty_dict_suggestion_message(suggestion: AlphabettyDictSuggestion) -> str:
    message, _keyboard = _alphabetty_dict_suggestion_payload(suggestion)
    return message


def notify_new_alphabetty_dict_suggestion(suggestion: AlphabettyDictSuggestion) -> bool:
    suggestion = (
        AlphabettyDictSuggestion.objects
        .select_related('user', 'user__profile')
        .get(pk=suggestion.pk)
    )
    message, keyboard = _alphabetty_dict_suggestion_payload(suggestion)
    return send_admin_message(message, reply_markup=keyboard)


# --- Puzzles offered by players --------------------------------------------

def _offer_author_line(offer) -> str:
    label = user_html(offer.user)
    signature = str(getattr(offer, 'author', '') or '').strip()
    if signature and signature != person_name(offer.user):
        label += ' · подпись «{}»'.format(_escape(signature))
    return 'Автор: {}'.format(label)


def _offer_message(*, title: str, offer, body: list[str], comment: str, support_path: str, play_path: str) -> str:
    from games.ops.telegram.channels.puzzle_text import escape

    lines = [title, '', _offer_author_line(offer), *body]
    if comment:
        lines.extend(['', 'Комментарий автора:', escape(comment[:1500])])
    links = []
    if play_path:
        links.append(_link('{}{}'.format(site_base_url(), play_path), 'Превью'))
    links.append(_support_link(support_url(support_path)))
    lines.extend(['', ' · '.join(links)])
    return _join_lines(lines)


def _offer_keyboard(kind: str, offer) -> dict:
    from games.ops.telegram.bot.offers import offer_keyboard

    return offer_keyboard(kind, offer.pk)


def _offer_puzzle_lines(task_group_id, task_type: str) -> list[str]:
    from games.ops.telegram.channels.puzzle_text import PUZZLE_LINES, first_task

    task = first_task(task_group_id, task_type) if task_group_id else None
    if task is None:
        return ['⚠️ Задание не найдено.']
    lines, _ready = PUZZLE_LINES[task_type](task)
    return lines


def format_alphabetty_offer_message(offer: AlphabettyOffer) -> str:
    return _offer_message(
        title='🔤 <b>Новая алфавитка на проверку</b>',
        offer=offer,
        body=_offer_puzzle_lines(offer.task_group_id, 'alphabetty'),
        comment=offer.comment,
        support_path='/support/alphabetty/',
        play_path='/alphabetty/{}/'.format(offer.share_hash) if offer.share_hash else '',
    )


def notify_new_alphabetty_offer(offer_id: int) -> bool:
    offer = AlphabettyOffer.objects.select_related('user', 'user__profile').get(pk=offer_id)
    return send_admin_message(format_alphabetty_offer_message(offer), reply_markup=_offer_keyboard('alphabetty', offer))


def format_ladder_offer_message(offer: LadderOffer) -> str:
    return _offer_message(
        title='🪜 <b>Новая лесенка на проверку</b>',
        offer=offer,
        body=_offer_puzzle_lines(offer.task_group_id, 'raddle'),
        comment=offer.comment,
        support_path='/support/ladders/',
        play_path='/ladder/{}/'.format(offer.share_hash) if offer.share_hash else '',
    )


def notify_new_ladder_offer(offer_id: int) -> bool:
    offer = LadderOffer.objects.select_related('user', 'user__profile').get(pk=offer_id)
    return send_admin_message(format_ladder_offer_message(offer), reply_markup=_offer_keyboard('ladder', offer))


def format_word_salad_offer_message(offer: WordSaladOffer) -> str:
    from games.ops.telegram.channels.puzzle_text import escape, spoiler

    if offer.kind == WordSaladOffer.KIND_IDEA:
        body = []
        if offer.theme:
            body.append('Тема: {}'.format(escape(offer.theme[:200])))
        if offer.idea_text:
            body.extend(['Идея:', escape(offer.idea_text[:1500])])
        words = ' · '.join((offer.suggested_words or '').split())
        if words:
            body.append('Слова: {}'.format(spoiler(words[:1000])))
        return _offer_message(
            title='🥗 <b>Новая идея салатика</b>',
            offer=offer,
            body=body,
            comment=offer.comment,
            support_path='/support/salad/',
            play_path='',
        )
    return _offer_message(
        title='🥗 <b>Новый салатик на проверку</b>',
        offer=offer,
        body=_offer_puzzle_lines(offer.task_group_id, 'word_salad'),
        comment=offer.comment,
        support_path='/support/salad/',
        play_path=offer.play_url() or '',
    )


def notify_new_word_salad_offer(offer_id: int) -> bool:
    offer = WordSaladOffer.objects.select_related('user', 'user__profile').get(pk=offer_id)
    return send_admin_message(format_word_salad_offer_message(offer), reply_markup=_offer_keyboard('salad', offer))


def format_censorly_offer_message(offer: CensorlyOffer) -> str:
    party = offer.party
    return _offer_message(
        title='📰 <b>Новая цензурка на проверку</b>',
        offer=offer,
        body=_offer_puzzle_lines(party.task_group_id, 'censorly'),
        comment=offer.comment,
        support_path='/support/censorly/',
        play_path=party.play_url() or '',
    )


def notify_new_censorly_offer(offer_id: int) -> bool:
    offer = CensorlyOffer.objects.select_related('user', 'user__profile', 'party').get(pk=offer_id)
    return send_admin_message(format_censorly_offer_message(offer), reply_markup=_offer_keyboard('censorly', offer))


# --- Bug reports ----------------------------------------------------------

def bug_report_thread_url(report: BugReport) -> str:
    """The admin's page of the report: thread, reply form, status."""
    return bug_report_support_url(report)


def _reply_hint(report: BugReport) -> str:
    if report.user_id:
        return '↩️ Ответьте на это сообщение — ответ уйдёт автору на сайт и в Telegram.'
    return 'Автор не вошёл в аккаунт: ответить ему нельзя.'


def _report_page_line(report: BugReport):
    """Where the player pressed "report"; None (dropped by _join_lines) when unknown."""
    url = (report.page_url or '').strip()
    if url.startswith('/') and not url.startswith('//'):
        url = site_base_url() + url
    if not url.startswith(('https://', 'http://')):
        return None
    return 'Страница: {}'.format(_link(url, url.split('://', 1)[1][:80]))


def bug_report_id_from_message_text(text: str) -> int | None:
    """Report id from the first line of an admin-chat message this bot sent."""
    first_line = (text or '').strip().split('\n', 1)[0]
    match = BUG_REPORT_HEADER_RE.match(first_line)
    return int(match.group(1)) if match else None


def format_bug_report_message(report: BugReport) -> str:
    if report.report_type == BugReport.REPORT_TYPE_CENSORLY_WORD_MATCH:
        return format_censorly_word_report_message(report)
    task = report.task
    game = report.game
    task_label = task_display_name(game, task)
    if hasattr(game, 'get_no_html_name'):
        game_label = game.get_no_html_name()
    else:
        game_label = getattr(game, 'name', None) or game.pk

    return _join_lines([
        '🐞 <b>Баг #{}</b>'.format(report.pk),
        '',
        'Игра: {} · {}'.format(
            _link(game_site_url(game), game_label), _link(game_support_url(game), '/support'),
        ),
        'Задание: {} · {}'.format(
            _link(task_play_url(game, task), '№{}'.format(task_label)),
            _link(task_support_url(task, game), '/support'),
        ),
        'Автор: {}'.format(_bug_report_reporter(report)),
        _report_page_line(report),
        '',
        _escape(report.text[:3500]),
        '',
        _reply_hint(report),
        _support_link(bug_report_thread_url(report)),
    ])


CENSORLY_REASON_LABELS = {
    'FALSE_POSITIVE': 'лишнее открытие',
    'FALSE_NEGATIVE': 'слово не открылось',
    'OTHER': 'другое',
}
CENSORLY_DIAGNOSTIC_FIELDS = (
    ('runtime', 'Где сработало'),
    ('classification', 'Классификация'),
    ('guess_structures', 'Разбор догадки'),
    ('target_structures', 'Разбор слова в статье'),
    ('recommendation', 'Что поправить'),
)


def format_censorly_word_report_message(report: BugReport) -> str:
    metadata = report.metadata if isinstance(report.metadata, dict) else {}
    reason = metadata.get('reason') or 'OTHER'
    diagnostic = metadata.get('diagnostic') if isinstance(metadata.get('diagnostic'), dict) else {}
    lines = [
        '🧩 <b>Жалоба на Цензурку #{}</b>: {}'.format(
            report.pk, _escape(CENSORLY_REASON_LABELS.get(reason, reason)),
        ),
        '',
        'Автор: {}'.format(_bug_report_reporter(report)),
        'Статья: {}'.format(_escape(metadata.get('article') or '—')),
        'Открытое слово: <code>{}</code>'.format(_escape(metadata.get('target_surface') or '—')),
    ]
    if metadata.get('other_word'):
        lines.append('Слово игрока: <code>{}</code>'.format(_escape(metadata['other_word'])))
    if metadata.get('expected'):
        lines.append('Ожидание: <b>{}</b>'.format(_escape(metadata['expected'])))
    if report.censorly_review_case_id:
        lines.append('Жалоб на эту пару: <b>{}</b>'.format(report.censorly_review_case.report_count))
    if metadata.get('comment'):
        lines.extend(['', 'Комментарий:', _escape(metadata['comment'][:2000])])
    if diagnostic and diagnostic.get('status') != 'unavailable':
        lines.extend(['', '<b>Диагностика</b>'])
        for key, label in CENSORLY_DIAGNOSTIC_FIELDS:
            lines.append('{}: <code>{}</code>'.format(label, _escape(diagnostic.get(key) or '—')))
    else:
        lines.extend(['', 'Диагностика недоступна.'])
    lines.extend([
        '',
        'Версия сайта: <code>{}</code>'.format(_escape(getattr(settings, 'SITE_DEPLOY_VERSION', '') or 'unknown')),
        _reply_hint(report),
        _support_link(bug_report_thread_url(report)),
    ])
    return _join_lines(lines)


def notify_new_bug_report(report: BugReport) -> bool:
    report = (
        BugReport.objects
        .select_related(
            'task', 'task__task_group', 'game', 'game__project', 'team', 'user', 'user__profile',
            'censorly_review_case',
        )
        .get(pk=report.pk)
    )
    keyboard = (
        censorly_word_report_keyboard(report.pk)
        if report.report_type == BugReport.REPORT_TYPE_CENSORLY_WORD_MATCH
        else bug_report_keyboard(report.pk)
    )
    sent = send_admin_message_result(format_bug_report_message(report), reply_markup=keyboard)
    if sent is None:
        return False
    remember_bug_report_message(report.pk, sent.get('message_id'))
    return True


TELEGRAM_MESSAGE_KEY = 'telegram_message_id'


def remember_bug_report_message(report_id: int, message_id) -> None:
    """Keep the admin-chat message of a report, so later thread messages reply to it."""
    if not message_id:
        return
    report = BugReport.objects.filter(pk=report_id).only('metadata').first()
    if report is None:
        return
    metadata = dict(report.metadata) if isinstance(report.metadata, dict) else {}
    metadata[TELEGRAM_MESSAGE_KEY] = int(message_id)
    BugReport.objects.filter(pk=report_id).update(metadata=metadata)


def bug_report_message_id(report: BugReport) -> int | None:
    metadata = report.metadata if isinstance(report.metadata, dict) else {}
    value = metadata.get(TELEGRAM_MESSAGE_KEY)
    return int(value) if value else None


def send_user_telegram_message(user, text: str) -> bool:
    """DM a logged-in user via verified telegram_user_id. Never use telegram_handle."""
    profile = getattr(user, 'profile', None)
    chat_id = getattr(profile, 'telegram_user_id', None) if profile is not None else None
    if not chat_id:
        return False
    if not telegram_bot_configured():
        logger.debug('Telegram user DM skipped: bot token is empty')
        return False
    return send_message(chat_id, text)


def format_bug_report_user_reply_message(message: BugReportMessage) -> str:
    report = message.report
    return _join_lines([
        '💬 <b>Ответ по багу #{}</b> от автора'.format(report.pk),
        '',
        'Автор: {}'.format(_bug_report_reporter(report)),
        '',
        _escape(message.text[:3500]),
        '',
        _reply_hint(report),
        _support_link(bug_report_thread_url(report)),
    ])


def format_bug_report_admin_reply_message(message: BugReportMessage) -> str:
    """The user's copy of an admin reply; it links to the user's own thread page."""
    from games.site.pages.profile.feedback import profile_reports_path

    report = message.report
    task = report.task
    game = report.game
    task_label = task_display_name(game, task) if task is not None else '—'
    if game is not None and hasattr(game, 'get_no_html_name'):
        game_label = game.get_no_html_name()
    elif game is not None:
        game_label = getattr(game, 'name', None) or game.pk
    else:
        game_label = '—'
    return _join_lines([
        '✉️ <b>Ответ по вашему сообщению о проблеме</b>',
        '',
        'Игра: {}'.format(_escape(game_label)),
        'Задание: {}'.format(_escape(task_label)),
        '',
        _escape(message.text[:3500]),
        '',
        '<a href="{}">Открыть обращение</a>'.format(admin_url(profile_reports_path(report_id=report.pk))),
    ])


def notify_bug_report_thread_message(message: BugReportMessage) -> bool:
    """Ping admin-chat on user replies and DM the user on admin replies.

    Opening message is skipped: notify_new_bug_report already fired for the report.
    """
    message = (
        BugReportMessage.objects
        .select_related(
            'report', 'report__task', 'report__task__task_group',
            'report__game', 'report__game__project', 'report__team', 'report__user',
            'report__user__profile', 'author_user',
        )
        .get(pk=message.pk)
    )
    if message.is_opening():
        return False
    if message.author_role == BugReportMessage.ROLE_USER:
        # Threaded under the report's first message when it is known.
        return send_admin_message_result(
            format_bug_report_user_reply_message(message),
            reply_to_message_id=bug_report_message_id(message.report),
        ) is not None
    if message.author_role == BugReportMessage.ROLE_ADMIN:
        user = message.report.user
        if user is None:
            return False
        from games.notifications.service import notify

        return notify(user, 'support.reply', report_id=message.report_id, message_id=message.pk) is not None
    return False


# --- Tickets, orders, donations -------------------------------------------

def _ticket_lines(ticket_request: TicketRequest) -> list[str]:
    return [
        'Команда: {}'.format(team_html(ticket_request.team)),
        'Билетов: {} · {} {}'.format(ticket_request.tickets, ticket_request.money, _escape(ticket_request.currency)),
        'Оплата: {} · {}'.format(
            _escape(ticket_request.payment_provider), _escape(ticket_request.merchant),
        ),
        'Статус: {}'.format(_escape(TICKET_STATUS_LABELS.get(ticket_request.status, ticket_request.status))),
    ]


def format_ticket_request_message(ticket_request: TicketRequest) -> str:
    return _join_lines([
        '🎫 <b>Заявка на билеты #{}</b>'.format(ticket_request.pk),
        '',
        *_ticket_lines(ticket_request),
        '',
        _support_link(support_url('/support/pending/')),
    ])


def format_corporate_order_message(order: CorporateGameOrder) -> str:
    admin_link = _admin_link('/admin/games/corporategameorder/{}/change/'.format(order.pk))
    lines = [
        '🏢 <b>Заявка на корпоративную игру #{}</b>'.format(order.pk),
        '',
        'Компания: {}'.format(_escape(order.company_name)),
        'Контактное лицо: {}'.format(_escape(order.contact_name)),
        'Связаться: {} · {}'.format(_escape(_contact_method_label(order)), _escape(order.contact_value)),
    ]
    if order.team_size:
        lines.append('Размер команды: {}'.format(_escape(order.team_size)))
    if order.preferred_date:
        lines.append('Когда: {}'.format(_escape(order.preferred_date)))
    if order.message:
        lines.extend(['', _escape(order.message[:3500])])
    lines.extend(['', _link(admin_link, DJANGO_ADMIN_LINK_TEXT)])
    return _join_lines(lines)


def format_payment_message(ticket_request: TicketRequest, event: str) -> str:
    if event == 'payment.succeeded':
        title = '✅ <b>Билеты оплачены, заявка #{}</b>'.format(ticket_request.pk)
    else:
        title = '❌ <b>Оплата билетов отменена, заявка #{}</b>'.format(ticket_request.pk)
    return _join_lines([
        title,
        '',
        *_ticket_lines(ticket_request),
    ])


def format_admin_game_lifecycle_message(game, event: str) -> str:
    name = _escape(game.get_no_html_name())
    start = moscow_time(game.get_visible_start_time())
    end = moscow_time(game.get_visible_end_time())

    if event == 'start_soon':
        title = '⏰ <b>Через час начинается игра</b>'
    elif event == 'started':
        title = '🟢 <b>Игра началась</b>'
    elif event == 'ended':
        title = '🔴 <b>Игра завершилась</b>'
    else:
        title = 'ℹ️ <b>Игра: {}</b>'.format(_escape(event))

    return _join_lines([
        title,
        '',
        '«{}»'.format(name),
        'Время: {} — {}'.format(start, end),
        '',
        '{} · {}'.format(_link(game_site_url(game), 'Сайт'), _support_link(game_support_url(game))),
    ])


def format_admin_registration_message(registration, count: int) -> str:
    """One team registered for a game; ``count`` is the game's total after it."""
    game = registration.game
    lines = [
        '📝 <b>Новая регистрация на игру</b>',
        '',
        '«{}»'.format(_escape(game.get_no_html_name())),
        '{}{}'.format(
            team_html(registration.team),
            ' (по приглашению команды {})'.format(_escape(
                getattr(registration.with_referent, 'visible_name', None) or registration.with_referent.pk,
            )) if registration.with_referent_id else '',
        ),
        'Всего команд: <b>{}</b>'.format(count),
        '',
        _support_link(game_support_url(game)),
    ]
    return _join_lines(lines)


def notify_new_ticket_request(ticket_request: TicketRequest) -> bool:
    return send_admin_message(
        format_ticket_request_message(ticket_request),
        reply_markup=ticket_request_keyboard(ticket_request.pk),
    )


def notify_new_corporate_order(order: CorporateGameOrder) -> bool:
    return send_admin_message(format_corporate_order_message(order))


def notify_payment_event(ticket_request: TicketRequest, event: str) -> bool:
    return send_admin_message(format_payment_message(ticket_request, event))


def format_donation_message(donation: Donation, event: str = 'created') -> str:
    admin_link = _admin_link('/admin/games/donation/{}/change/'.format(donation.pk))
    if event == 'donation.confirmed':
        title = '✅ <b>Донат #{} подтверждён</b>'.format(donation.pk)
    elif event == 'donation.rejected':
        title = '❌ <b>Донат #{} отклонён</b>'.format(donation.pk)
    else:
        title = '💚 <b>Новый крипто-донат #{}</b>'.format(donation.pk)
    lines = [
        title,
        '',
        'Сумма: {} ₽'.format(donation.amount_rub),
        'Статус: {}'.format(_escape(DONATION_STATUS_LABELS.get(donation.status, donation.status))),
    ]
    if donation.pay_amount and donation.pay_currency:
        lines.append('Оплачено: {} {}'.format(_escape(donation.pay_amount), _escape(donation.pay_currency)))
    if donation.user_id:
        lines.append('Пользователь: {}'.format(user_html(donation.user)))
    lines.extend(['', _link(admin_link, DJANGO_ADMIN_LINK_TEXT)])
    return _join_lines(lines)


def notify_new_donation(donation: Donation) -> bool:
    return send_admin_message(format_donation_message(donation, 'created'))


def notify_donation_event(donation: Donation, event: str) -> bool:
    return send_admin_message(format_donation_message(donation, event))


def fetch_recent_telegram_chat_ids() -> list[dict]:
    import requests

    from games.ops.telegram.transport.api import _safe_error_text

    if not settings.TELEGRAM_BOT_TOKEN:
        raise ValueError('TELEGRAM_BOT_TOKEN is not configured')

    url = 'https://api.telegram.org/bot{}/getUpdates'.format(settings.TELEGRAM_BOT_TOKEN)
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except Exception as error:
        raise RuntimeError(
            'Telegram getUpdates failed: {}'.format(_safe_error_text(error))
        ) from None
    body = response.json()
    if not body.get('ok'):
        raise RuntimeError('Telegram API error: {!r}'.format(body))

    seen = {}
    for update in body.get('result', []):
        message = update.get('message') or update.get('edited_message')
        if not message:
            continue
        chat = message.get('chat') or {}
        chat_id = chat.get('id')
        if chat_id is None:
            continue
        seen[str(chat_id)] = {
            'chat_id': chat_id,
            'type': chat.get('type'),
            'title': chat.get('title'),
            'username': chat.get('username'),
            'first_name': chat.get('first_name'),
            'last_name': chat.get('last_name'),
        }
    return list(seen.values())

