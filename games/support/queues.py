from django.db.models import Q
from django.utils import timezone

from games.models import (
    AnonymousMergeJob,
    DailyDifficultyQueueStatus,
    DailyGameDifficulty,
    DailyResultProjectionState,
    TicketRequest,
    ClubSubscription,
    NextGameVoteEvent,
    WordSaladRecheckJob,
    WordSaladRecheckItem,
)
from games.social.models import SocialQueuePost


def _iso(value):
    return value.isoformat() if value else None


def _card(name, kind, *, pending=0, running=0, failed=0, updated=None, detail=''):
    age = (timezone.now() - updated).total_seconds() if updated else None
    health = 'unknown' if updated is None else ('error' if failed else ('stale' if age > 600 else 'ok'))
    return {
        'name': name, 'kind': kind, 'pending': pending, 'running': running,
        'failed': failed, 'updated_at': _iso(updated), 'detail': detail, 'health': health,
    }


def dashboard_context():
    now = timezone.now()
    cards = []
    details = []
    salad = WordSaladRecheckJob.objects.all()
    latest = salad.order_by('-updated_at').first()
    cards.append(_card(
        'Перепроверка салатиков', 'durable DB queue',
        pending=salad.filter(status=WordSaladRecheckJob.STATUS_PENDING).count(),
        running=salad.filter(status=WordSaladRecheckJob.STATUS_RUNNING).count(),
        failed=salad.filter(status=WordSaladRecheckJob.STATUS_FAILED).count(),
        updated=latest.updated_at if latest else None,
        detail='акторы: {}, ошибки элементов: {}; последний job #{}'.format(
            WordSaladRecheckItem.objects.filter(
                job__status__in=(WordSaladRecheckJob.STATUS_PENDING, WordSaladRecheckJob.STATUS_RUNNING),
            ).count(),
            WordSaladRecheckItem.objects.filter(status=WordSaladRecheckItem.STATUS_FAILED).count(),
            latest.pk if latest else '—',
        ),
    ))
    for job in salad.exclude(status=WordSaladRecheckJob.STATUS_SUPERSEDED).order_by('-updated_at')[:20]:
        details.append({
            'queue': 'Салатики', 'id': job.pk, 'status': job.status,
            'progress': '{} / {}'.format(job.completed_actors, job.total_actors),
            'updated_at': _iso(job.updated_at), 'error': job.last_error,
        })
    merges = AnonymousMergeJob.objects.all()
    latest = merges.order_by('-updated_at').first()
    cards.append(_card(
        'Мердж гостевого профиля', 'durable DB queue',
        pending=merges.filter(status=AnonymousMergeJob.STATUS_PENDING).count(),
        running=merges.filter(status=AnonymousMergeJob.STATUS_RUNNING).count(),
        failed=merges.filter(status=AnonymousMergeJob.STATUS_FAILED).count(),
        updated=latest.updated_at if latest else None,
        detail='активных/ошибочных jobs: {}'.format(merges.exclude(status=AnonymousMergeJob.STATUS_COMPLETED).count()),
    ))
    for job in merges.filter(status__in=(AnonymousMergeJob.STATUS_PENDING, AnonymousMergeJob.STATUS_RUNNING, AnonymousMergeJob.STATUS_FAILED)).order_by('-updated_at')[:20]:
        details.append({
            'queue': 'Мердж', 'id': job.pk, 'status': job.status,
            'progress': '{} / {}'.format(job.completed_reconciliation_units, job.total_reconciliation_units),
            'updated_at': _iso(job.updated_at), 'error': job.last_error,
        })
    difficulty = DailyDifficultyQueueStatus.objects.first()
    due = DailyGameDifficulty.objects.filter(dirty=True).filter(
        Q(refresh_not_before__isnull=True) | Q(refresh_not_before__lte=now)
    ).count()
    cards.append(_card(
        'Сложность ежедневных игр', 'minute cron', pending=due,
        running=1 if difficulty and difficulty.last_started_at and (
            not difficulty.last_finished_at or difficulty.last_started_at > difficulty.last_finished_at
        ) else 0,
        failed=1 if difficulty and difficulty.last_error else 0,
        updated=(difficulty.last_finished_at if difficulty else None),
        detail='due snapshots; worker {}'.format(difficulty.last_worker if difficulty else 'не запускался'),
    ))
    projections = DailyResultProjectionState.objects.all()
    cards.append(_card(
        'Daily result projection', 'DB-backed refresh',
        pending=projections.filter(full_refresh_required=True).count(),
        running=projections.filter(pending_actor_refreshes__gt=0).count(),
        failed=0,
        updated=projections.order_by('-completed_at').values_list('completed_at', flat=True).first(),
        detail='invalid: {}'.format(projections.filter(is_valid=False).count()),
    ))
    social = SocialQueuePost.objects.all()
    pending_count = running_count = failed_count = 0
    for field in ('telegram_status', 'twitter_status', 'instagram_status', 'threads_status'):
        pending_count += social.filter(**{field + '__in': [SocialQueuePost.STATUS_QUEUED, SocialQueuePost.STATUS_PENDING]}).count()
        running_count += social.filter(**{field: SocialQueuePost.STATUS_PUBLISHING}).count()
        failed_count += social.filter(**{field: SocialQueuePost.STATUS_FAILED}).count()
    latest = social.order_by('-updated_at').first()
    cards.append(_card(
        'Социальные публикации', 'multi-network DB queue',
        pending=pending_count, running=running_count, failed=failed_count,
        updated=latest.updated_at if latest else None,
        detail='статусы Telegram / X / Instagram / Threads',
    ))
    purchase = TicketRequest.objects.filter(
        purchase_goal_queued_at__isnull=False, purchase_goal_sent_at__isnull=True,
    )
    cards.append(_card(
        'Цели покупок', 'analytics DB queue', pending=purchase.count(),
        updated=purchase.order_by('-purchase_goal_queued_at').values_list('purchase_goal_queued_at', flat=True).first(),
        detail='покупки, ожидающие отправки в аналитику',
    ))
    subscription_goals = ClubSubscription.objects.filter(
        Q(payment_success_goal_queued_at__isnull=False, payment_success_goal_sent_at__isnull=True)
        | Q(renewal_goal_queued_at__isnull=False, renewal_goal_sent_at__isnull=True)
        | Q(cancelled_goal_queued_at__isnull=False, cancelled_goal_sent_at__isnull=True)
    )
    cards.append(_card(
        'Цели подписок', 'analytics DB queue', pending=subscription_goals.count(),
        updated=subscription_goals.order_by('-updated_at').values_list('updated_at', flat=True).first(),
        detail='оплата / продление / отмена',
    ))
    vote_goals = NextGameVoteEvent.objects.filter(
        analytics_goal_queued_at__isnull=False, analytics_goal_sent_at__isnull=True,
    )
    cards.append(_card(
        'Цели голосований', 'analytics DB queue', pending=vote_goals.count(),
        updated=vote_goals.order_by('-analytics_goal_queued_at').values_list('analytics_goal_queued_at', flat=True).first(),
        detail='Tribute-голоса, ожидающие отправки',
    ))
    return {'queue_cards': cards, 'queue_details': details[:40], 'queue_checked_at': now}
