"""Предложения слов в словарь Алфавитки."""

from __future__ import annotations

import re
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from games.alphabetty.core import is_valid_guess, normalize_word
from games.alphabetty.dicts import add_personal_dict_word, invalidate_dict_caches
from games.models import AlphabettyDictSuggestion

_WORD_RE = re.compile(r'^[А-Я]+$')


def _validate_candidate(word: str) -> tuple[Optional[str], Optional[str]]:
    """Вернуть (normalized, error) — error если слово нельзя предложить."""
    n = normalize_word(word)
    if not n:
        return None, 'Пустое слово'
    if len(n) > 40:
        return None, 'Слишком длинное слово'
    if not _WORD_RE.match(n):
        return None, 'Только русские буквы'
    return n, None


def _add_to_personal(word: str, *, user=None, anon_key: str | None = None) -> None:
    add_personal_dict_word(word, user=user, anon_key=anon_key)


@transaction.atomic
def suggest_word(
    word: str,
    *,
    user=None,
    anon_key: str | None = None,
) -> dict[str, Any]:
    """Создать или обновить pending-предложение и сразу добавить в личный словарь.

    status ответа: ok | already_pending | already_in_dict | error
    """
    n, err = _validate_candidate(word)
    if err:
        return {'status': 'error', 'error': err, 'word': n or ''}

    # Глобальный словарь (без личного) — иначе «уже в словаре» для всех.
    if is_valid_guess(n):
        _add_to_personal(n, user=user, anon_key=anon_key)
        return {
            'status': 'already_in_dict',
            'error': 'Слово уже в словаре',
            'word': n,
        }

    existing = (
        AlphabettyDictSuggestion.objects.select_for_update()
        .filter(word=n)
        .first()
    )
    if existing is None:
        obj = AlphabettyDictSuggestion.objects.create(
            word=n,
            status=AlphabettyDictSuggestion.STATUS_PENDING,
            suggest_count=1,
            user=user,
            anon_key=anon_key if user is None else None,
        )
        _add_to_personal(n, user=user, anon_key=anon_key)
        return {
            'status': 'ok',
            'word': n,
            'suggestion_id': obj.pk,
            'suggest_count': obj.suggest_count,
            'personal': True,
            'message': f'Слова {n} нет в словаре, но мы добавили его для вас',
        }

    if existing.status in AlphabettyDictSuggestion.STATUSES_VALID:
        invalidate_dict_caches()
        _add_to_personal(n, user=user, anon_key=anon_key)
        return {
            'status': 'already_in_dict',
            'error': 'Слово уже в словаре',
            'word': n,
        }

    if existing.status == AlphabettyDictSuggestion.STATUS_PENDING:
        existing.suggest_count += 1
        if user and existing.user_id is None:
            existing.user = user
        existing.save(update_fields=['suggest_count', 'user', 'updated_at'])
        _add_to_personal(n, user=user, anon_key=anon_key)
        return {
            'status': 'already_pending',
            'word': n,
            'suggestion_id': existing.pk,
            'suggest_count': existing.suggest_count,
            'personal': True,
            'message': f'Слова {n} нет в словаре, но мы добавили его для вас',
        }

    # Rejected → снова в очередь
    existing.status = AlphabettyDictSuggestion.STATUS_PENDING
    existing.suggest_count += 1
    existing.reviewed_at = None
    if user and existing.user_id is None:
        existing.user = user
    existing.save(update_fields=[
        'status', 'suggest_count', 'reviewed_at', 'user', 'updated_at',
    ])
    _add_to_personal(n, user=user, anon_key=anon_key)
    return {
        'status': 'ok',
        'word': n,
        'suggestion_id': existing.pk,
        'suggest_count': existing.suggest_count,
        'personal': True,
        'message': f'Слова {n} нет в словаре, но мы добавили его для вас',
    }


def propose_alphabetty_word(
    word: str,
    *,
    task_group=None,
    task=None,
    user=None,
    anon_key: str | None = None,
) -> dict[str, Any]:
    """Совместимое имя для старого вызова из Алфавитки.

    Новая схема не имеет отдельного round-only словаря: редкое слово просто
    попадает в личный словарь и на обычную модерацию через suggest_word().
    """
    return suggest_word(word, user=user, anon_key=anon_key)


@transaction.atomic
def approve_suggestions(queryset) -> int:
    """Одобрить как валидную отгадку (не трогает ApprovedAnswer)."""
    now = timezone.now()
    ids = list(queryset.values_list('pk', flat=True))
    updated = (
        AlphabettyDictSuggestion.objects.filter(pk__in=ids)
        .exclude(status=AlphabettyDictSuggestion.STATUS_APPROVED_ANSWER)
        .update(
            status=AlphabettyDictSuggestion.STATUS_APPROVED,
            reviewed_at=now,
        )
    )
    # Уже ApprovedAnswer — только обновить reviewed_at
    AlphabettyDictSuggestion.objects.filter(
        pk__in=ids,
        status=AlphabettyDictSuggestion.STATUS_APPROVED_ANSWER,
    ).update(reviewed_at=now)
    invalidate_dict_caches()
    return updated


@transaction.atomic
def approve_suggestions_for_answer(queryset) -> int:
    """Одобрить и для словаря, и для пула загадывания."""
    now = timezone.now()
    ids = list(queryset.values_list('pk', flat=True))
    updated = AlphabettyDictSuggestion.objects.filter(pk__in=ids).update(
        status=AlphabettyDictSuggestion.STATUS_APPROVED_ANSWER,
        reviewed_at=now,
    )
    invalidate_dict_caches()
    return updated


@transaction.atomic
def reject_suggestions(queryset) -> int:
    now = timezone.now()
    ids = list(queryset.values_list('pk', flat=True))
    updated = AlphabettyDictSuggestion.objects.filter(pk__in=ids).update(
        status=AlphabettyDictSuggestion.STATUS_REJECTED,
        reviewed_at=now,
    )
    invalidate_dict_caches()
    return updated
