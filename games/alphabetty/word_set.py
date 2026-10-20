"""Компактное неизменяемое множество слов для словаря Алфавитки.

``frozenset`` из 1.8 млн словоформ занимает в CPython ~210 МБ: каждая
кириллическая строка хранится в UCS-2 с заголовком объекта, а хеш-таблица
добавляет ещё ~95 МБ. ``PackedWordSet`` держит все слова в одном ``bytes``
(~20 МБ): однобайтовая кодировка, слова отсортированы по байтам и разделены
``\\n``. Проверка ``word in words`` — бинарный поиск по этому буферу, без
создания объекта на каждое слово.

Формат буфера (он же формат файла после распаковки)::

    b"\\nАБА\\nАББАТ\\n...\\nЯЯ\\n"

ведущий и завершающий ``\\n``, без пустых строк и повторов, строки по
возрастанию байтов. Для А–Я в cp1251 порядок байтов совпадает с алфавитным,
но множеству это не важно: порядок нужен только самому поиску.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Set

ENCODING = 'cp1251'
_SEP = b'\n'
_EMPTY = _SEP
# Кусок, на котором ищем самое длинное слово: память на временные bytes
# ограничена им, а не всем словарём.
_SCAN_CHUNK = 1 << 20


class PackedWordSet(Set):
    """Неизменяемое множество строк в одном отсортированном ``bytes``."""

    __slots__ = ('_blob', '_count', '_max_length')

    def __init__(self, blob: bytes = _EMPTY):
        blob = bytes(blob)
        _check_format(blob)
        self._blob = blob
        self._count = blob.count(_SEP) - 1
        # Нужна только странице игры, а скан занимает ~150 мс на 1.8 млн слов.
        self._max_length: int | None = None

    @classmethod
    def from_words(cls, words: Iterable[str]) -> PackedWordSet:
        encoded = {_encode_strict(word) for word in words}
        if not encoded:
            return cls()
        return cls(_SEP + _SEP.join(sorted(encoded)) + _SEP)

    @classmethod
    def _from_iterable(cls, words: Iterable[str]) -> PackedWordSet:
        # Set-миксины (&, |, -, ^) строят результат через cls(iterable).
        return cls.from_words(words)

    def to_bytes(self) -> bytes:
        return self._blob

    @property
    def max_length(self) -> int:
        """Длина самого длинного слова (0 для пустого множества)."""
        if self._max_length is None:
            self._max_length = _longest_line(self._blob)
        return self._max_length

    def __contains__(self, word: object) -> bool:
        key = _encode_lookup(word)
        if key is None:
            return False
        blob = self._blob
        # Инвариант: искомое слово, если есть, лежит строкой целиком между
        # разделителями blob[lo] и blob[hi].
        lo, hi = 0, len(blob) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            start = blob.rfind(_SEP, lo, mid + 1)
            end = blob.find(_SEP, start + 1, hi + 1)
            line = blob[start + 1:end]
            if line == key:
                return True
            if line < key:
                lo = end
            else:
                hi = start
        return False

    def __iter__(self) -> Iterator[str]:
        blob = self._blob
        start = 0
        last = len(blob) - 1
        while start < last:
            end = blob.find(_SEP, start + 1)
            yield blob[start + 1:end].decode(ENCODING)
            start = end

    def __len__(self) -> int:
        return self._count

    def __repr__(self) -> str:
        return f'<PackedWordSet words={self._count} bytes={len(self._blob)}>'


def _check_format(blob: bytes) -> None:
    if not blob.startswith(_SEP) or not blob.endswith(_SEP):
        raise ValueError('packed word set must start and end with a newline')
    if blob != _EMPTY and _SEP + _SEP in blob:
        raise ValueError('packed word set must not contain empty words')


def _encode_strict(word: str) -> bytes:
    if not word or '\n' in word:
        raise ValueError(f'invalid word for packed word set: {word!r}')
    try:
        return word.encode(ENCODING)
    except UnicodeEncodeError:
        raise ValueError(f'word is not representable in {ENCODING}: {word!r}') from None


def _encode_lookup(word: object) -> bytes | None:
    if not isinstance(word, str) or not word or '\n' in word:
        return None
    try:
        return word.encode(ENCODING)
    except UnicodeEncodeError:
        return None


def _longest_line(blob: bytes) -> int:
    # ENCODING однобайтовая, поэтому длина в байтах равна длине в символах.
    longest = 0
    start = 0
    size = len(blob)
    while start < size:
        end = blob.find(_SEP, min(start + _SCAN_CHUNK, size - 1))
        longest = max(longest, max(map(len, blob[start:end].split(_SEP))))
        start = end + 1
    return longest
