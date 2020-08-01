import pymorphy2


MORPH_ANALYZER = pymorphy2.MorphAnalyzer()


def get_norm_form(word):
    return MORPH_ANALYZER.parse(word)[0].normal_form


class NormMatcher:
    def __init__(self, pattern):
        self.variants = []
        for variant in pattern.split('\n'):
            words = variant.strip().split()
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
