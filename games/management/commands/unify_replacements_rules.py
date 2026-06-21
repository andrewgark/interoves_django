"""
Point all «Замены» TaskGroup.rules at section_tutorial_replacements.

Replaces legacy HTMLPage names:
  - Правила "Замены"
  - Правила "Замены" (лучше)

Also sets rules on hub circles that had no TaskGroup.rules (fallback only).

Idempotent. Safe to re-run.

Usage:
    python manage.py unify_replacements_rules --dry-run
    python manage.py unify_replacements_rules
"""

from django.core.management.base import BaseCommand, CommandError

from games.models import Game, GameTaskGroup, HTMLPage, TaskGroup

TARGET_RULES = 'section_tutorial_replacements'
LEGACY_RULES = (
    'Правила "Замены"',
    'Правила "Замены" (лучше)',
)
HUB_GAME_ID = 'replacements'


class Command(BaseCommand):
    help = 'Set TaskGroup.rules to section_tutorial_replacements for all «Замены» circles.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print counts only; do not update the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if not HTMLPage.objects.filter(pk=TARGET_RULES).exists():
            raise CommandError(f'HTMLPage {TARGET_RULES!r} does not exist')

        legacy_qs = TaskGroup.objects.filter(rules_id__in=LEGACY_RULES)
        hub_null_ids = list(
            GameTaskGroup.objects.filter(game_id=HUB_GAME_ID, task_group__rules__isnull=True)
            .values_list('task_group_id', flat=True)
        )
        hub_null_qs = TaskGroup.objects.filter(pk__in=hub_null_ids)

        legacy_count = legacy_qs.count()
        hub_null_count = hub_null_qs.count()

        self.stdout.write(f'Legacy rules pages -> {TARGET_RULES!r}: {legacy_count} task group(s)')
        for name in LEGACY_RULES:
            n = legacy_qs.filter(rules_id=name).count()
            if n:
                self.stdout.write(f'  {name!r}: {n}')
        self.stdout.write(f'Hub {HUB_GAME_ID!r} with NULL rules -> {TARGET_RULES!r}: {hub_null_count}')

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run — no changes written.'))
            return

        updated_legacy = legacy_qs.update(rules_id=TARGET_RULES)
        updated_null = hub_null_qs.update(rules_id=TARGET_RULES)
        self.stdout.write(
            self.style.SUCCESS(
                f'Updated {updated_legacy} legacy + {updated_null} hub-null task group(s).',
            ),
        )
