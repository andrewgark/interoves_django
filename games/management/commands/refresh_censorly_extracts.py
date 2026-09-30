"""Re-fetch Wikipedia extracts for existing Цензурки puzzles (longer trim, flags)."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from games.censorly import CENSORLY_TAGS_KEY, CENSORLY_TASK_TYPE
from games.censorly.play import CENSORLY_BASE_POINTS, puzzle_from_task
from games.censorly.tokenize import build_puzzle_payload, title_content_lemmas
from games.censorly.wiki import WikiFetchError, fetch_article
from games.models import RandomCensorlyGame, Task


class Command(BaseCommand):
    help = (
        'Re-download Wikipedia plaintext for censorly tasks and rebuild puzzle tags '
        '(applies current MAX_BODY_CHARS / tail-section trim / truncated flag).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Max tasks to refresh (0 = all)',
        )
        parser.add_argument(
            '--only-truncated',
            action='store_true',
            help='Skip tasks explicitly marked truncated=False',
        )

    def handle(self, *args, **options):
        dry = options['dry_run']
        limit = int(options['limit'] or 0)
        only_truncated = bool(options['only_truncated'])

        qs = Task.objects.filter(task_type=CENSORLY_TASK_TYPE, is_removed=False).order_by('id')

        updated = 0
        skipped = 0
        errors = 0
        for task in qs.iterator():
            if limit and updated >= limit:
                break
            old = puzzle_from_task(task) or {}
            title = (old.get('wiki_title') or task.answer or '').strip()
            if not title:
                skipped += 1
                continue
            if only_truncated and old.get('truncated') is False:
                skipped += 1
                continue
            try:
                article = fetch_article(title)
            except WikiFetchError as exc:
                self.stderr.write(f'#{task.pk} {title}: {exc}')
                errors += 1
                continue
            puzzle = build_puzzle_payload(
                wiki_title=article.title,
                body_text=article.extract,
                wiki_pageid=article.pageid,
                truncated=bool(article.truncated),
            )
            if not title_content_lemmas(puzzle):
                self.stderr.write(f'#{task.pk} {title}: no title lemmas after refresh')
                errors += 1
                continue
            body_n = len(puzzle.get('body_tokens') or [])
            old_n = len(old.get('body_tokens') or [])
            self.stdout.write(
                f'#{task.pk} {article.title}: tokens {old_n} -> {body_n} '
                f'truncated={puzzle["truncated"]}'
            )
            if dry:
                updated += 1
                continue
            with transaction.atomic():
                tags = dict(task.tags or {})
                tags[CENSORLY_TAGS_KEY] = puzzle
                task.tags = tags
                task.answer = article.title
                update_fields = ['tags', 'answer']
                if task.points != CENSORLY_BASE_POINTS:
                    task.points = CENSORLY_BASE_POINTS
                    update_fields.append('points')
                task.save(update_fields=update_fields)
                tg = task.task_group
                if tg is not None and tg.points != CENSORLY_BASE_POINTS:
                    tg.points = CENSORLY_BASE_POINTS
                    tg.save(update_fields=['points'])
                if tg is not None:
                    RandomCensorlyGame.objects.filter(task_group=tg).update(
                        wiki_title=article.title,
                    )
            updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done updated={updated} skipped={skipped} errors={errors} dry_run={dry}',
        ))
