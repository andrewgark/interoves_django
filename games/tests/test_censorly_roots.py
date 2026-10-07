"""Gold cognate groups for one pool article, plus pairwise school links.

``bee.roots.json`` is hand-set. It is not produced by ``opens_with``.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.test import SimpleTestCase

from games.censorly.roots import opens_with
from games.censorly.tokenize import build_puzzle_payload

_GOLD_PATH = Path(__file__).resolve().parent / 'censorly_testdata' / 'articles' / 'bee.roots.json'
_ARTICLE_PATH = Path(__file__).resolve().parent / 'censorly_testdata' / 'articles' / 'bee.txt'


def _article_lemmas() -> set[str]:
    text = _ARTICLE_PATH.read_text()
    payload = build_puzzle_payload(wiki_title='Медоносная пчела', body_text=text)
    lemmas = set()
    for part in ('title_tokens', 'body_tokens'):
        for tok in payload[part]:
            if tok.get('kind') == 'content' and tok.get('lemma'):
                lemmas.add(tok['lemma'])
    return lemmas


def _gold():
    return json.loads(_GOLD_PATH.read_text())


def _ids_for(gold, lemma: str) -> set[str]:
    found = {group['id'] for group in gold['groups'] if lemma in group['lemmas']}
    return found or {lemma}


class CognateGoldTests(SimpleTestCase):
    def test_gold_lemmas_are_content_lemmas_of_the_article(self):
        gold = _gold()
        lemmas = _article_lemmas()
        missing = []
        for group in gold['groups']:
            self.assertGreaterEqual(len(set(group['lemmas'])), 2, group['id'])
            self.assertEqual(len(group['lemmas']), len(set(group['lemmas'])), group['id'])
            for lemma in group['lemmas']:
                if lemma not in lemmas:
                    missing.append((group['id'], lemma))
        for lemma in gold['opaque']:
            if lemma not in lemmas:
                missing.append(('opaque', lemma))
        self.assertEqual(missing, [])

    def test_homonym_roots_stay_apart(self):
        gold = _gold()
        splits = (
            ('семь', 'семья'),
            ('вода', 'пчеловод'),
            ('лето', 'лететь'),
            ('молодой', 'молочко'),
            ('печка', 'печатный'),
            ('крыло', 'крышечка'),
            ('вид', 'видеть'),
            ('испарять', 'спаривание'),
            ('сота', 'сотня'),
            ('ножка', 'приносить'),
            ('сердце', 'средний'),
            ('перейти', 'выходить'),
            ('состоять', 'становиться'),
            ('потом', 'потомство'),
            ('усик', 'сильный'),
            ('мир', 'вымирать'),
            ('цвет', 'цветковый'),
        )
        for left, right in splits:
            self.assertTrue(
                _ids_for(gold, left).isdisjoint(_ids_for(gold, right)),
                f'{left} / {right}',
            )
        self.assertEqual(_ids_for(gold, 'пчела'), _ids_for(gold, 'пчелиный'))
        self.assertIn('пчел', _ids_for(gold, 'пчеловод'))
        self.assertIn('вод2', _ids_for(gold, 'пчеловод'))
        self.assertIn('мед', _ids_for(gold, 'медоносный'))
        self.assertIn('нес', _ids_for(gold, 'медоносный'))

    def test_inflection_stays_on_the_same_lexeme(self):
        self.assertTrue(opens_with('пчёлами', 'пчела'))
        self.assertTrue(opens_with('пчёлами', 'пчелиный'))
        self.assertTrue(opens_with('городами', 'город'))
        self.assertTrue(opens_with('водный', 'вода'))
        self.assertFalse(opens_with('водный', 'водить'))

    def test_dictionary_splits_homonyms_and_links_allomorphs(self):
        self.assertTrue(opens_with('пчела', 'пчелиный'))
        self.assertTrue(opens_with('пчела', 'пчеловод'))
        self.assertTrue(opens_with('бежать', 'бег'))
        self.assertTrue(opens_with('конец', 'кончать'))
        self.assertTrue(opens_with('нога', 'ножка'))
        self.assertTrue(opens_with('пять', 'пятый'))
        self.assertTrue(opens_with('новый', 'вновь'))
        self.assertTrue(opens_with('мёд', 'медовый'))
        self.assertFalse(opens_with('вода', 'водить'))
        self.assertFalse(opens_with('вода', 'пчеловод'))
        self.assertFalse(opens_with('лето', 'лететь'))
        self.assertFalse(opens_with('семь', 'семья'))
        self.assertFalse(opens_with('полный', 'половина'))
        self.assertFalse(opens_with('пара', 'испарять'))


# School-grammar pairs for Цензурки. A shared dictionary root is not enough:
# each pair is accepted or rejected on its own, without transitive closure.
_MUST_LINK = (
    ('вместе', 'место'),
    ('он', 'она'),
    ('он', 'они'),
    ('он', 'оно'),
    ('она', 'они'),
    ('опыление', 'пыль'),
    ('вскоре', 'скорость'),
    ('мёд', 'медоносный'),
    ('вводить', 'вывести'),
    ('откладывать', 'отложить'),
    ('день', 'дневный'),
    ('день', 'дневной'),
    ('большой', 'больший'),
    ('один', 'единственный'),
    ('улей', 'улья'),
)

_MUST_NOT_LINK = (
    ('воздух', 'дышать'),
    ('достаточный', 'состав'),
    ('достаточный', 'становиться'),
    ('достаточный', 'представлять'),
    ('достаточный', 'остаться'),
    ('необходимый', 'выходить'),
    ('образ', 'раз'),
    ('образ', 'сразу'),
    ('цвет', 'цветковый'),
    ('один', 'однако'),
    ('личинка', 'отличие'),
    ('строить', 'три'),
    ('верхний', 'совершать'),
    ('печка', 'обеспечить'),
    ('брачный', 'забраться'),
    ('самый', 'самец'),
    ('слово', 'условие'),
    ('год', 'погода'),
    ('запас', 'опасность'),
    ('равный', 'уровень'),
    ('длина', 'длиться'),
    ('друг', 'другой'),
    ('полукольцо', 'поляризовать'),
)


class CognateRelationTests(SimpleTestCase):
    def test_school_cognates_open_together(self):
        for left, right in _MUST_LINK:
            self.assertTrue(opens_with(left, right), f'{left} / {right}')
            self.assertTrue(opens_with(right, left), f'{right} / {left}')

    def test_school_homonyms_stay_closed(self):
        for left, right in _MUST_NOT_LINK:
            self.assertFalse(opens_with(left, right), f'{left} / {right}')
            self.assertFalse(opens_with(right, left), f'{right} / {left}')

    def test_ambiguous_form_does_not_expand_through_the_wrong_lexeme(self):
        # прибыли is both прибыль and прибыть. Neither reading may open быть.
        self.assertFalse(opens_with('прибыли', 'быть'))
        self.assertFalse(opens_with('прибыль', 'быть'))
        self.assertFalse(opens_with('прибыть', 'быть'))
        self.assertTrue(opens_with('прибыли', 'прибыть'))
        self.assertTrue(opens_with('прибыли', 'прибыль'))

    def test_stress_homographs_are_not_one_lexeme(self):
        build = 'стро\u0301ить'
        triple = 'строи\u0301ть'
        self.assertFalse(opens_with(build, triple))
        self.assertFalse(opens_with(triple, build))
        self.assertFalse(opens_with('строить', 'три'))
        self.assertFalse(opens_with(build, 'три'))
        self.assertFalse(opens_with(triple, 'три'))
