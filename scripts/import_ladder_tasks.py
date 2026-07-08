#!/usr/bin/env python3
"""
Импорт лесенок из Google Sheets в раздел «Лесенка» (game id ``ladder``).

Таблица: https://docs.google.com/spreadsheets/d/1Ru3idvJOss9n70HpAUl18AC9XLEHY8cZfGnrGqsLphQ/

Колонки: # (номер лесенки), ## (позиция слова), Слово, N (длина), Подсказка.

Использование (из корня репозитория)::

    ../venv/interoves_django/bin/python scripts/import_ladder_tasks.py
    ../venv/interoves_django/bin/python scripts/import_ladder_tasks.py --dry-run
    ../venv/interoves_django/bin/python scripts/import_ladder_tasks.py --csv /path/to.csv

На проде::

    ./scripts/eb_run.sh scripts/import_ladder_tasks.py
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict

import django

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interoves_django.settings")
django.setup()

from games.ladder_daily import LADDER_GAME_ID, LADDER_PUBLISH_START_TAG  # noqa: E402
from games.models import CheckerType, Game, GameTaskGroup, Task, TaskGroup  # noqa: E402
from games.raddle import ensure_raddle_assist_hints, validate_raddle_checker_data  # noqa: E402

SHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1Ru3idvJOss9n70HpAUl18AC9XLEHY8cZfGnrGqsLphQ/export?format=csv&gid=0"
)
DEFAULT_PUBLISH_START = "2026-07-08T00:00:00+03:00"


def _normalize_length(raw: str):
    s = (raw or "").strip()
    if not s:
        return None
    if re.match(r"^\d+$", s):
        return int(s)
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.match(r"^(\d+)\s+(\d+)$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return s


def _fetch_csv(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read().decode("utf-8-sig")


def _parse_rows(csv_text: str) -> dict[int, list[dict]]:
    reader = csv.reader(io.StringIO(csv_text))
    header = next(reader, None)
    if not header:
        return {}
    ladders: dict[int, list[dict]] = defaultdict(list)
    for row in reader:
        if not row or not row[0].strip():
            continue
        try:
            ladder_num = int(row[0].strip())
            word_idx = int(row[1].strip())
        except (ValueError, IndexError):
            continue
        word = (row[2] if len(row) > 2 else "").strip().upper()
        if not word:
            continue
        length_raw = row[3] if len(row) > 3 else ""
        hint = (row[4] if len(row) > 4 else "").strip()
        length = _normalize_length(length_raw)
        ladders[ladder_num].append({
            "idx": word_idx,
            "word": word,
            "length": length,
            "hint": hint,
        })
    return ladders


def _build_checker_data(words: list[dict]) -> dict:
    # Подсказка на строке слова N связывает слова N → N+1 (как в таблице и raddle.quest).
    # У последнего слова подсказки нет.
    words_sorted = sorted(words, key=lambda w: w["idx"])
    word_list = [w["word"] for w in words_sorted]
    lengths = []
    for w in words_sorted:
        if w["length"] is None:
            raise ValueError(f"missing length for word {w['word']!r}")
        lengths.append(w["length"])
    hints = [(w.get("hint") or "").strip() for w in words_sorted[:-1]]
    return {
        "lengths": lengths,
        "hints": hints,
        "words": word_list,
        "raddle_assist": {"enabled": True, "fractions": [1, 0.5, 0]},
    }


def _validate_ladder(ladder_num: int, data: dict) -> None:
    raw = json.dumps(data, ensure_ascii=False)
    err = validate_raddle_checker_data(raw)
    if err:
        raise ValueError(f"ladder #{ladder_num}: {err}")


def run(
    *,
    dry_run: bool,
    csv_path: str | None,
    publish_start: str,
    sheet_url: str,
) -> int:
    try:
        hub = Game.objects.get(pk=LADDER_GAME_ID)
    except Game.DoesNotExist:
        print(f"Game {LADDER_GAME_ID!r} not found — run migrations first.", file=sys.stderr)
        return 1

    if csv_path:
        csv_text = open(csv_path, encoding="utf-8-sig").read()
    else:
        print(f"Fetching {sheet_url} …")
        csv_text = _fetch_csv(sheet_url)

    ladders = _parse_rows(csv_text)
    if not ladders:
        print("No ladders parsed.", file=sys.stderr)
        return 1

    checker = CheckerType.objects.get(id="raddle")
    planned = []
    for num in sorted(ladders):
        data = _build_checker_data(ladders[num])
        _validate_ladder(num, data)
        planned.append((num, data))

    if dry_run:
        for num, data in planned:
            print(f"would import ladder #{num}: {len(data['words'])} words")
        print(f"DRY RUN: {len(planned)} ladder(s)")
        return 0

    tags = dict(hub.tags or {})
    tags[LADDER_PUBLISH_START_TAG] = publish_start
    hub.tags = tags
    hub.save(update_fields=["tags"])

    created_tg = updated_tg = created_link = updated_link = created_task = updated_task = 0
    for num, data in planned:
        number = str(num)
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
                label=f"ladder:{number}",
                checker=checker,
                points=1,
                max_attempts=3,
            )
            created_tg += 1

        checker_data = json.dumps(data, ensure_ascii=False)
        answer = "\n".join(data["words"])
        if not task_group.max_attempts:
            task_group.max_attempts = 3
            task_group.save(update_fields=["max_attempts"])

        task, task_created = Task.objects.update_or_create(
            task_group=task_group,
            number="1",
            defaults={
                "task_type": "raddle",
                "checker": checker,
                "checker_data": checker_data,
                "answer": answer,
                "text": f"Лесенка #{num}",
                "points": 1,
                "max_attempts": None,
                "is_removed": False,
            },
        )
        if task_created:
            created_task += 1
        else:
            updated_task += 1
        ensure_raddle_assist_hints(task)

        link, link_created = GameTaskGroup.objects.update_or_create(
            game=hub,
            number=number,
            defaults={
                "task_group": task_group,
                "name": f"Лесенка #{num}",
            },
        )
        if link_created:
            created_link += 1
        else:
            updated_link += 1

    print(
        f"hub={LADDER_GAME_ID!r} ladders={len(planned)} "
        f"publish_start={publish_start!r}; "
        f"task_groups created={created_tg} updated={updated_tg}; "
        f"tasks created={created_task} updated={updated_task}; "
        f"links created={created_link} updated={updated_link}"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--csv", metavar="PATH", help="локальный CSV вместо Google Sheets")
    p.add_argument(
        "--publish-start",
        default=DEFAULT_PUBLISH_START,
        help=f"полночь МСК первой лесенки (default: {DEFAULT_PUBLISH_START})",
    )
    p.add_argument("--sheet-url", default=SHEET_CSV_URL)
    args = p.parse_args()
    return run(
        dry_run=args.dry_run,
        csv_path=args.csv,
        publish_start=args.publish_start,
        sheet_url=args.sheet_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())
