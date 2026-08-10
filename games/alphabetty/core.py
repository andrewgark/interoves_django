"""Нормализация, сравнение и префикс-подсказки Алфавитки."""

from __future__ import annotations

import random
from typing import Any, Optional

# Полный алфавит (ввод допускает Ё). При сравнении Ё ≡ Е.
RU_ALPHABET = 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ'
# Порядок для сравнения и префикс-подсказок: без отдельной позиции Ё.
_RU_ORDER = 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ'
_ALPH_INDEX = {ch: i for i, ch in enumerate(_RU_ORDER)}
_ALPH_INDEX['Ё'] = _ALPH_INDEX['Е']


def normalize_word(word: str | None) -> str:
    """UPPERCASE; Ё → Е (в словаре буквы Ё нет, ввод с ё всё равно принимаем)."""
    if not word:
        return ''
    return str(word).strip().upper().replace('Ё', 'Е')


def alphabet_key(word: str) -> tuple:
    """Ключ сортировки после normalize (Ё уже как Е)."""
    n = normalize_word(word)
    return tuple(_ALPH_INDEX.get(ch, 1000 + ord(ch)) for ch in n)


def compare_words(a: str, b: str) -> int:
    """-1 если a < b, 0 если равны, 1 если a > b (по алфавиту Алфавитки)."""
    ka, kb = alphabet_key(a), alphabet_key(b)
    if ka < kb:
        return -1
    if ka > kb:
        return 1
    return 0


def is_valid_guess(
    word: str,
    valid: frozenset[str] | None = None,
    *,
    user=None,
    anon_key: str | None = None,
    task=None,
) -> bool:
    from games.alphabetty.dicts import (
        get_valid_set,
        is_approved_dict_word,
        is_personal_dict_word,
    )

    n = normalize_word(word)
    if not n:
        return False
    if valid is not None:
        return n in valid
    if n in get_valid_set():
        return True
    if is_approved_dict_word(n):
        return True
    if task is not None:
        try:
            secret = normalize_word(getattr(task, 'answer', None))
        except Exception:
            secret = ''
        if secret and n == secret:
            return True
    if user is not None or anon_key:
        return is_personal_dict_word(n, user=user, anon_key=anon_key)
    return False


def guess_status(guess: str, secret: str) -> str:
    """earlier | later | correct (guess уже нормализован / сравнивается через normalize)."""
    g, s = normalize_word(guess), normalize_word(secret)
    c = compare_words(g, s)
    if c == 0:
        return 'correct'
    if c < 0:
        return 'earlier'
    return 'later'


def max_word_length(valid: frozenset[str] | None = None) -> int:
    from games.alphabetty.dicts import get_valid_set

    pool = valid if valid is not None else get_valid_set()
    return max((len(w) for w in pool), default=20)


def pick_answer_words(
    n: int,
    *,
    exclude: set[str] | frozenset[str] | None = None,
    rng: random.Random | None = None,
) -> list[str]:
    """Случайные уникальные слова из answer-пула, не входящие в exclude."""
    from games.alphabetty.dicts import get_answer_pool

    if n <= 0:
        return []
    excl = {normalize_word(w) for w in (exclude or set())}
    candidates = [w for w in get_answer_pool() if w not in excl]
    if not candidates:
        return []
    r = rng or random.Random()
    k = min(n, len(candidates))
    return r.sample(candidates, k)


def known_prefix(
    lo: Optional[str],
    hi: Optional[str],
    *,
    hint_prefix: str = '',
) -> str:
    """Буквы, о которых точно известно, что ответ начинается с них.

    Это LCP границ lo/hi и/или буквы, раскрытые подсказками (hint_prefix).
    """
    lo_n = normalize_word(lo) if lo else ''
    hi_n = normalize_word(hi) if hi else ''
    bound_prefix = ''
    if lo_n and hi_n:
        lcp = 0
        limit = min(len(lo_n), len(hi_n))
        while lcp < limit and lo_n[lcp] == hi_n[lcp]:
            lcp += 1
        bound_prefix = lo_n[:lcp]
    elif lo_n and lo_n[0] == _RU_ORDER[-1]:
        # После слова на последнюю букву алфавита ответ тоже начинается с нее.
        bound_prefix = lo_n[:1]
    elif hi_n and hi_n[0] == _RU_ORDER[0]:
        # Перед словом на первую букву алфавита ответ тоже начинается с нее.
        bound_prefix = hi_n[:1]
    hp = normalize_word(hint_prefix)
    if not hp:
        return bound_prefix
    if bound_prefix and not hp.startswith(bound_prefix):
        return bound_prefix
    return hp if len(hp) >= len(bound_prefix) else bound_prefix


def _letter_range(start: str, end: str) -> list[str]:
    start = 'Е' if start == 'Ё' else start
    end = 'Е' if end == 'Ё' else end
    if start not in _ALPH_INDEX or end not in _ALPH_INDEX:
        return []
    i, j = _ALPH_INDEX[start], _ALPH_INDEX[end]
    if i > j:
        return []
    return list(_RU_ORDER[i : j + 1])


def build_prefix_level(
    lo: Optional[str],
    hi: Optional[str],
    *,
    expand_prefix: str = '',
) -> list[dict[str, Any]]:
    """Уровень префикс-подсказок между границами lo < secret < hi.

    ``expand_prefix`` — уже раскрытый префикс ('' = верхний уровень у точки расхождения).

    Каждый элемент: ``{prefix, letter, display, expandable, kind}``
    где kind ∈ {'expand', 'leaf'}, display вроде ``РИМ+`` / ``РИН``.
    """
    lo_n = normalize_word(lo) if lo else ''
    hi_n = normalize_word(hi) if hi else ''
    prefix = normalize_word(expand_prefix)

    if lo_n and hi_n and compare_words(lo_n, hi_n) >= 0:
        return []
    if not lo_n and not hi_n:
        if not prefix:
            return []
        # Только подсказки, без границ: следующий уровень после известного префикса.
        pos = len(prefix)
        letters = list(_RU_ORDER)
        rows: list[dict[str, Any]] = []
        for ch in letters:
            node = prefix + ch
            rows.append({
                'prefix': node,
                'letter': ch,
                'display': node + '+',
                'expandable': True,
                'kind': 'expand',
            })
        return rows

    # Верхний уровень: начинаем с LCP границ (если обе есть и префикс пуст).
    if not prefix and lo_n and hi_n:
        lcp = 0
        limit = min(len(lo_n), len(hi_n))
        while lcp < limit and lo_n[lcp] == hi_n[lcp]:
            lcp += 1
        prefix = lo_n[:lcp]

    if prefix:
        if lo_n and not (
            lo_n.startswith(prefix) or compare_words(lo_n, prefix) < 0
        ):
            return []
        if hi_n and not (
            hi_n.startswith(prefix) or compare_words(hi_n, prefix) > 0
        ):
            return []

    pos = len(prefix)
    lo_bound = lo_n if lo_n.startswith(prefix) else ''
    hi_bound = hi_n if hi_n.startswith(prefix) else ''

    if lo_bound and len(lo_bound) > pos:
        start_ch = lo_bound[pos]
    else:
        start_ch = _RU_ORDER[0]

    if hi_bound and len(hi_bound) > pos:
        end_ch = hi_bound[pos]
        # hi заканчивается на этой букве → secret < hi не может начинаться с prefix+end_ch.
        # Сама граница уже показана отдельным словом; из интервала букву убираем.
        hi_ends_here = len(hi_bound) == pos + 1
    else:
        end_ch = _RU_ORDER[-1]
        hi_ends_here = False

    if hi_ends_here:
        idx = _ALPH_INDEX.get(end_ch if end_ch != 'Ё' else 'Е')
        if idx is None or idx <= 0:
            return []
        end_ch = _RU_ORDER[idx - 1]

    letters = _letter_range(start_ch, end_ch)
    if not letters:
        return []

    rows: list[dict[str, Any]] = []
    for ch in letters:
        node = prefix + ch
        is_lo = bool(lo_bound) and len(lo_bound) > pos and lo_bound[pos] == ch
        is_hi = bool(hi_bound) and len(hi_bound) > pos and hi_bound[pos] == ch
        expandable = False
        if is_lo:
            # Слово-граница длиннее node ИЛИ ровно node (тогда расширения всё ещё > lo).
            if len(lo_bound) >= len(node):
                expandable = True
        if is_hi and len(hi_bound) > len(node):
            expandable = True
        kind = 'expand' if expandable else 'leaf'
        suffix = '+' if expandable else ''
        rows.append({
            'prefix': node,
            'letter': ch,
            'display': node + suffix,
            'expandable': expandable,
            'kind': kind,
        })
    return rows
