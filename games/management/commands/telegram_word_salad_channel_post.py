from django.core.management.base import BaseCommand

from games.telegram.mtproto import telegram_user_configured
from games.telegram.word_salad_channel import (
    process_salad_channel_tick,
    publish_salad_channel_post,
    schedule_salad_channel_post,
)


class Command(BaseCommand):
    help = (
        'Schedule today\'s salad into the channel\'s Telegram queue for 14:30 MSK '
        '(MTProto schedule_date via user session). Default action=tick runs at 00:15 MSK.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            nargs='?',
            default='tick',
            choices=('tick', 'schedule', 'prepare', 'publish'),
            help='tick (00:15 window), schedule/prepare (force schedule), publish (send now)',
        )
        parser.add_argument('--force', action='store_true', help='Reschedule even if already done today')
        parser.add_argument(
            '--now',
            action='store_true',
            help='Send immediately (no Telegram schedule queue)',
        )
        parser.add_argument(
            '--no-admin-preview',
            action='store_true',
            help='Do not send draft preview to admin bot chat',
        )

    def handle(self, *args, **options):
        if not telegram_user_configured():
            self.stderr.write(
                'Configure TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_USER_SESSION '
                '(manage.py telegram_user_login) and TELEGRAM_CHANNEL_CHAT_ID=@interoves'
            )
            return

        action = options['action']
        force = options['force']
        notify_admin = not options['no_admin_preview']

        if options['now'] or action == 'publish':
            self.stderr.write(
                'WARNING: this publishes to the channel immediately (not scheduled).'
            )
            post = publish_salad_channel_post(force=force or options['now'], notify_admin=notify_admin)
            if post is None:
                self.stderr.write('Publish failed (no today salad / channel?).')
                return
            self.stdout.write('Published salad №{} status={} message_id={}'.format(
                post.ladder_number, post.telegram_status, post.telegram_external_id,
            ))
            return

        if action in ('schedule', 'prepare'):
            post = schedule_salad_channel_post(force=force, notify_admin=notify_admin)
            if post is None:
                self.stderr.write('Schedule failed (no today salad / not configured?).')
                return
            self.stdout.write(
                'Salad №{} status={} scheduled_for={} message_id={}'.format(
                    post.ladder_number,
                    post.telegram_status,
                    post.telegram_scheduled_for,
                    post.telegram_external_id,
                )
            )
            if post.telegram_error:
                self.stderr.write(post.telegram_error)
            if post.telegram_status == 'failed':
                return
            return

        stats = process_salad_channel_tick()
        self.stdout.write(
            'Salad channel tick: scheduled={scheduled} skipped={skipped}'.format(**stats)
        )
