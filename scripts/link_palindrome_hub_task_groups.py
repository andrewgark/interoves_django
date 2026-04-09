#!/usr/bin/env python3
"""
Дублирует «палиндромные» круги из десяточек (project main) в игру-хаб «Палиндромы»
(game id ``palindromes``). Старые привязки к играм des* **не удаляются**.

Номер в хабе: из ``des117`` → ``117``; если из одной игры несколько кругов —
``NNN * 1000 + номер_круга`` (например ``137001``, ``137003``). Название круга
копируется из исходного ``GameTaskGroup``.

Использование (из корня репозитория, venv см. ``agents/AGENTS.md``)::

    ../venv/interoves_django/bin/python scripts/link_palindrome_hub_task_groups.py
    ../venv/interoves_django/bin/python scripts/link_palindrome_hub_task_groups.py --dry-run

Через SSM-туннель на prod RDS (см. ``agents/aws-eb.md``)::

    ./scripts/with_rds.sh ../venv/interoves_django/bin/python scripts/link_palindrome_hub_task_groups.py
    ./scripts/with_rds.sh ../venv/interoves_django/bin/python scripts/link_palindrome_hub_task_groups.py --dry-run

Для похожих сценариев скопируйте файл, поменяйте ``HUB_GAME_ID``, фильтр источника
и функцию расчёта ``number``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict

import django

# Repo root = parent of scripts/
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interoves_django.settings")
django.setup()

from django.db.models import Q  # noqa: E402

from games.models import Game, GameTaskGroup  # noqa: E402

# --- настройте под другой хаб / другой отбор ---
HUB_GAME_ID = "palindromes"
SOURCE_PROJECT_ID = "main"


def _source_rows():
    return (
        GameTaskGroup.objects.filter(game__project_id=SOURCE_PROJECT_ID)
        .filter(Q(name__icontains="Палиндром") | Q(task_group__label__icontains="Палиндром"))
        .select_related("game")
        .order_by("game_id", "number", "pk")
    )


def _compute_numbers(by_source: dict[str, list[GameTaskGroup]]) -> list[tuple[GameTaskGroup, int]]:
    des_re = re.compile(r"^des(\d+)$")
    out: list[tuple[GameTaskGroup, int]] = []
    for _source_id, rows in by_source.items():
        n = len(rows)
        for gtg in rows:
            m = des_re.match(gtg.game_id)
            if not m:
                continue
            base = int(m.group(1))
            if n == 1:
                new_number = base
            else:
                new_number = base * 1000 + int(gtg.number)
            out.append((gtg, new_number))
    return out


def run(*, dry_run: bool) -> int:
    try:
        hub = Game.objects.get(pk=HUB_GAME_ID)
    except Game.DoesNotExist:
        print(f"Game {HUB_GAME_ID!r} not found.", file=sys.stderr)
        return 1

    des_re = re.compile(r"^des(\d+)$")
    by_source: dict[str, list[GameTaskGroup]] = defaultdict(list)
    for gtg in _source_rows():
        if des_re.match(gtg.game_id):
            by_source[gtg.game_id].append(gtg)

    planned = _compute_numbers(by_source)
    if dry_run:
        for gtg, new_number in planned:
            print(
                f"would link task_group_id={gtg.task_group_id} "
                f"from {gtg.game_id!r} -> hub number={new_number} name={gtg.name!r}"
            )
        print(f"DRY RUN: {len(planned)} row(s)")
        return 0

    created = updated = 0
    for gtg, new_number in planned:
        _obj, was_created = GameTaskGroup.objects.update_or_create(
            game=hub,
            task_group_id=gtg.task_group_id,
            defaults={
                "number": new_number,
                "name": gtg.name,
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1

    print(f"hub={HUB_GAME_ID!r} created={created} updated={updated} total={created + updated}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="только вывести, что было бы сделано, без записи в БД",
    )
    args = p.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
