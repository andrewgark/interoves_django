"""Relation layers: the ways a guess can open an article token.

Each layer owns both halves of its rule:

* ``opens(guess, target)``: does this layer link the pair;
* ``guess_keys(word)`` / ``target_keys(word)``: index keys such that a pair
  the layer links always shares a key. ``ArticleIndex`` builds its postings
  from these keys only, so the prefilter cannot disagree with the layer.

The order in which layers are asked, and the gates between them, live in
``deciders.matcher``.
"""

from __future__ import annotations

from functools import cached_property

from games.puzzles.censorly.lexical import reviewed
from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.deciders.readings import READINGS

_ROMAN_DIGITS = (
    (90, 'XC'), (50, 'L'), (40, 'XL'),
    (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I'),
)


def roman(value: int) -> str:
    parts = []
    left = value
    for amount, glyph in _ROMAN_DIGITS:
        while left >= amount:
            parts.append(glyph)
            left -= amount
    return ''.join(parts)


def _member_index(groups) -> dict[str, tuple[int, ...]]:
    members: dict[str, list[int]] = {}
    for group_id, group in enumerate(groups):
        for word in group:
            members.setdefault(word, []).append(group_id)
    return {word: tuple(ids) for word, ids in members.items()}


class Layer:
    name = ''
    # A layer whose target keys equal another layer's reuses its postings.
    postings_of = None

    def opens(self, guess, target) -> bool:
        raise NotImplementedError

    def guess_keys(self, word):
        return ()

    def target_keys(self, word):
        return ()


class ExactLayer(Layer):
    """Same spelling after folding."""

    name = 'exact'

    def opens(self, guess, target) -> bool:
        return guess.fold == target.fold

    def guess_keys(self, word):
        return (word.fold,)

    def target_keys(self, word):
        return (word.fold,)


class AliasLayer(Layer):
    """One-word abbreviations (кг/килограмм) and Arabic ↔ Roman numerals.

    The full form matches in any inflection (килограммов), an abbreviation
    only as written: «мед» must not become мёд, «порт» must not become порты.
    Direct pairs only: 20 opens XX, not XIX and not двадцать; кг does not
    open килограммовый.
    """

    name = 'alias'

    @cached_property
    def _groups(self) -> tuple[tuple[str, frozenset[str]], ...]:
        """(full form or '', every member spelling) per group."""
        written = tuple(
            (fold(row['full']), frozenset(fold(word) for word in (row['full'], *row['short'].split())))
            for row in reviewed.ALIASES.data
        )
        numerals = tuple(
            ('', frozenset((str(value), fold(roman(value))))) for value in range(1, 100)
        )
        return written + numerals

    @property
    def groups(self) -> tuple[frozenset[str], ...]:
        return tuple(members for _full, members in self._groups)

    @cached_property
    def _by_spelling(self) -> dict[str, tuple[int, ...]]:
        return _member_index(self.groups)

    @cached_property
    def _by_full_lemma(self) -> dict[str, tuple[int, ...]]:
        return _member_index(tuple((full,) if full else () for full, _members in self._groups))

    def _members(self, word) -> dict[int, set[str]]:
        """Group id → the members this word stands for."""
        found: dict[int, set[str]] = {}
        for group_id in self._by_spelling.get(word.fold, ()):
            found.setdefault(group_id, set()).add(word.fold)
        for item in word.reads:
            for group_id in self._by_full_lemma.get(item.lemma, ()):
                found.setdefault(group_id, set()).add(item.lemma)
        return found

    def opens(self, guess, target) -> bool:
        if guess.fold == target.fold:
            return False
        left, right = self._members(guess), self._members(target)
        return any(
            group_id in right and (members | right[group_id]) != members & right[group_id]
            for group_id, members in left.items()
        )

    def guess_keys(self, word):
        return tuple(self._members(word))

    def target_keys(self, word):
        return tuple(self._members(word))


class GrammarLayer(Layer):
    """Suppletive and ordinal groups the dictionaries do not join (два/второй)."""

    name = 'grammar'

    @cached_property
    def groups(self) -> tuple[frozenset[str], ...]:
        return tuple(frozenset(group) for group in reviewed.GRAMMAR_GROUPS.data)

    @cached_property
    def _by_word(self) -> dict[str, tuple[int, ...]]:
        return _member_index(self.groups)

    def opens(self, guess, target) -> bool:
        return self.linked(guess.reads, target.reads)

    def linked(self, left_reads, right_reads) -> bool:
        left = {item.lemma for item in left_reads}
        right = {item.lemma for item in right_reads}
        return any(left & group and right & group for group in self.groups)

    def guess_keys(self, word):
        return tuple(
            group for item in word.reads for group in self._by_word.get(item.lemma, ())
        )

    def target_keys(self, word):
        forms = {word.fold} | {fold(item.lemma) for item in READINGS.all(word.surface)}
        return tuple(group for form in forms for group in self._by_word.get(form, ()))


class ProperLayer(Layer):
    """Reviewed proper-name relations (Британия/британский, Александр/Саша).

    Relations are closed into connected components: every spelling in a
    component opens every other. A lowercase article word that is also a
    common word is not read as a given name, surname or patronymic: «поля»
    is поле, not the name Поля, so «Полина» does not open it.
    """

    name = 'proper'

    @cached_property
    def component_of(self) -> dict[str, str]:
        from games.puzzles.censorly.lexical.match.proper_names import PAIR_RELATIONS

        neighbors: dict[str, set[str]] = {}
        for left, right in PAIR_RELATIONS:
            left, right = fold(left), fold(right)
            if left == right:
                continue
            neighbors.setdefault(left, set()).add(right)
            neighbors.setdefault(right, set()).add(left)
        component_by_word: dict[str, str] = {}
        for word in neighbors:
            if word in component_by_word:
                continue
            pending, component = [word], set()
            while pending:
                current = pending.pop()
                if current in component:
                    continue
                component.add(current)
                pending.extend(neighbors[current] - component)
            component_id = min(component)
            component_by_word.update({member: component_id for member in component})
        return component_by_word

    def forms(self, word, readings) -> set[str]:
        """Spellings through which ``word`` enters the name components."""
        surface = word.surface
        readings = tuple(readings)
        spelling = {fold(surface)}
        every = READINGS.all(surface)
        if (
            not word.is_guess and not surface[:1].isupper()
            and any(item.person_name for item in every)
            and not all(item.person_name for item in every)
        ):
            readings = tuple(item for item in readings if not item.person_name)
            if any(item.person_name and item.lemma == fold(surface) for item in every):
                spelling = set()
        forms = spelling | {fold(item.lemma) for item in readings if item.lemma}
        # A context-resolved token («кит.» as a language) keeps its own reading.
        if not word.contextual_reading.startswith('__censorly_'):
            forms.update(READINGS.label_lemmas(surface))
        return forms

    def components(self, forms) -> set[str]:
        return {self.component_of[form] for form in forms if form in self.component_of}

    @staticmethod
    def _acronym_clash(word) -> bool:
        """ВОЗ is an acronym; lowercase «воз» is an unrelated common noun."""
        return word.surface != 'ВОЗ' and (
            word.fold == 'воз' or any(item.lemma == 'воз' for item in word.reads)
        )

    def opens(self, guess, target) -> bool:
        if self._acronym_clash(guess) or self._acronym_clash(target):
            return False
        return bool(
            self.components(self.forms(guess, guess.reads))
            & self.components(self.forms(target, target.reads))
        )

    def guess_keys(self, word):
        return tuple(self.components(self.forms(word, READINGS.all(word.surface))))

    def target_keys(self, word):
        return tuple(self.components(
            self.forms(word, READINGS.all(word.surface)) | self.forms(word, word.reads)
        ))


class LexemeLayer(Layer):
    """Forms of one paradigm: a shared (paradigm id, lemma)."""

    name = 'lexeme'

    def opens(self, guess, target) -> bool:
        return bool(guess.lexemes & target.lexemes)

    def guess_keys(self, word):
        return tuple(word.lexemes)

    def target_keys(self, word):
        return tuple(word.lexemes)


class ReverseLexemeLayer(Layer):
    """The article token is the citation form of one of the guess's readings.

    The guess prefers its citation reading for roots («улья» → улей), but
    must still open the citation form of its other readings.
    """

    name = 'lexeme'

    def opens(self, guess, target) -> bool:
        if guess.is_guess:
            reverse = guess.lexemes_by_fold.get(target.fold, ())
        else:
            # An article word compared as a guess (audits, tests).
            reverse = {
                (item.para_id, item.lemma)
                for item in READINGS.all(guess.surface)
                if fold(item.lemma) == target.fold
            }
        return bool(reverse and reverse & target.lexemes)

    def guess_keys(self, word):
        return tuple(key for keys in word.lexemes_by_fold.values() for key in keys)

    def target_keys(self, word):
        return tuple(word.lexemes)


class RootLayer(Layer):
    """Equal complete root structures (with their senses)."""

    name = 'root'

    def opens(self, guess, target) -> bool:
        return bool(guess.roots & target.roots)

    def guess_keys(self, word):
        return tuple(word.roots)

    def target_keys(self, word):
        return tuple(word.roots)


EXACT = ExactLayer()
ALIAS = AliasLayer()
GRAMMAR = GrammarLayer()
PROPER = ProperLayer()
LEXEME = LexemeLayer()
REVERSE_LEXEME = ReverseLexemeLayer()
REVERSE_LEXEME.postings_of = LEXEME
ROOT = RootLayer()
