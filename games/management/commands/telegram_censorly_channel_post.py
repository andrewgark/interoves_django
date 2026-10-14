from django.core.management.base import BaseCommand

from games.telegram.censorly_channel import (
    process_censorly_channel_tick,
    publish_censorly_channel_post,
    schedule_censorly_channel_post,
)
from games.telegram.mtproto import telegram_user_configured


class Command(BaseCommand):
    help = (
        'Schedule today\'s Censorly for 02:00 MSK. Default action=tick prepares '
        'it at 00:15 MSK.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'action', nargs='?', default='tick',
            choices=('tick', 'schedule', 'prepare', 'publish'),
        )
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--now', action='store_true', help='Publish immediately')
        parser.add_argument('--no-admin-preview', action='store_true')

    def handle(self, *args, **options):
        if not telegram_user_configured():
            self.stderr.write(
                'Configure TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_USER_SESSION '
                'and TELEGRAM_CHANNEL_CHAT_ID.'
            )
            return
        action = options['action']
        force = options['force']
        notify_admin = not options['no_admin_preview']
        if options['now'] or action == 'publish':
            self.stderr.write(
                'WARNING: this publishes to the channel immediately (not scheduled).'
            )
            post = publish_censorly_channel_post(
                force=force or options['now'], notify_admin=notify_admin,
            )
            if post is None:
                self.stderr.write('Publish failed (no today Censorly / channel?).')
                return
            self.stdout.write('Censorly №{} status={} message_id={}'.format(
                post.ladder_number, post.telegram_status, post.telegram_external_id,
            ))
            return
        if action in ('schedule', 'prepare'):
            post = schedule_censorly_channel_post(force=force, notify_admin=notify_admin)
            if post is None:
                self.stderr.write('Schedule failed (no today Censorly / not configured?).')
                return
            self.stdout.write(
                'Censorly №{} status={} scheduled_for={} message_id={}'.format(
                    post.ladder_number, post.telegram_status,
                    post.telegram_scheduled_for, post.telegram_external_id,
                )
            )
            if post.telegram_error:
                self.stderr.write(post.telegram_error)
            return
        stats = process_censorly_channel_tick()
        self.stdout.write(
            'Censorly channel tick: scheduled={scheduled} skipped={skipped}'.format(**stats)
        )
