"""Предложения слов в словарь Алфавитки."""

from __future__ import annotations

import re
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from games.alphabetty.core import is_valid_guess, normalize_word
from games.alphabetty.dicts import invalidate_approved_extras
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


@transaction.atomic
def suggest_word(
    word: str,
    *,
    user=None,
    anon_key: str | None = None,
) -> dict[str, Any]:
    """Создать или обновить pending-предложение.

    status ответа: ok | already_pending | already_in_dict | error
    """
    n, err = _validate_candidate(word)
    if err:
        return {'status': 'error', 'error': err, 'word': n or ''}

    if is_valid_guess(n):
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
        return {
            'status': 'ok',
            'word': n,
            'suggestion_id': obj.pk,
            'suggest_count': obj.suggest_count,
            'message': 'Спасибо! Предложение отправлено на модерацию',
        }

    if existing.status == AlphabettyDictSuggestion.STATUS_APPROVED:
        invalidate_approved_extras()
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
        return {
            'status': 'already_pending',
            'word': n,
            'suggestion_id': existing.pk,
            'suggest_count': existing.suggest_count,
            'message': 'Это слово уже предложено — учли ваш голос',
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
    return {
        'status': 'ok',
        'word': n,
        'suggestion_id': existing.pk,
        'suggest_count': existing.suggest_count,
        'message': 'Спасибо! Предложение отправлено на модерацию',
    }


@transaction.atomic
def approve_suggestions(queryset) -> int:
    now = timezone.now()
    ids = list(queryset.values_list('pk', flat=True))
    updated = AlphabettyDictSuggestion.objects.filter(pk__in=ids).update(
        status=AlphabettyDictSuggestion.STATUS_APPROVED,
        reviewed_at=now,
    )
    invalidate_approved_extras()
    return updated


@transaction.atomic
def reject_suggestions(queryset) -> int:
    now = timezone.now()
    ids = list(queryset.values_list('pk', flat=True))
    updated = AlphabettyDictSuggestion.objects.filter(pk__in=ids).update(
        status=AlphabettyDictSuggestion.STATUS_REJECTED,
        reviewed_at=now,
    )
    invalidate_approved_extras()
    return updated
