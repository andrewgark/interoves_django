"""Named text normalization steps shared by answer checkers and the browser.

A checker declares a pipeline of step names instead of hand-written string
munging, so the same pipeline can be shipped to the client. Every step has a
twin with the same name in ``static/js/offline_normalize.js``;
``contracts/normalize.json`` pins the behaviour of both implementations.
"""

import re

_INNER_SPACES_RE = re.compile(r"[^\S\r\n]+")
_PUNCT_RE = re.compile(r"[.,\/#!?$%\^&\*;:{}=\"\-_`~()—–]+")
_PUNCT_METAGRAM_RE = re.compile(r"[.,\/#!?$%\^&\*;:{}=\"\-_`~()—]+")
_SPACE_RUN_RE = re.compile(r" +")
_NON_WORD_CHAR_RE = re.compile(r'[^0-9a-zа-яё]', re.I)


def _drop_punct(text):
    """Drop punctuation line by line; a one-character line is kept as typed."""
    result = []
    for line in text.split('\n'):
        line = line.strip()
        if len(line) != 1:
            line = _SPACE_RUN_RE.sub(' ', _PUNCT_RE.sub('', line))
        result.append(line)
    return '\n'.join(result)


def _drop_punct_metagram(text):
    """Punctuation separates words: replace it with one space."""
    return _SPACE_RUN_RE.sub(' ', _PUNCT_METAGRAM_RE.sub(' ', text))


OPS = {
    'lower': str.lower,
    'strip': str.strip,
    'yo_to_e': lambda text: text.replace('ё', 'е'),
    'drop_inner_spaces': lambda text: _INNER_SPACES_RE.sub('', text),
    'drop_punct': _drop_punct,
    'drop_punct_metagram': _drop_punct_metagram,
    'keep_word_chars': lambda text: _NON_WORD_CHAR_RE.sub('', text),
}

CLEAN = ('lower', 'strip', 'yo_to_e')
CLEAN_KEEP_YO = ('lower', 'strip')
RADDLE_WORD = ('keep_word_chars', *CLEAN)


def validate_ops(ops):
    unknown = [op for op in ops if op not in OPS]
    if unknown:
        raise ValueError('unknown normalization steps: {}'.format(', '.join(unknown)))
    return tuple(ops)


def normalize(text, ops):
    for op in ops:
        text = OPS[op](text)
    return text


def normalized(*ops):
    """Normalize the first argument after ``self`` before calling the method.

    Steps run left to right, so ``@normalized(*CLEAN, 'drop_punct')`` cleans
    first and drops punctuation second.
    """
    ops = validate_ops(ops)

    def decorator(func):
        def func_wrapper(self, text, *args, **kw):
            return func(self, normalize(text, ops), *args, **kw)

        func_wrapper.normalize_ops = ops
        return func_wrapper

    return decorator
