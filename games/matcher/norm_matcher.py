import pymorphy2


def get_norm_form(word):
    morph = pymorphy2.MorphAnalyzer()
    return morph.parse(word)[0].normal_form


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
            ok = True
            if len(variant) == len(words):
                for variant_word, word in zip(variant, words):
                    if word != variant_word:
                        ok = False
                        break
            if ok:
                return True
        return False
