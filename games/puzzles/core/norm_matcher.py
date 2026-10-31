from functools import lru_cache

import pymorphy3

from games.puzzles.core.normalize_ops import normalize

MORPH_ANALYZER = pymorphy3.MorphAnalyzer()


def get_norm_form(word):
    return MORPH_ANALYZER.parse(word)[0].normal_form


@lru_cache(maxsize=4096)
def accepted_forms(word, ops):
    """Forms of ``word`` that ``NormMatcher`` accepts in its place, normalized by ``ops``.

    Every form of every lexeme ``word`` may belong to, kept only when the
    lemmatizer takes it back to the lemma of ``word``: the set never holds a
    form the server would reject. Forms the dictionary does not list (typos,
    unknown words) are missing, so the set is not every accepted spelling.
    """
    lemma = get_norm_form(word)
    forms = {word}
    for parse in MORPH_ANALYZER.parse(word):
        for form in parse.lexeme:
            normalized = normalize(form.word, ops)
            if normalized and len(normalized.split()) == 1:
                forms.add(normalized)
    return frozenset(form for form in forms if get_norm_form(form) == lemma)


class NormMatcher:
    def __init__(self, pattern):
        self.variants = []
        # The pattern's own words, for the client spec (accepted_forms).
        self.variant_words = []
        for variant in pattern.split('\n'):
            words = variant.strip().split()
            self.variant_words.append(words)
            words = [get_norm_form(word) for word in words]
            self.variants.append(words)

    def match(self, text):
        words = text.strip().split()
        words = [get_norm_form(word) for word in words]
        for variant in self.variants:
            if len(variant) != len(words):
                continue
            ok = True
            for variant_word, word in zip(variant, words):
                if word != variant_word:
                    ok = False
                    break
            if ok:
                return True
        return False
