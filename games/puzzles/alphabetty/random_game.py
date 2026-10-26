"""Creation of permanent, deduplicated random Alphabetty games."""

from __future__ import annotations

import random

from django.db import IntegrityError, transaction

from games.alphabetty.core import normalize_word
from games.alphabetty.dicts import get_answer_pool
from games.models import CheckerType, GameTaskGroup, RandomAlphabettyGame, Task, TaskGroup
from games.placement_share import allocate_share_hash


class EmptyRandomAlphabettyDictionary(LookupError):
    """The configured answer dictionary has no usable words."""


class RandomAlphabettyDictionaryExhausted(LookupError):
    """Every usable dictionary word already has a permanent game."""


def get_or_create_random_game(*, game):
    """Pick one admin-approved answer and return its permanent game/link.

    The unique word and hash columns make repeated clicks and concurrent clicks
    converge on the same game instead of creating duplicate statistics rows.
    """
    pool = tuple(dict.fromkeys(
        normalize_word(word) for word in get_answer_pool() if normalize_word(word)
    ))
    if not pool:
        raise EmptyRandomAlphabettyDictionary('Alphabetty answer dictionary is empty')

    # A bounded retry handles two requests selecting the same last unused word.
    for _ in range(8):
        used_words = set(RandomAlphabettyGame.objects.values_list('word', flat=True))
        available_words = [word for word in pool if word not in used_words]
        if not available_words:
            # A one-word dictionary is still idempotent: repeated clicks must
            # resolve to its canonical permanent game.
            if len(pool) == 1:
                existing = RandomAlphabettyGame.objects.filter(
                    word=pool[0],
                ).select_related('task_group').first()
                if existing is not None:
                    return existing
            raise RandomAlphabettyDictionaryExhausted(
                'Every Alphabetty answer already has a random game',
            )
        word = random.choice(available_words)
        existing = RandomAlphabettyGame.objects.filter(word=word).select_related('task_group').first()
        if existing is not None:
            return existing
        try:
            with transaction.atomic():
                existing = RandomAlphabettyGame.objects.select_for_update().filter(word=word).first()
                if existing is not None:
                    return existing
                checker = CheckerType.objects.get(pk='alphabetty')
                task_group = TaskGroup.objects.create(
                    label=f'alphabetty:random:{word}',
                    checker=checker,
                    points=1,
                    max_attempts=3,
                )
                Task.objects.create(
                    task_group=task_group,
                    number='1',
                    task_type='alphabetty',
                    checker=checker,
                    checker_data=word,
                    answer=word,
                    text='',
                    tags={},
                    points=1,
                    is_removed=False,
                )
                share_hash = allocate_share_hash()
                RandomAlphabettyGame.objects.create(
                    word=word,
                    share_hash=share_hash,
                    task_group=task_group,
                )
                GameTaskGroup.objects.create(
                    game=game,
                    task_group=task_group,
                    number=share_hash,
                    name=f'Случайная алфавитка #{share_hash}',
                    share_hash=share_hash,
                )
                return RandomAlphabettyGame.objects.select_related('task_group').get(word=word)
        except IntegrityError:
            # Another request won the unique-word race; use its canonical row.
            existing = RandomAlphabettyGame.objects.filter(word=word).select_related('task_group').first()
            if existing is not None:
                return existing
            raise
    raise LookupError('Не удалось создать случайную алфавитку')
