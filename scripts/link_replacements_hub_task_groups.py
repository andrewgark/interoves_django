#!/usr/bin/env python3
"""
Дублирует круги «Замены» из десяточек (project main) в игру-хаб «Замены»
(game id ``replacements``). Старые привязки к играм des* **не удаляются**.

Номер в хабе: из ``des117`` → ``117``; если из одной игры несколько кругов —
``NNN * 1000 + номер_круга`` (как у палиндромов). Уже привязанные к хабу
task_group пропускаются.

Использование (из корня репозитория, venv см. ``agents/AGENTS.md``)::

    ../venv/interoves_django/bin/python scripts/link_replacements_hub_task_groups.py
    ../venv/interoves_django/bin/python scripts/link_replacements_hub_task_groups.py --dry-run

На проде через EB::

    ./scripts/eb_run.sh manage.py shell < scripts/_run_link_replacements.py

или одноразово::

    ./scripts/eb_run.sh scripts/link_replacements_hub_task_groups.py
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict

import django

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interoves_django.settings")
django.setup()

from django.db.models import Q  # noqa: E402

from games.models import Game, GameTaskGroup  # noqa: E402

HUB_GAME_ID = "replacements"
SOURCE_PROJECT_ID = "main"
TITLE_SUBSTRING = "Замены"


def _source_rows():
    return (
        GameTaskGroup.objects.filter(game__project_id=SOURCE_PROJECT_ID)
        .filter(
            Q(name__icontains=TITLE_SUBSTRING)
            | Q(task_group__label__icontains=TITLE_SUBSTRING)
        )
        .select_related("game")
        .order_by("game_id", "number", "pk")
    )


def _hub_task_group_ids(hub: Game) -> set[int]:
    return set(
        GameTaskGroup.objects.filter(game=hub).values_list("task_group_id", flat=True)
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

    already = _hub_task_group_ids(hub)
    des_re = re.compile(r"^des(\d+)$")
    by_source: dict[str, list[GameTaskGroup]] = defaultdict(list)
    skipped_linked = skipped_non_des = 0
    for gtg in _source_rows():
        if gtg.task_group_id in already:
            skipped_linked += 1
            continue
        if not des_re.match(gtg.game_id):
            skipped_non_des += 1
            print(
                f"skip non-des game_id={gtg.game_id!r} "
                f"task_group_id={gtg.task_group_id} name={gtg.name!r}",
                file=sys.stderr,
            )
            continue
        by_source[gtg.game_id].append(gtg)

    planned = _compute_numbers(by_source)
    if dry_run:
        for gtg, new_number in planned:
            print(
                f"would link task_group_id={gtg.task_group_id} "
                f"from {gtg.game_id!r} circle={gtg.number} "
                f"-> hub number={new_number} name={gtg.name!r}"
            )
        print(
            f"DRY RUN: {len(planned)} row(s); "
            f"skipped already in hub={skipped_linked}, non-des={skipped_non_des}"
        )
        return 0

    created = updated = 0
    for gtg, new_number in planned:
        _obj, was_created = GameTaskGroup.objects.update_or_create(
            game=hub,
            task_group_id=gtg.task_group_id,
            defaults={
                "number": str(new_number),
                "name": gtg.name,
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1

    print(
        f"hub={HUB_GAME_ID!r} created={created} updated={updated} "
        f"total={created + updated}; "
        f"skipped already in hub={skipped_linked}, non-des={skipped_non_des}"
    )
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
