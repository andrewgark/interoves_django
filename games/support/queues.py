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
    QueueWorkerHeartbeat,
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
    heartbeat_names = (
        ('daily_difficulty_refresh', 'Сложность: refresh'),
        ('daily_difficulty_health_check', 'Сложность: health-check'),
        ('daily_result_projection_reconcile', 'Проекции результатов'),
        ('telegram_game_announcements', 'Telegram: анонсы'),
        ('telegram_admin_report', 'Telegram: admin report'),
        ('instagram_refresh_token', 'Instagram: refresh token'),
        ('anonymous_merge', 'Мердж гостевых профилей'),
        ('word_salad_recheck', 'Салатики: recheck'),
    )
    heartbeats = {
        row.queue_name: row
        for row in QueueWorkerHeartbeat.objects.filter(
            queue_name__in=[name for name, _ in heartbeat_names],
        )
    }
    heartbeat_rows = []
    for name, label in heartbeat_names:
        row = heartbeats.get(name)
        age = (now - row.updated_at).total_seconds() if row else None
        stale_after = {
            'daily_difficulty_health_check': 2 * 60 * 60,
            'instagram_refresh_token': 2 * 24 * 60 * 60,
        }.get(name, 10 * 60)
        health = 'unknown' if row is None else (
            'error' if row.status == QueueWorkerHeartbeat.STATUS_FAILED else
            'stale' if age > stale_after else
            'ok'
        )
        heartbeat_rows.append({
            'name': label,
            'queue_name': name,
            'health': health,
            'status': row.status if row else 'не запускалась',
            'started_at': _iso(row.started_at) if row else None,
            'finished_at': _iso(row.finished_at) if row else None,
            'last_success_at': _iso(row.last_success_at) if row else None,
            'updated_at': _iso(row.updated_at) if row else None,
            'duration_ms': row.duration_ms if row else None,
            'processed_count': row.processed_count if row else None,
            'worker': row.worker if row else '',
            'error': row.last_error if row else '',
        })
    return {
        'queue_cards': cards,
        'queue_details': details[:40],
        'heartbeat_rows': heartbeat_rows,
        'queue_checked_at': now,
    }
