"""Refresh the long-lived Instagram access token (run well before its 60-day expiry).

The Instagram API with Instagram Login issues long-lived tokens that expire after 60 days.
A fresh token can be requested any time the current one is still valid (and >24h old); the
new token resets the clock to ~60 days. Schedule this (e.g. weekly cron / EB scheduled task)
so the feed never goes dark.

Locally (secrets/ present) the new token is written back to
``secrets/instagram_access_token.txt`` automatically. On Elastic Beanstalk the token comes
from the INSTAGRAM_ACCESS_TOKEN env var, so the command prints the new value and you update
the env var (the command cannot self-mutate EB configuration).
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand

from games.instagram.api import clear_feed_cache, instagram_configured, refresh_access_token


class Command(BaseCommand):
    help = 'Refresh the long-lived Instagram access token before it expires.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-write',
            action='store_true',
            help='Do not write the new token to secrets/instagram_access_token.txt.',
        )

    def handle(self, *args, **options):
        if not instagram_configured():
            self.stderr.write('INSTAGRAM_ACCESS_TOKEN is not configured; nothing to refresh.')
            return

        try:
            payload = refresh_access_token()
        except RuntimeError as exc:
            self.stderr.write(str(exc))
            return

        new_token = payload.get('access_token')
        expires_in = payload.get('expires_in')
        if not new_token:
            self.stderr.write(f'Unexpected refresh response: {payload}')
            return

        days = round(int(expires_in) / 86400) if expires_in else '?'
        self.stdout.write(self.style.SUCCESS(f'Token refreshed; valid ~{days} more days.'))

        secrets_path = os.path.join(settings.BASE_DIR, 'secrets', 'instagram_access_token.txt')
        if not options['no_write'] and os.path.isdir(os.path.dirname(secrets_path)):
            with open(secrets_path, 'w', encoding='utf-8') as f:
                f.write(new_token + '\n')
            self.stdout.write(f'Wrote new token to {secrets_path}')
        else:
            self.stdout.write('New token (set INSTAGRAM_ACCESS_TOKEN to this value):')
            self.stdout.write(new_token)

        clear_feed_cache()
