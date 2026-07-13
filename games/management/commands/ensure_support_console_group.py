from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from games.support.constants import SUPPORT_CONSOLE_GROUP


class Command(BaseCommand):
    help = "Create Support Console group and add user 'admin' to it."

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            default='admin',
            help='User to add to the group (default: admin).',
        )

    def handle(self, *args, **options):
        group, created = Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)
        if created:
            self.stdout.write(self.style.SUCCESS('Created group "{}".'.format(SUPPORT_CONSOLE_GROUP)))
        else:
            self.stdout.write('Group "{}" already exists.'.format(SUPPORT_CONSOLE_GROUP))

        username = options['username']
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stderr.write(self.style.ERROR('No user with username "{}".'.format(username)))
            return

        group.user_set.add(user)
        self.stdout.write(self.style.SUCCESS('Added "{}" to "{}".'.format(username, SUPPORT_CONSOLE_GROUP)))
