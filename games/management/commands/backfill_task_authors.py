"""Dry-run-first exact matching for textual task author attribution."""

import re

from django.core.management.base import BaseCommand

from games.models import LadderOffer, Profile, TaskGroup, WordSaladOffer


def normalize(value):
    return ' '.join(str(value or '').casefold().split())


def split_authors(value):
    return [part.strip() for part in re.split(r'\s*(?:,|;|/|&|\+|\band\b|\bи\b)\s*', value or '', flags=re.I) if part.strip()]


class Command(BaseCommand):
    help = 'Match existing textual task authors to Profiles (dry-run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Link only unique exact normalized matches.')

    def handle(self, *args, **options):
        profiles = list(Profile.objects.select_related('user').order_by('pk'))
        names = {}
        for profile in profiles:
            candidates = {
                '{} {}'.format(profile.first_name, profile.last_name),
                profile.user.get_full_name(),
                profile.user.get_username(),
            }
            for candidate in candidates:
                key = normalize(candidate)
                if key:
                    names.setdefault(key, set()).add(profile.pk)

        counts = {'unique': 0, 'ambiguous': 0, 'not found': 0, 'already linked': 0}
        offer_author_by_group = {}
        for offer_id, author, group_id in LadderOffer.objects.values_list('pk', 'author', 'task_group_id'):
            if author.strip():
                offer_author_by_group.setdefault(group_id, []).append(
                    ('ladder_offer', offer_id, author),
                )
        for offer_id, author, group_id in WordSaladOffer.objects.values_list('pk', 'author', 'task_group_id'):
            if group_id and author.strip():
                offer_author_by_group.setdefault(group_id, []).append(
                    ('word_salad_offer', offer_id, author),
                )

        groups = TaskGroup.objects.prefetch_related('authors', 'tasks').order_by('pk')
        for group in groups:
            tasks = list(group.tasks.all())
            sources = [
                (task.task_type, task.pk, str((task.tags or {}).get('author') or '').strip())
                for task in tasks
            ] + offer_author_by_group.get(group.pk, [])
            for task_type, source_id, text in sources:
                if not text:
                    continue
                for name in split_authors(text):
                    matches = names.get(normalize(name), set())
                    linked_ids = {profile.user_id for profile in group.authors.all()}
                    already = matches and matches.issubset(linked_ids)
                    if already:
                        status = 'already linked'
                        candidates = sorted(matches)
                        reason = 'existing relation'
                    elif len(matches) == 1:
                        status = 'unique'
                        candidates = sorted(matches)
                        reason = 'normalized exact display/username match'
                    elif len(matches) > 1:
                        status = 'ambiguous'
                        candidates = sorted(matches)
                        reason = 'same normalized name on multiple profiles'
                    else:
                        status = 'not found'
                        candidates = []
                        reason = 'no normalized exact match'
                    counts[status] += 1
                    candidate_profiles = [
                        '{} {} (@{})'.format(p.first_name, p.last_name, p.user.get_username())
                        for p in profiles if p.pk in candidates
                    ]
                    self.stdout.write(
                        'task_type={} task_id={} task_group={} textual_author={!r} candidates={} reason={} status={}'.format(
                            task_type, source_id, group.pk, name,
                            candidate_profiles or '—', reason, status,
                        )
                    )
                    if options['apply'] and status == 'unique':
                        group.authors.add(candidates[0])
        self.stdout.write('Summary: {}'.format(', '.join('{}={}'.format(k, v) for k, v in counts.items())))
        if not options['apply']:
            self.stdout.write('Dry run only. Pass --apply to link unique exact matches.')
