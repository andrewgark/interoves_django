#!/usr/bin/env python3
"""
Импорт салатов из Google Sheets в раздел «Салат» (game id ``word_salad``).

Таблица: https://docs.google.com/spreadsheets/d/1oPpssEKL7ZLGiflAB6B0U3oYK2YITZKFLJzPFhiW9Ac/edit?gid=614962790

Колонки: номер, сетка 4×4 в C–F, слова в H, тема в I.

Использование (из корня репозитория)::

    ../venv/interoves_django/bin/python scripts/import_word_salad_tasks.py
    ../venv/interoves_django/bin/python scripts/import_word_salad_tasks.py --dry-run
    ../venv/interoves_django/bin/python scripts/import_word_salad_tasks.py --csv /path/to.csv
    ../venv/interoves_django/bin/python scripts/import_word_salad_tasks.py --only 1-5

На проде::

    ./scripts/eb_run.sh scripts/import_word_salad_tasks.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

import django

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interoves_django.settings")
django.setup()

from games.models import CheckerType, GameTaskGroup, Task, TaskGroup  # noqa: E402
from games.support.services.word_salad import (  # noqa: E402
    WORD_SALAD_SECTION_TITLE,
    ensure_word_salad_game,
)
from games.word_salad import WORD_SALAD_GAME_ID, validate_puzzle  # noqa: E402
from games.word_salad_daily import (  # noqa: E402
    WORD_SALAD_DEFAULT_PUBLISH_START,
    WORD_SALAD_PUBLISH_START_TAG,
)
from games.word_salad_sheet import parse_word_salad_csv  # noqa: E402

SHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1oPpssEKL7ZLGiflAB6B0U3oYK2YITZKFLJzPFhiW9Ac/export?format=csv&gid=614962790"
)


def _fetch_csv(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read().decode("utf-8-sig")


def _parse_only_spec(spec: str | None) -> set[int] | None:
    if not spec or not spec.strip():
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a.strip()), int(b.strip())
            out.update(range(lo, hi + 1))
        else:
            out.add(int(part))
    return out


def run(
    *,
    dry_run: bool,
    csv_path: str | None,
    publish_start: str | None,
    sheet_url: str,
    only: set[int] | None,
    skip_publish_start: bool,
) -> int:
    hub = ensure_word_salad_game()

    if csv_path:
        csv_text = open(csv_path, encoding="utf-8-sig").read()
    else:
        print(f"Fetching {sheet_url} …")
        csv_text = _fetch_csv(sheet_url)

    salads = parse_word_salad_csv(csv_text)
    if only is not None:
        salads = {n: v for n, v in salads.items() if n in only}
    if not salads:
        print("No salads parsed.", file=sys.stderr)
        return 1

    planned = []
    errors = []
    for num in sorted(salads):
        entry = salads[num]
        try:
            grid, words = validate_puzzle(entry["grid"], entry["words"])
        except ValueError as exc:
            errors.append(f"salad #{num}: {exc}")
            continue
        planned.append((num, grid, words, entry.get("theme") or ""))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    if dry_run:
        for num, grid, words, theme in planned:
            extra = f" theme={theme!r}" if theme else ""
            print(f"would import salad #{num}: {len(words)} words{extra}")
        print(f"DRY RUN: {len(planned)} salad(s)")
        return 0

    if not skip_publish_start:
        start = publish_start or WORD_SALAD_DEFAULT_PUBLISH_START
        tags = dict(hub.tags or {})
        tags[WORD_SALAD_PUBLISH_START_TAG] = start
        hub.tags = tags
        hub.save(update_fields=["tags"])

    checker, _ = CheckerType.objects.get_or_create(pk="word_salad")
    created_tg = updated_tg = created_link = updated_link = created_task = updated_task = 0
    for num, grid, words, theme in planned:
        number = str(num)
        title = f"{WORD_SALAD_SECTION_TITLE} #{num}"
        checker_data = json.dumps({"grid": grid, "words": words}, ensure_ascii=False)
        link = (
            GameTaskGroup.objects.filter(game=hub, number=number)
            .select_related("task_group")
            .first()
        )
        if link:
            task_group = link.task_group
            updated_tg += 1
        else:
            task_group = TaskGroup.objects.create(
                label=f'salad:{num}',
                checker=checker,
                points=1,
                max_attempts=None,
                is_18_plus=False,
            )
            created_tg += 1

        task, task_created = Task.objects.update_or_create(
            task_group=task_group,
            number="1",
            defaults={
                "task_type": "word_salad",
                "checker": checker,
                "checker_data": checker_data,
                "answer": "",
                "text": theme,
                "tags": {},
                "points": 1,
                "max_attempts": None,
                "is_removed": False,
            },
        )
        if task_created:
            created_task += 1
        else:
            updated_task += 1

        link, link_created = GameTaskGroup.objects.update_or_create(
            game=hub,
            number=number,
            defaults={
                "task_group": task_group,
                "name": title,
            },
        )
        if link_created:
            created_link += 1
        else:
            updated_link += 1
            if not (link.name or "").strip():
                link.name = title
                link.save(update_fields=["name"])

        extra = f" theme={theme!r}" if theme else ""
        print(f"imported #{num}: {len(words)} words{extra}")

    pub = publish_start or WORD_SALAD_DEFAULT_PUBLISH_START
    if skip_publish_start:
        pub = "(unchanged)"
    print(
        f"hub={WORD_SALAD_GAME_ID!r} salads={len(planned)} "
        f"tg +{created_tg}/~{updated_tg} link +{created_link}/~{updated_link} "
        f"task +{created_task}/~{updated_task} publish_start={pub}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Import daily salads from Google Sheets")
    parser.add_argument("--csv", metavar="PATH", help="локальный CSV вместо Google Sheets")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", help="номера, например 1-5,12")
    parser.add_argument(
        "--publish-start",
        default=WORD_SALAD_DEFAULT_PUBLISH_START,
        help="ISO datetime МСК для салата №1",
    )
    parser.add_argument("--skip-publish-start", action="store_true")
    parser.add_argument("--sheet-url", default=SHEET_CSV_URL)
    args = parser.parse_args()
    return run(
        dry_run=args.dry_run,
        csv_path=args.csv,
        publish_start=args.publish_start,
        sheet_url=args.sheet_url,
        only=_parse_only_spec(args.only),
        skip_publish_start=args.skip_publish_start,
    )


if __name__ == "__main__":
    raise SystemExit(main())
