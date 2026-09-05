from django.core.management.base import BaseCommand

from games.models import ClubSubscription, ClubSubscriptionEvent, Profile


class Command(BaseCommand):
    help = 'Show local Club subscription state for a user id or Telegram user id.'

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int)
        parser.add_argument('--telegram-user-id', type=int)

    def handle(self, *args, **options):
        user_id = options.get('user_id')
        telegram_user_id = options.get('telegram_user_id')
        if not user_id and not telegram_user_id:
            self.stderr.write('Pass --user-id or --telegram-user-id')
            return
        subscription = None
        if user_id:
            subscription = ClubSubscription.objects.filter(user_id=user_id).first()
        if subscription is None and telegram_user_id:
            subscription = ClubSubscription.objects.filter(telegram_user_id=telegram_user_id).first()
            profile = Profile.objects.filter(
                telegram_user_id=telegram_user_id, telegram_verified=True,
            ).select_related('user').first()
            if profile:
                self.stdout.write('telegram_user_id={} -> user_id={}'.format(
                    telegram_user_id, profile.user_id,
                ))
            else:
                self.stdout.write('No verified Profile for telegram_user_id={}'.format(telegram_user_id))
        if subscription is None:
            self.stdout.write('No ClubSubscription row')
            events = ClubSubscriptionEvent.objects.all()
            if telegram_user_id:
                events = events.filter(telegram_user_id=telegram_user_id)
            for event in events[:20]:
                self.stdout.write(
                    '{received_at} {event_name} result={result} sub={sub} tg={tg}'.format(
                        received_at=event.received_at,
                        event_name=event.event_name,
                        result=event.result,
                        sub=event.tribute_subscription_id,
                        tg=event.telegram_user_id,
                    )
                )
            return
        self.stdout.write(
            'user_id={user} status={status} access={access} currency={currency} '
            'amount={amount} auto_renew={auto} paid_until={paid} '
            'tribute_subscription_id={sid} telegram_user_id={tg} '
            'last_event={event} last_webhook_at={when} duplicate={dup}'.format(
                user=subscription.user_id,
                status=subscription.status,
                access=subscription.grants_access(),
                currency=subscription.currency,
                amount=subscription.amount,
                auto=subscription.auto_renew,
                paid=subscription.paid_until,
                sid=subscription.tribute_subscription_id,
                tg=subscription.telegram_user_id,
                event=subscription.last_webhook_event,
                when=subscription.last_webhook_at,
                dup=subscription.duplicate_detected,
            )
        )
        for event in subscription.events.all()[:20]:
            self.stdout.write(
                '  {received_at} {event_name} result={result} period={period} expires={expires}'.format(
                    received_at=event.received_at,
                    event_name=event.event_name,
                    result=event.result,
                    period=event.tribute_period_id,
                    expires=event.expires_at,
                )
            )
