from django.core.management.base import BaseCommand

from games.telegram.ladder_channel import (
    process_ladder_channel_tick,
    publish_ladder_channel_post,
    schedule_ladder_channel_post,
)
from games.telegram.mtproto import telegram_user_configured


class Command(BaseCommand):
    help = (
        'Schedule today\'s ladder into the channel\'s Telegram queue for 16:30 MSK '
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
            post = publish_ladder_channel_post(force=force or options['now'], notify_admin=notify_admin)
            if post is None:
                self.stderr.write('Publish failed (no today ladder / channel?).')
                return
            self.stdout.write('Published ladder №{} status={} message_id={}'.format(
                post.ladder_number, post.status, post.telegram_message_id,
            ))
            return

        if action in ('schedule', 'prepare'):
            post = schedule_ladder_channel_post(force=force, notify_admin=notify_admin)
            if post is None:
                self.stderr.write('Schedule failed (no today ladder / not configured?).')
                return
            self.stdout.write(
                'Ladder №{} status={} scheduled_for={} message_id={}'.format(
                    post.ladder_number,
                    post.status,
                    post.scheduled_for,
                    post.telegram_message_id,
                )
            )
            if post.error:
                self.stderr.write(post.error)
            if post.status == 'failed':
                return
            return

        stats = process_ladder_channel_tick()
        self.stdout.write(
            'Ladder channel tick: scheduled={scheduled} skipped={skipped}'.format(**stats)
        )
