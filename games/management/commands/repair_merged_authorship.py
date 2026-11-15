"""Move authorship left on accounts merged before merges moved it (dry-run by default)."""

from django.core.management.base import BaseCommand
from django.db import transaction

from games.identity.merge.account import _merge_authorship
from games.models import AccountMerge, GameAuthor, TaskGroup


class Command(BaseCommand):
    help = 'Move release authorship and Десяточка grants from merged-away accounts to their targets.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write the changes.')

    def handle(self, *args, **options):
        merges = AccountMerge.objects.filter(
            source_user__isnull=False, target_user__isnull=False,
        ).select_related('source_user', 'target_user').order_by('pk')
        total = [0, 0]
        for merge in merges:
            groups = TaskGroup.authors.through.objects.filter(profile_id=merge.source_user_id).count()
            grants = GameAuthor.objects.filter(profile_id=merge.source_user_id).count()
            if not (groups or grants):
                continue
            self.stdout.write('merge {}: user {} -> {}: {} releases, {} Десяточка grants'.format(
                merge.pk, merge.source_user_id, merge.target_user_id, groups, grants,
            ))
            if options['apply']:
                with transaction.atomic():
                    moved = _merge_authorship(merge.target_user, merge.source_user)
                total[0] += moved[0]
                total[1] += moved[1]
            else:
                total[0] += groups
                total[1] += grants
        self.stdout.write('{}: {} releases, {} grants'.format(
            'moved' if options['apply'] else 'would move (dry run)', *total,
        ))
