"""Keep the long-lived Instagram token alive (safe to run daily via cron).

Instagram long-lived tokens expire after 60 days; refreshing (while still valid and >24h
old) resets the clock ~60 days and yields a *new* token string. We persist the live token
in the DB (InstagramToken) so a refresh doesn't require rewriting the EB env var / a
redeploy — runtime reads the DB token first (see games.instagram.api.current_access_token).

Behaviour:
- First run with no DB row: seed it from INSTAGRAM_ACCESS_TOKEN (no API call).
- Otherwise: refresh only if the stored token is older than --max-age-days (default 30),
  so a daily cron actually hits the API roughly monthly — well within the 60-day window.
- --force refreshes regardless of age.
"""

from django.core.management.base import BaseCommand

from games.instagram.refresh import run_instagram_token_refresh


class Command(BaseCommand):
    help = 'Refresh/seed the long-lived Instagram token in the DB (safe to run daily).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Refresh via the API regardless of token age.',
        )
        parser.add_argument(
            '--max-age-days',
            type=int,
            default=30,
            help='Only refresh if the stored token is older than this many days (default 30).',
        )

    def handle(self, *args, **options):
        result = run_instagram_token_refresh(
            force=options['force'],
            max_age_days=options['max_age_days'],
        )
        if result is None:
            self.stdout.write('Instagram refresh skipped: lock held')
            return
        action = result.get('action')
        if action == 'missing_seed':
            self.stderr.write(
                'No DB token and INSTAGRAM_ACCESS_TOKEN is unset; nothing to seed.'
            )
            return
        if action == 'seeded':
            self.stdout.write('Seeded Instagram token in DB from INSTAGRAM_ACCESS_TOKEN.')
            return
        if action == 'skipped':
            self.stdout.write(
                'Token refreshed {}d ago (< {}d); skipping.'.format(
                    result['age_days'], result['max_age_days'],
                )
            )
            return
        if action == 'failed':
            self.stderr.write('Instagram token refresh failed.')
            return
        days = result.get('expires_days')
        if days is None:
            days = '?'
        self.stdout.write(
            self.style.SUCCESS('Instagram token refreshed; valid ~{} more days.'.format(days))
        )
