"""Gold cognate groups for one pool article, plus pairwise school links.

``bee.roots.json`` is hand-set. It is not produced by the matcher. Pair
checks go through the gameplay matcher (``semantics.opens``).
"""

from __future__ import annotations

import json
from pathlib import Path

from django.test import SimpleTestCase

from games.puzzles.censorly.lexical.semantics import opens
from games.puzzles.censorly.text.tokenize import build_puzzle_payload

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


class BalkanRootGameplayTests(SimpleTestCase):
    def test_geographic_island_and_peninsula_roots(self):
        from games.puzzles.censorly.lexical.match import matching_lemmas
        from games.puzzles.censorly.text.tokenize import tokenize_text

        pairs = (
            ('аравия', 'аравийский'),
            ('скандинавия', 'скандинавский'),
            ('курилы', 'курильский'),
            ('антилы', 'антильский'),
            ('канары', 'канарский'),
            ('чукотка', 'чукотский'),
            ('синай', 'синайский'),
            ('багамы', 'багамский'),
            ('сейшелы', 'сейшельский'),
            ('фареры', 'фарерский'),
            ('молукки', 'молуккский'),
            ('фолкленды', 'фолклендский'),
            ('лофотены', 'лофотенский'),
            ('аляска', 'аляскинский'),
            ('лабрадор', 'лабрадорский'),
            ('ванкувер', 'ванкуверский'),
            ('сомали', 'сомалийский'),
            ('куба', 'кубинский'),
            ('крит', 'критский'),
            ('сахалин', 'сахалинский'),
            ('таймыр', 'таймырский'),
            ('ямал', 'ямальский'),
            ('корсика', 'корсиканский'),
            ('сардиния', 'сардинский'),
            ('сицилия', 'сицилийский'),
            ('кипр', 'кипрский'),
            ('мадагаскар', 'мадагаскарский'),
            ('бали', 'балийский'),
            ('суматра', 'суматранский'),
            ('мальта', 'мальтийский'),
        )
        self.assertEqual(len(pairs), 30)
        for place, adjective in pairs:
            with self.subTest(place=place):
                self.assertIn(adjective, matching_lemmas(tokenize_text(adjective), place))
                place_tokens = tokenize_text(place)
                place_lemma = next(token['lemma'] for token in place_tokens if token['kind'] == 'content')
                self.assertIn(place_lemma, matching_lemmas(place_tokens, adjective))

    def test_balkans_family_shares_one_game_root(self):
        from games.puzzles.censorly.lexical.match import matching_lemmas
        from games.puzzles.censorly.text.tokenize import tokenize_text

        tokens = tokenize_text('Балканы. Балканский. Балканистика. Забалканский. Балкон.')
        opened = matching_lemmas(tokens, 'Балканы')
        self.assertTrue({'балканский', 'балканистика', 'забалканский'} <= opened)
        self.assertNotIn('балкон', opened)
        self.assertIn('балканы', matching_lemmas(tokens, 'балканский'))


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
        self.assertTrue(opens('пчёлами', 'пчела'))
        self.assertTrue(opens('пчёлами', 'пчелиный'))
        self.assertTrue(opens('городами', 'город'))
        self.assertTrue(opens('водный', 'вода'))
        self.assertFalse(opens('водный', 'водить'))

    def test_dictionary_splits_homonyms_and_links_allomorphs(self):
        self.assertTrue(opens('пчела', 'пчелиный'))
        self.assertTrue(opens('бежать', 'бег'))
        self.assertTrue(opens('конец', 'кончать'))
        self.assertTrue(opens('нога', 'ножка'))
        self.assertTrue(opens('пять', 'пятый'))
        self.assertTrue(opens('новый', 'вновь'))
        self.assertTrue(opens('мёд', 'медовый'))
        self.assertFalse(opens('вода', 'водить'))
        self.assertFalse(opens('вода', 'пчеловод'))
        self.assertFalse(opens('лето', 'лететь'))
        self.assertFalse(opens('семь', 'семья'))
        self.assertFalse(opens('полный', 'половина'))
        self.assertFalse(opens('пара', 'испарять'))


# School-grammar pairs for Цензурки. A shared dictionary root is not enough:
# each pair is accepted or rejected on its own, without transitive closure.
_MUST_LINK = (
    ('вскоре', 'скорость'),
    ('день', 'дневной'),
    ('большой', 'больший'),
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
    ('друг', 'другой'),
    ('полукольцо', 'поляризовать'),
)


class CognateRelationTests(SimpleTestCase):
    def test_school_cognates_open_together(self):
        for left, right in _MUST_LINK:
            self.assertTrue(opens(left, right), f'{left} / {right}')
            self.assertTrue(opens(right, left), f'{right} / {left}')

    def test_school_homonyms_stay_closed(self):
        for left, right in _MUST_NOT_LINK:
            self.assertFalse(opens(left, right), f'{left} / {right}')
            self.assertFalse(opens(right, left), f'{right} / {left}')

    def test_ambiguous_form_does_not_expand_through_the_wrong_lexeme(self):
        # прибыли is both прибыль and прибыть. Neither reading may open быть.
        self.assertFalse(opens('прибыли', 'быть'))
        self.assertFalse(opens('прибыль', 'быть'))
        self.assertFalse(opens('прибыть', 'быть'))
        self.assertTrue(opens('прибыли', 'прибыть'))
        self.assertTrue(opens('прибыли', 'прибыль'))


class HalfCompoundTests(SimpleTestCase):
    def _relation(self, guess, target):
        from games.puzzles.censorly.lexical.match import ANALYZER, MATCHER

        return MATCHER.relation(ANALYZER.guess(guess), ANALYZER.target(target))

    def test_half_compound_opens_from_its_base(self):
        for guess, target in (
            ('остров', 'полуострова'),
            ('полуостров', 'остров'),
            ('остров', 'полуостровной'),
            ('полуостров', 'полуостровной'),
            ('час', 'полчаса'),
            ('круг', 'полукруг'),
            ('шар', 'полушарие'),
        ):
            with self.subTest(guess=guess, target=target):
                self.assertEqual(self._relation(guess, target), 'root')

    def test_every_half_compound_has_a_root(self):
        from games.puzzles.censorly.lexical.match.rootbank import structures_of
        from games.puzzles.censorly.lexical.reviewed import HALF_COMPOUNDS

        rootless = [compound for compound in HALF_COMPOUNDS.data if not structures_of(compound)]
        self.assertEqual(rootless, [])

    def test_reviewed_bases_open_their_families(self):
        for guess, target in (
            ('маска', 'полумаска'),
            ('маска', 'маскировка'),
            ('мягкий', 'мягкость'),
            ('метр', 'полметра'),
            ('литр', 'поллитровка'),
            ('тонна', 'полутонный'),
            ('тон', 'полутон'),
            ('штоф', 'полштофа'),
            ('кеды', 'полукеды'),
            ('финал', 'полуфинальный'),
            ('пролетарий', 'пролетариат'),
            ('легковес', 'полулегковес'),
        ):
            with self.subTest(guess=guess, target=target):
                self.assertEqual(self._relation(guess, target), 'root')

    def test_reviewed_bases_keep_homonyms_apart(self):
        for guess, target in (
            ('масса', 'массаж'),
            ('такт', 'тактика'),
            ('тон', 'тонкий'),
            ('тон', 'тонна'),
            ('метр', 'метрдотель'),
            ('метр', 'термометр'),
        ):
            with self.subTest(guess=guess, target=target):
                self.assertFalse(self._relation(guess, target))

    def test_half_does_not_open_half_compounds(self):
        for guess, target in (
            ('половина', 'полуостров'),
            ('половине', 'полуострова'),
            ('полуостров', 'половина'),
            ('пополам', 'полчаса'),
            ('половина', 'полушарие'),
            ('полукруг', 'полчаса'),
        ):
            with self.subTest(guess=guess, target=target):
                self.assertFalse(self._relation(guess, target))

    def test_other_pol_roots_stay_out_of_the_half_cluster(self):
        for guess, target in (
            ('пол', 'полуостров'),
            ('полка', 'полуостров'),
            ('полоть', 'полчаса'),
            ('поле', 'полевод'),
            ('половина', 'полдень'),
            ('половина', 'поломойка'),
            ('половина', 'полярный'),
            ('половник', 'половина'),
            ('половник', 'полуостров'),
            ('пола', 'половина'),
            ('испольщик', 'половина'),
        ):
            with self.subTest(guess=guess, target=target):
                self.assertFalse(self._relation(guess, target))

    def test_sharecropping_is_its_own_pol_cluster(self):
        self.assertEqual(self._relation('испольщик', 'исполу'), 'root')
        self.assertEqual(self._relation('полть', 'половина'), 'root')


class ReportedSenseSplitTests(SimpleTestCase):
    def _relation(self, guess, target):
        from games.puzzles.censorly.lexical.match import ANALYZER, MATCHER

        return MATCHER.relation(ANALYZER.guess(guess), ANALYZER.target(target))

    def test_reported_cognates_stay_closed(self):
        for guess, target in (
            ('растение', 'выросла'),
            ('распределение', 'пределами'),
            ('приток', 'Протекает'),
            ('нижний', 'низменность'),
            ('возраст', 'расти'),
            ('отрасль', 'вырасти'),
            ('удел', 'делить'),
            ('отек', 'течь'),
            ('унизить', 'низкий'),
            ('поток', 'приток'),
            ('ток', 'поток'),
            ('сток', 'протока'),
            ('посадить', 'соседними'),
            ('сад', 'посадить'),
            ('председатель', 'сидеть'),
            ('заседание', 'сидеть'),
            ('всадник', 'посадить'),
            ('сажа', 'сажать'),
            ('печать', 'опечатка'),
            ('печать', 'отпечаток'),
            ('напечатать', 'запечатать'),
            ('присоединение', 'единственного'),
            ('единица', 'единственный'),
            ('поединок', 'единый'),
            ('святой', 'посвящённый'),
            ('священник', 'святой'),
        ):
            with self.subTest(guess=guess, target=target):
                self.assertFalse(self._relation(guess, target))

    def test_split_clusters_still_open_within_themselves(self):
        for guess, target in (
            ('растение', 'растительность'),
            ('вырасти', 'рост'),
            ('взрослый', 'возраст'),
            ('предел', 'запредельный'),
            ('делить', 'распределение'),
            ('приток', 'отток'),
            ('сток', 'сточный'),
            ('протока', 'проточный'),
            ('течь', 'протекает'),
            ('нижний', 'вниз'),
            ('снизить', 'низкий'),
            ('низина', 'низменность'),
            ('низость', 'низменный'),
            ('впечатление', 'запечатлеть'),
            ('сосед', 'соседство'),
            ('посадить', 'сажать'),
            ('сад', 'садовник'),
            ('посадка', 'посадочный'),
            ('посадка', 'посадить'),
            ('печать', 'напечатать'),
            ('печать', 'запечатать'),
            ('распечатать', 'печатать'),
            ('распечатать', 'запечатать'),
            ('соединить', 'объединение'),
            ('единый', 'единство'),
            ('уединение', 'наедине'),
            ('посвятить', 'посвящение'),
            ('святой', 'священный'),
            ('освятить', 'освящение'),
        ):
            with self.subTest(guess=guess, target=target):
                self.assertEqual(self._relation(guess, target), 'root')

    def test_reviewed_out_of_dictionary_word_opens_its_own_forms(self):
        # pymorphy only predicts квадривиум; reviewed_derived_roots.tsv lets
        # its inflections through.
        self.assertEqual(self._relation('квадривиум', 'квадривиуму'), 'root')
        self.assertFalse(self._relation('тривиум', 'квадривиум'))
