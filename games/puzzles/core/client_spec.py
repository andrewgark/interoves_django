"""Answer checks the browser can run without the server.

A checker describes its check as data (``BaseChecker.client_spec``): named
normalization steps (``normalize_ops``) and salted SHA-256 hashes of the
accepted normalized answers. ``verify`` and ``verify_word`` are the
reference interpreter: the browser twin must give the same verdicts, which
``contracts/client_spec.json`` pins. The hashes only keep answers out of
plain sight; a dictionary attack recovers short answers, which is accepted
for the games where client checks are enabled (docs/offline-play-plan.md).

Kinds:
  ``hash_set``  normalized answer hash -> status (``Ok`` or ``Partial``);
                any other answer is ``Wrong``.
  ``any``       every answer is ``Ok``.
  ``raddle``    per ladder word, the hashes of its accepted spellings.
  ``norm_words`` per accepted variant, per word position, the hashes of the
                word forms the server accepts there (norm_matcher). Only
                says ``Ok``: a word outside the sets may still be a form the
                server's lemmatizer accepts, so anything else is unknown
                (None) and waits for the server.
"""

from __future__ import annotations

import hashlib

from games.puzzles.core.normalize_ops import normalize, validate_ops

SPEC_VERSION = 1


def spec_salt(task):
    """Per task and revision, so equal answers of different tasks hash apart."""
    return '{}:{}'.format(task.id, task.attempt_revision)


def answer_hash(salt, normalized_answer):
    return hashlib.sha256('{}\n{}'.format(salt, normalized_answer).encode('utf-8')).hexdigest()


def hash_set_spec(salt, ops, statuses):
    """``statuses`` maps an already normalized answer to ``Ok`` or ``Partial``."""
    return {
        'v': SPEC_VERSION,
        'kind': 'hash_set',
        'ops': list(validate_ops(ops)),
        'salt': salt,
        'accept': {answer_hash(salt, answer): status for answer, status in statuses.items()},
    }


def any_answer_spec():
    return {'v': SPEC_VERSION, 'kind': 'any'}


def raddle_spec(salt, ops, words):
    """``words`` lists, per ladder word, its accepted spellings already normalized."""
    return {
        'v': SPEC_VERSION,
        'kind': 'raddle',
        'ops': list(validate_ops(ops)),
        'salt': salt,
        'words': [sorted({answer_hash(salt, spelling) for spelling in spellings}) for spellings in words],
    }


def norm_words_spec(salt, ops, variants):
    """``variants``: per accepted variant, per word, its accepted forms already normalized."""
    return {
        'v': SPEC_VERSION,
        'kind': 'norm_words',
        'ops': list(validate_ops(ops)),
        'salt': salt,
        'variants': [
            [sorted({answer_hash(salt, form) for form in forms}) for forms in variant]
            for variant in variants
        ],
    }


def verify_norm_words(spec, text):
    """``Ok`` when every word of ``text`` is an accepted form of its variant word, else None."""
    words = normalize(text, spec['ops']).split()
    digests = [answer_hash(spec['salt'], word) for word in words]
    for variant in spec['variants']:
        if len(variant) == len(digests) and all(d in forms for d, forms in zip(digests, variant)):
            return 'Ok'
    return None


def verify(spec, text):
    """Status of an answer under a ``hash_set`` or ``any`` spec."""
    if spec['kind'] == 'any':
        return 'Ok'
    if spec['kind'] == 'hash_set':
        digest = answer_hash(spec['salt'], normalize(text, spec['ops']))
        return spec['accept'].get(digest, 'Wrong')
    if spec['kind'] == 'norm_words':
        return verify_norm_words(spec, text)
    raise ValueError('not an answer spec: {}'.format(spec['kind']))


def verify_word(spec, word_index, word):
    """Whether ``word`` is an accepted spelling of ladder word ``word_index``."""
    if spec['kind'] != 'raddle':
        raise ValueError('not a ladder spec: {}'.format(spec['kind']))
    if not 0 <= word_index < len(spec['words']):
        return False
    return answer_hash(spec['salt'], normalize(word, spec['ops'])) in spec['words'][word_index]
