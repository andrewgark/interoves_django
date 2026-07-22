"""Post a ladder teaser to Instagram now (manual test / catch-up).

Defaults to today's published ladder. Uses SocialQueuePost when present (same path as
the 00:15 MSK cron); otherwise creates/updates a ladder queue row and publishes.
"""

from django.core.management.base import BaseCommand

from games.instagram.api import publish_configured
from games.social.models import SocialQueuePost
from games.social.publish import publish_instagram
from games.telegram.ladder_channel import (
    build_caption,
    resolve_ladder_by_number,
    resolve_today_ladder,
)
from games.telegram.ladder_image import render_ladder_teaser_png


class Command(BaseCommand):
    help = "Post a ladder teaser to Instagram now (default: today's ladder)."

    def add_arguments(self, parser):
        parser.add_argument('--number', type=int, help='Ladder number (default: today).')
        parser.add_argument(
            '--force',
            action='store_true',
            help='Repost even if already recorded as sent',
        )

    def handle(self, *args, **options):
        if not publish_configured():
            self.stderr.write('INSTAGRAM_ACCESS_TOKEN is not configured.')
            return

        number = options.get('number')
        force = options.get('force')
        ladder = resolve_ladder_by_number(number) if number else resolve_today_ladder()
        if ladder is None:
            self.stderr.write(
                'No published ladder found ({}).'.format(
                    'number={}'.format(number) if number else 'today'
                )
            )
            return

        post = SocialQueuePost.objects.filter(
            source=SocialQueuePost.SOURCE_LADDER,
            ladder_date=ladder.ladder_date,
        ).first()
        if post is None:
            post = SocialQueuePost(
                source=SocialQueuePost.SOURCE_LADDER,
                ladder_date=ladder.ladder_date,
                ladder_number=ladder.number,
                play_url=ladder.play_url,
            )
        post.ladder_number = ladder.number
        post.play_url = ladder.play_url
        if not post.caption:
            post.caption = build_caption(ladder)
        if not post.image:
            png = render_ladder_teaser_png(ladder.task, ladder_number=ladder.number)
            post.set_image_bytes(png, filename='ladder-{}.png'.format(ladder.number))
        post.save()

        publish_instagram(post, force=force)
        post.refresh_from_db()
        if post.instagram_status == SocialQueuePost.STATUS_SENT:
            self.stdout.write(
                self.style.SUCCESS(
                    'Published ladder №{} to Instagram; media_id={}'.format(
                        post.ladder_number, post.instagram_external_id,
                    )
                )
            )
        else:
            self.stderr.write(
                'Instagram status={} error={}'.format(
                    post.instagram_status, post.instagram_error,
                )
            )
