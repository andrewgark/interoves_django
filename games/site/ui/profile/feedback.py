"""Кабинет обращений: список багрепортов пользователя и тред сообщений.

Новый тип пользовательских заданий (offer-flow) добавляет пункт
«Мои …» → `/create_<kind>/` на странице Профиля. Унифицировать
LadderOffer/AlphabettyOffer не нужно; переписка в v1 только у багов.
"""
from django.db import transaction
from django.db.models import F, Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from games.models import AlphabettyOffer, BugReport, BugReportMessage, LadderOffer, WordSaladOffer

MAX_MESSAGE_LEN = 5000


def profile_cabinet_flags(user):
    """Which «Мои …» links to show on the profile page."""
    return {
        'has_bug_reports': BugReport.objects.filter(user=user).exists(),
        'has_ladder_offers': LadderOffer.objects.filter(user=user).exists(),
        'has_alphabetty_offers': AlphabettyOffer.objects.filter(user=user).exists(),
        'has_word_salad_offers': WordSaladOffer.objects.filter(user=user).exists(),
    }


def profile_reports_path(project_id=None, report_id=None) -> str:
    pid = (project_id or '').strip()
    if pid and pid not in ('main', 'sections'):
        prefix = '/{}/profile/reports'.format(pid)
    else:
        prefix = '/profile/reports'
    if report_id:
        return '{}/{}/'.format(prefix, report_id)
    return prefix + '/'


def reports_for_user(user):
    return (
        BugReport.objects
        .filter(user=user)
        .select_related('task', 'task__task_group', 'game', 'game__project')
        .order_by('-time')
    )


def unread_admin_reply_q():
    return Q(author_role=BugReportMessage.ROLE_ADMIN) & (
        Q(report__user_last_read_at__isnull=True)
        | Q(created_at__gt=F('report__user_last_read_at'))
    )


def unread_report_ids_for_user(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return set()
    return set(
        BugReportMessage.objects
        .filter(report__user=user)
        .filter(unread_admin_reply_q())
        .values_list('report_id', flat=True)
        .distinct()
    )


def user_has_unread_feedback(user) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return (
        BugReportMessage.objects
        .filter(report__user=user)
        .filter(unread_admin_reply_q())
        .exists()
    )


def mark_report_read(report):
    now = timezone.now()
    BugReport.objects.filter(pk=report.pk).update(user_last_read_at=now)
    report.user_last_read_at = now


def preview_text(text, max_len=160) -> str:
    compact = ' '.join((text or '').split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1].rstrip() + '…'


def add_thread_message(report, *, author_user, author_role, text):
    text = (text or '').strip()
    if not text:
        raise ValueError('Пустое сообщение.')
    if len(text) > MAX_MESSAGE_LEN:
        raise ValueError('Слишком длинное сообщение.')
    return BugReportMessage.objects.create(
        report=report,
        author_user=author_user,
        author_role=author_role,
        text=text,
    )


def add_user_reply(report, user, text):
    if not report.user_can_reply():
        raise ValueError('Это обращение закрыто.')
    return add_thread_message(
        report,
        author_user=user,
        author_role=BugReportMessage.ROLE_USER,
        text=text,
    )


def add_admin_reply(report, admin_user, text):
    return add_thread_message(
        report,
        author_user=admin_user,
        author_role=BugReportMessage.ROLE_ADMIN,
        text=text,
    )


def ensure_opening_message(report):
    if not report or not report.pk or not (report.text or '').strip():
        return None
    if BugReportMessage.objects.filter(report_id=report.pk).exists():
        return None
    return BugReportMessage.objects.create(
        report=report,
        author_user=report.user,
        author_role=BugReportMessage.ROLE_USER,
        text=report.text,
    )


@receiver(post_save, sender=BugReport, dispatch_uid='feedback-bugreport-opening-message')
def _ensure_opening_message_on_create(sender, instance, created, **kwargs):
    if not created:
        return
    ensure_opening_message(instance)


@receiver(post_save, sender=BugReportMessage, dispatch_uid='feedback-bugreportmessage-notify')
def _notify_thread_message(sender, instance, created, **kwargs):
    if not created:
        return

    def after_commit():
        from games.telegram.notify import notify_bug_report_thread_message

        notify_bug_report_thread_message(instance)

    transaction.on_commit(after_commit)
