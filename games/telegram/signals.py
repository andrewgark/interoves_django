from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from games.models import BugReport, CorporateGameOrder, Game, Registration, TicketRequest
from games.telegram.admin_commands import registration_milestone_reached
from games.telegram.notify import (
    notify_new_bug_report,
    notify_new_corporate_order,
    notify_new_ticket_request,
    notify_payment_event,
)
from games.telegram.scheduling import notify_admin_game_lifecycle, notify_admin_registration_milestone


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


@receiver(pre_save, sender=Game)
def telegram_cache_old_game_for_lifecycle(sender, instance, **kwargs):
    if not instance.pk:
        instance._telegram_old_game = None
        return
    try:
        instance._telegram_old_game = Game.objects.get(pk=instance.pk)
    except Game.DoesNotExist:
        instance._telegram_old_game = None


@receiver(post_save, sender=Game)
def telegram_notify_game_lifecycle(sender, instance, created, **kwargs):
    if created:
        return
    old = getattr(instance, '_telegram_old_game', None)
    if old is None:
        return

    from games.access import game_has_ended, game_has_started

    def after_commit():
        started_before = game_has_started(old)
        started_after = game_has_started(instance)
        ended_before = game_has_ended(old)
        ended_after = game_has_ended(instance)
        if not started_before and started_after:
            notify_admin_game_lifecycle(instance, 'started')
        if not ended_before and ended_after:
            notify_admin_game_lifecycle(instance, 'ended')

    transaction.on_commit(after_commit)


@receiver(post_save, sender=Registration)
def telegram_notify_registration_milestone(sender, instance, created, **kwargs):
    if not created or instance.game_id is None:
        return

    def after_commit():
        from games.models import Registration as RegistrationModel

        count = RegistrationModel.objects.filter(game_id=instance.game_id).count()
        milestone = registration_milestone_reached(count - 1, count)
        if milestone is not None:
            notify_admin_registration_milestone(instance.game, milestone)

    transaction.on_commit(after_commit)
