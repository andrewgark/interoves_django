"""Post a ladder teaser to Instagram now (manual test / catch-up).

Defaults to today's published ladder. Publishes immediately to @interoveslocumpraesta and,
if a TelegramLadderChannelPost row exists for that day, records the media id on it. This is
the same publishing path the 00:15 MSK cron uses (via _maybe_post_ladder_to_instagram), so
it's a faithful test of the cron behaviour.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse

from games.instagram.api import publish_configured, publish_image_url
from games.twitter.api import html_caption_to_plain
from games.telegram.ladder_channel import (
    build_caption,
    resolve_ladder_by_number,
    resolve_today_ladder,
)
from games.telegram.models import TelegramLadderChannelPost


class Command(BaseCommand):
    help = "Post a ladder teaser to Instagram now (default: today's ladder)."

    def add_arguments(self, parser):
        parser.add_argument('--number', type=int, help='Ladder number (default: today).')

    def handle(self, *args, **options):
        if not publish_configured():
            self.stderr.write('INSTAGRAM_ACCESS_TOKEN is not configured.')
            return

        number = options.get('number')
        ladder = resolve_ladder_by_number(number) if number else resolve_today_ladder()
        if ladder is None:
            self.stderr.write(
                'No published ladder found ({}).'.format(
                    'number={}'.format(number) if number else 'today'
                )
            )
            return

        caption = html_caption_to_plain(build_caption(ladder))
        if not caption:
            caption = 'Лесенка №{}\n{}'.format(ladder.number, ladder.play_url)
        image_url = settings.SITE_BASE_URL + reverse(
            'ladder_teaser_jpg', args=[ladder.number]
        )
        self.stdout.write('Image URL: {}'.format(image_url))

        try:
            media_id = publish_image_url(image_url, caption)
        except RuntimeError as exc:
            self.stderr.write('Instagram publish failed: {}'.format(exc))
            return

        self.stdout.write(
            self.style.SUCCESS(
                'Published ladder №{} to Instagram; media_id={}'.format(
                    ladder.number, media_id
                )
            )
        )

        # Best-effort: record on today's channel post if it exists.
        post = TelegramLadderChannelPost.objects.filter(
            ladder_date=ladder.ladder_date
        ).first()
        if post is not None:
            post.instagram_media_id = media_id
            post.instagram_error = ''
            post.save(update_fields=['instagram_media_id', 'instagram_error'])
            self.stdout.write('Recorded media id on TelegramLadderChannelPost.')
