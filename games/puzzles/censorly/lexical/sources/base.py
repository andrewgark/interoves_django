"""Common base of external dictionaries and their text-file conventions."""

from __future__ import annotations

import unicodedata
from functools import cached_property
from pathlib import Path
from threading import Lock

DICTIONARY_DIR = Path(__file__).resolve().parents[1] / 'data' / 'dictionaries'

_SUPERSCRIPTS = str.maketrans('', '', '¹²³⁴⁵⁶⁷⁸⁹⁰')


class ExternalDictionary:
    """A read-only dictionary loaded from files on first use.

    Subclasses implement ``_load`` and expose lookups over ``self.data``.
    The load runs once per process and is shared by all threads.
    """

    name = ''

    def __init__(self, *paths: Path):
        self.paths = tuple(Path(path) for path in paths)
        self._lock = Lock()

    @cached_property
    def data(self):
        with self._lock:
            return self._load()

    def _load(self):
        raise NotImplementedError

    def release(self) -> None:
        """Forget the loaded data; the next lookup loads it again.

        The root bank keeps only what gameplay needs, so a web process does
        not hold the raw dictionaries after the bank is built.
        """
        with self._lock:
            self.__dict__.pop('data', None)


def lines(path: Path):
    """Non-empty, non-comment lines of a dictionary text file."""
    text = path.read_text(encoding='utf-8')
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            yield line


def strip_note(text: str) -> str:
    """Drop ``(…)`` and ``[…]`` notes from a dictionary headword."""
    for opener, closer in (('(', ')'), ('[', ']')):
        while opener in text and closer in text:
            start = text.find(opener)
            end = text.find(closer, start)
            if end < 0:
                break
            text = text[:start] + text[end + 1:]
    return text.strip()


def norm(text: str) -> str:
    """Morph spelling without stress, lower case, ё→е. Keeps superscripts."""
    folded = unicodedata.normalize('NFC', text or '')
    folded = ''.join(char for char in folded if unicodedata.category(char) != 'Mn')
    return folded.lower().replace('ё', 'е').strip()


def plain(morph: str) -> str:
    """Morph spelling as written in a word: no homonym number, no ``(j)``."""
    spelled = norm(morph).translate(_SUPERSCRIPTS)
    return spelled.replace('(j)', '').replace('(', '').replace(')', '')
