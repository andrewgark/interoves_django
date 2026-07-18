#!/usr/bin/env python3
"""
Сдвиг номеров лесенок в разделе ``ladder`` (GameTaskGroup.number и связанные label/name/text).

Пример — сдвинуть будущие 12–39 на +4 (→ 16–43), чтобы освободить слоты 12–15::

    ../venv/interoves_django/bin/python scripts/shift_ladder_numbers.py --from 12 --to 39 --by 4 --dry-run
    ../venv/interoves_django/bin/python scripts/shift_ladder_numbers.py --from 12 --to 39 --by 4

На проде::

    ./scripts/eb_run.sh scripts/shift_ladder_numbers.py --from 12 --to 39 --by 4
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import django

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interoves_django.settings")
django.setup()

from django.db import transaction  # noqa: E402

from games.ladder_daily import LADDER_GAME_ID  # noqa: E402
from games.models import Game, GameTaskGroup, Task  # noqa: E402

_TITLE_RE = re.compile(r"^Лесенка\s*#\s*(\d+)\s*$", re.IGNORECASE)


def _apply_title(old: str, new_num: int) -> str:
    if not old:
        return old
    if _TITLE_RE.match(old.strip()):
        return f"Лесенка #{new_num}"
    return old


def run(*, from_num: int, to_num: int, by: int, dry_run: bool) -> int:
    if from_num > to_num:
        print("--from must be <= --to", file=sys.stderr)
        return 1
    if by == 0:
        print("--by must be non-zero", file=sys.stderr)
        return 1

    try:
        hub = Game.objects.get(pk=LADDER_GAME_ID)
    except Game.DoesNotExist:
        print(f"Game {LADDER_GAME_ID!r} not found", file=sys.stderr)
        return 1

    nums = list(range(from_num, to_num + 1))
    # При положительном сдвиге — с высоких номеров, чтобы не бить unique(game, number).
    order = sorted(nums, reverse=(by > 0))

    planned = []
    for old in order:
        link = (
            GameTaskGroup.objects.filter(game=hub, number=str(old))
            .select_related("task_group")
            .first()
        )
        if not link:
            print(f"skip missing #{old}")
            continue
        new = old + by
        conflict = GameTaskGroup.objects.filter(game=hub, number=str(new)).exclude(pk=link.pk).exists()
        if conflict and str(new) not in {str(n) for n in nums}:
            # Конфликт вне сдвигаемого диапазона — опасно.
            print(f"conflict: #{old} → #{new} already occupied outside range", file=sys.stderr)
            return 1
        planned.append((old, new, link))

    if dry_run:
        for old, new, link in planned:
            print(f"would rename #{old} → #{new} (gtg={link.pk}, tg={link.task_group_id})")
        print(f"DRY RUN: {len(planned)} ladder(s)")
        return 0

    with transaction.atomic():
        # Двухфазно через временные номера, если внутри диапазона возможны коллизии
        # при одношаговом update (на практике high→low при +by достаточно; temp надёжнее).
        temp_base = 10_000
        for i, (old, new, link) in enumerate(planned):
            temp = str(temp_base + i)
            tg = link.task_group
            link.number = temp
            link.name = _apply_title(link.name or "", new)
            link.save(update_fields=["number", "name"])
            if (tg.label or "").startswith("ladder:"):
                tg.label = f"ladder:{new}"
                tg.save(update_fields=["label"])
            task = Task.objects.filter(task_group=tg, number="1").first()
            if task and task.text:
                new_text = _apply_title(task.text, new)
                if new_text != task.text:
                    task.text = new_text
                    task.save(update_fields=["text"])

        for i, (old, new, link) in enumerate(planned):
            link.number = str(new)
            link.save(update_fields=["number"])
            print(f"#{old} → #{new}")

    print(f"shifted {len(planned)} ladder(s) by {by:+d}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--from", dest="from_num", type=int, required=True)
    p.add_argument("--to", dest="to_num", type=int, required=True)
    p.add_argument("--by", type=int, required=True, help="сдвиг номера (например 4)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    return run(
        from_num=args.from_num,
        to_num=args.to_num,
        by=args.by,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
