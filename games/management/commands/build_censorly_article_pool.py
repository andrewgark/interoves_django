"""Build / filter / publish the large Цензурки article pool."""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from games.censorly.article_pool import load_article_pool
from games.censorly.pool_build import ARTICLE_POOL_PATH, DEFAULT_CACHE_DIR
from games.censorly.pool_build.balance import balance_pool, bucket_for, primary_theme
from games.censorly.pool_build.filters import enrich_candidates
from games.censorly.pool_build.sources import run_harvest


class Command(BaseCommand):
    help = (
        'Harvest / filter / publish Цензурки article pool candidates '
        '(vital lists + pageviews → var/censorly_pool/).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            choices=('harvest', 'filter', 'publish', 'all'),
            help='Pipeline step',
        )
        parser.add_argument(
            '--cache-dir',
            default=str(DEFAULT_CACHE_DIR),
            help='Directory for harvest/filter artifacts',
        )
        parser.add_argument('--pageview-days', type=int, default=30)
        parser.add_argument('--target-size', type=int, default=10000)
        parser.add_argument('--min-bytes', type=int, default=8000)
        parser.add_argument('--min-langlinks', type=int, default=20)
        parser.add_argument('--review-sample-size', type=int, default=150)
        parser.add_argument('--seed', type=int, default=42)

    def handle(self, *args, **options):
        cache_dir = Path(options['cache_dir'])
        cache_dir.mkdir(parents=True, exist_ok=True)
        action = options['action']
        if action in ('harvest', 'all'):
            self._harvest(cache_dir, options)
        if action in ('filter', 'all'):
            self._filter(cache_dir, options)
        if action in ('publish', 'all'):
            self._publish(cache_dir)

    def _harvest(self, cache_dir: Path, options):
        self.stdout.write('Harvesting vital lists + pageviews + current pool…')
        payload = run_harvest(pageview_days=int(options['pageview_days']))
        path = cache_dir / 'harvest.json'
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=0), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(
            f'Wrote {payload["candidate_count"]} candidates → {path}'
        ))

    def _filter(self, cache_dir: Path, options):
        harvest_path = cache_dir / 'harvest.json'
        if not harvest_path.is_file():
            raise CommandError(f'Missing {harvest_path}; run harvest first')
        harvest = json.loads(harvest_path.read_text(encoding='utf-8'))
        candidates = harvest.get('candidates') or []
        self.stdout.write(f'Filtering {len(candidates)} candidates…')

        def progress(batch_i, total, n):
            self.stdout.write(f'  API batch {batch_i}/{total} ({n} titles)')

        accepted, rejected = enrich_candidates(
            candidates,
            cache_path=cache_dir / 'meta_cache.json',
            min_bytes=int(options['min_bytes']),
            min_langlinks=int(options['min_langlinks']),
            progress_cb=progress,
        )
        rejected_path = cache_dir / 'rejected.jsonl'
        with rejected_path.open('w', encoding='utf-8') as fh:
            for row in rejected:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')

        final, balance_report = balance_pool(
            accepted,
            target_size=int(options['target_size']),
            seed=int(options['seed']),
        )

        draft_path = cache_dir / 'pool_draft.txt'
        lines = []
        for row in final:
            title = row.get('canonical') or row['title']
            lines.append(title)
        draft_path.write_text('\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')

        sample = self._review_sample(final, size=int(options['review_sample_size']), seed=int(options['seed']))
        sample_path = cache_dir / 'review_sample.txt'
        sample_path.write_text(
            '\n'.join(
                f"{(r.get('canonical') or r['title'])}\t{bucket_for(r)}\t"
                f"{primary_theme(r) or '-'}\t{','.join(r.get('sources') or [])}"
                for r in sample
            ) + '\n',
            encoding='utf-8',
        )

        reject_reasons = Counter(r.get('reject') or 'unknown' for r in rejected)
        current = set(load_article_pool())
        draft_set = set(lines)
        report = {
            'accepted_before_balance': len(accepted),
            'rejected': len(rejected),
            'reject_reasons': dict(reject_reasons.most_common()),
            'balance': balance_report,
            'overlap_current_pool': len(current & draft_set),
            'current_pool_size': len(current),
            'draft_size': len(lines),
            'draft_path': str(draft_path),
            'sample_path': str(sample_path),
        }
        report_json = cache_dir / 'report.json'
        report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        report_md = cache_dir / 'report.md'
        report_md.write_text(self._format_report_md(report, sample), encoding='utf-8')

        accepted_path = cache_dir / 'accepted.json'
        accepted_path.write_text(json.dumps(accepted, ensure_ascii=False), encoding='utf-8')

        self.stdout.write(self.style.SUCCESS(
            f'Draft {len(lines)} titles → {draft_path}\n'
            f'Report → {report_md}\n'
            f'Review sample ({len(sample)}) → {sample_path}'
        ))

    def _review_sample(self, rows: list, *, size: int, seed: int) -> list:
        rng = random.Random(seed)
        by_bucket: dict[str, list] = {}
        for row in rows:
            by_bucket.setdefault(bucket_for(row), []).append(row)
        sample = []
        buckets = list(by_bucket.keys())
        if not buckets:
            return []
        per = max(1, size // len(buckets))
        for bucket in buckets:
            pool = list(by_bucket[bucket])
            rng.shuffle(pool)
            sample.extend(pool[:per])
        rng.shuffle(sample)
        return sample[:size]

    def _format_report_md(self, report: dict, sample: list) -> str:
        bal = report.get('balance') or {}
        lines = [
            '# Censorly pool draft report',
            '',
            f"- Draft size: **{report.get('draft_size')}** (target {bal.get('target_size')})",
            f"- Accepted before balance: {report.get('accepted_before_balance')}",
            f"- Rejected: {report.get('rejected')}",
            f"- Overlap with current pool: {report.get('overlap_current_pool')} / {report.get('current_pool_size')}",
            '',
            '## Buckets',
            '',
        ]
        for k, v in sorted((bal.get('bucket_counts') or {}).items()):
            lines.append(f'- `{k}`: {v}')
        lines.extend(['', '## Top reject reasons', ''])
        for k, v in list((report.get('reject_reasons') or {}).items())[:20]:
            lines.append(f'- `{k}`: {v}')
        lines.extend(['', '## Review sample (first 40)', ''])
        for row in sample[:40]:
            title = row.get('canonical') or row['title']
            lines.append(
                f"- {title} — {bucket_for(row)} / {primary_theme(row) or '-'} / "
                f"{','.join(row.get('sources') or [])}"
            )
        lines.append('')
        return '\n'.join(lines)

    def _publish(self, cache_dir: Path):
        draft = cache_dir / 'pool_draft.txt'
        if not draft.is_file():
            raise CommandError(f'Missing {draft}; run filter first')
        text = draft.read_text(encoding='utf-8')
        titles = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith('#')]
        if len(titles) < 100:
            raise CommandError(f'Draft looks too small ({len(titles)} titles)')
        ARTICLE_POOL_PATH.write_text('\n'.join(titles) + '\n', encoding='utf-8')
        load_article_pool.cache_clear()
        self.stdout.write(self.style.SUCCESS(
            f'Published {len(titles)} titles → {ARTICLE_POOL_PATH}'
        ))
