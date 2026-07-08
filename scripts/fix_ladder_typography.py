#!/usr/bin/env python3
"""
Типографика текстов лесенок:
- « - » → « — » (только дефис между пробелами)
- кавычки " " " " → « »
- «...» / "..." / "..." → «...»

Использование::

    ../venv/interoves_django/bin/python scripts/fix_ladder_typography.py --dry-run
    ./scripts/eb_run.sh scripts/fix_ladder_typography.py --dry-run
    ./scripts/eb_run.sh scripts/fix_ladder_typography.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import django

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interoves_django.settings")
django.setup()

from games.ladder_daily import LADDER_GAME_ID  # noqa: E402
from games.models import Game, GameTaskGroup, Hint, Task  # noqa: E402
from games.raddle import validate_raddle_checker_data  # noqa: E402


def fix_spaced_hyphen(text: str) -> str:
    return text.replace(" - ", " — ")


def fix_quotes(text: str) -> str:
    """ASCII и типографские кавычки → чередующиеся « »; уже стоящие « » сохраняем."""
    if not text:
        return text
    out: list[str] = []
    open_next = True
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "«":
            out.append(ch)
            i += 1
            continue
        if ch == "»":
            out.append(ch)
            i += 1
            continue
        if ch in "“„":
            out.append("«")
            open_next = False
            i += 1
            continue
        if ch == "”":
            out.append("»")
            open_next = True
            i += 1
            continue
        if ch == '"':
            out.append("«" if open_next else "»")
            open_next = not open_next
            i += 1
            continue
        if ch == "'":
            if (
                i > 0
                and text[i - 1].isalpha()
                and i + 1 < len(text)
                and text[i + 1].isalpha()
            ):
                out.append(ch)
                i += 1
                continue
            out.append("«" if open_next else "»")
            open_next = not open_next
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def fix_ellipsis_in_quotes(text: str) -> str:
    """«...» / "..." / "..." → «...»."""
    return re.sub(
        r'[«“„"][.…]{2,3}[»”"]',
        "«...»",
        text,
    )


def fix_typography(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    t = fix_spaced_hyphen(text)
    t = fix_quotes(t)
    t = fix_ellipsis_in_quotes(t)
    return t


def _fix_checker_data(raw: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    if not (raw or "").strip():
        return raw, changes
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return raw, changes
    if not isinstance(data, dict):
        return raw, changes

    modified = False
    hints = data.get("hints")
    if isinstance(hints, list):
        new_hints = []
        for i, h in enumerate(hints):
            if not isinstance(h, str):
                new_hints.append(h)
                continue
            fixed = fix_typography(h)
            if fixed != h:
                changes.append(f"  hints[{i}]: {h!r} → {fixed!r}")
                modified = True
            new_hints.append(fixed)
        data["hints"] = new_hints

    words = data.get("words")
    if isinstance(words, list):
        new_words = []
        for i, w in enumerate(words):
            if not isinstance(w, str):
                new_words.append(w)
                continue
            fixed = fix_typography(w)
            if fixed != w:
                changes.append(f"  words[{i}]: {w!r} → {fixed!r}")
                modified = True
            new_words.append(fixed)
        data["words"] = new_words

    if not modified:
        return raw, changes
    new_raw = json.dumps(data, ensure_ascii=False)
    err = validate_raddle_checker_data(new_raw)
    if err:
        raise ValueError(f"validation failed after fix: {err}")
    return new_raw, changes


def iter_ladder_tasks():
    hub = Game.objects.filter(pk=LADDER_GAME_ID).first()
    if not hub:
        return
    links = (
        GameTaskGroup.objects.filter(game=hub)
        .select_related("task_group")
        .order_by("number")
    )
    for link in links:
        for task in link.task_group.tasks.visible().filter(task_type="raddle"):
            yield link.number, task


def run(*, dry_run: bool) -> int:
    if not Game.objects.filter(pk=LADDER_GAME_ID).exists():
        print(f"Game {LADDER_GAME_ID!r} not found.", file=sys.stderr)
        return 1

    changed_ladders = 0
    changed_tasks = 0
    changed_hints = 0

    for ladder_num, task in iter_ladder_tasks():
        task_changes: list[str] = []
        old_cd = task.checker_data or ""
        old_text = task.text or ""

        new_cd, cd_changes = _fix_checker_data(old_cd)
        task_changes.extend(cd_changes)

        new_text = fix_typography(old_text)
        if new_text != old_text:
            task_changes.append(f"  task.text: {old_text!r} → {new_text!r}")

        hint_updates: list[tuple[Hint, str]] = []
        for h in Hint.objects.filter(task=task):
            old_h = h.text or ""
            fixed = fix_typography(old_h)
            if fixed != old_h:
                hint_updates.append((h, fixed))
                task_changes.append(f"  Hint #{h.number}: {old_h!r} → {fixed!r}")

        if not task_changes:
            continue

        changed_ladders += 1
        print(f"Ladder #{ladder_num} task id={task.id}:")
        for line in task_changes:
            print(line)

        if dry_run:
            continue

        update_fields: list[str] = []
        if new_cd != old_cd:
            task.checker_data = new_cd
            update_fields.append("checker_data")
        if new_text != old_text:
            task.text = new_text
            update_fields.append("text")
        if update_fields:
            task.save(update_fields=update_fields)
            changed_tasks += 1

        for h, fixed in hint_updates:
            h.text = fixed
            h.save(update_fields=["text"])
            changed_hints += 1

    mode = "DRY RUN" if dry_run else "APPLIED"
    print(
        f"{mode}: {changed_ladders} ladder(s) with changes"
        + (f", {changed_tasks} task(s), {changed_hints} hint(s) updated" if not dry_run else "")
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(dry_run=args.dry_run))
