from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from games.models import BugReport, CorporateGameOrder, TicketRequest
from games.telegram.notify import (
    notify_new_bug_report,
    notify_new_corporate_order,
    notify_new_ticket_request,
)


@receiver(post_save, sender=BugReport)
def telegram_notify_bug_report(sender, instance, created, **kwargs):
    if not created or instance.status != 'Pending':
        return
    transaction.on_commit(lambda: notify_new_bug_report(instance))


@receiver(post_save, sender=TicketRequest)
def telegram_notify_ticket_request(sender, instance, created, **kwargs):
    if not created or instance.status != 'Pending':
        return
    transaction.on_commit(lambda: notify_new_ticket_request(instance))


@receiver(post_save, sender=CorporateGameOrder)
def telegram_notify_corporate_order(sender, instance, created, **kwargs):
    if not created:
        return
    transaction.on_commit(lambda: notify_new_corporate_order(instance))
