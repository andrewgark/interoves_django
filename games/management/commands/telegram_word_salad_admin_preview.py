from django.core.management.base import BaseCommand

from games.ops.telegram.channels.salad import preview_salad_to_admin


class Command(BaseCommand):
    help = (
        'Render today\'s salad teaser and send it to the admin bot chat only '
        '(not the public channel).'
    )

    def handle(self, *args, **options):
        ok, message = preview_salad_to_admin()
        if ok:
            self.stdout.write(self.style.SUCCESS(message))
        else:
            self.stderr.write(message)
