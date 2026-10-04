"""Tests for Цензурки engine and staff access."""

from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase

from games.censorly import CENSORLY_GAME_ID, CENSORLY_TAGS_KEY, CENSORLY_TASK_TYPE
from games.censorly.normalize import lemma_of, normalize_surface
from games.censorly.play import apply_guess, get_play_state, puzzle_from_task
from games.censorly.redact import build_public_view, lemmas_matching_guess
from games.censorly.stopwords import is_stop_word
from games.censorly.tokenize import build_puzzle_payload, title_content_lemmas
from games.models import CheckerType, Game, GameTaskGroup, Project, RandomCensorlyGame, Task, TaskGroup
from games.placement_share import allocate_share_hash

_CENSORLY_TESTDATA = Path(__file__).resolve().parent / 'censorly_testdata'


def _load_censorly_testdata(name: str) -> str:
    return (_CENSORLY_TESTDATA / name).read_text(encoding='utf-8')


def _make_puzzle_task(*, title='Москва', body='Москва — столица России. В Москве живут люди.'):
    Project.objects.get_or_create(id='sections')
    CheckerType.objects.get_or_create(id='censorly')
    project = Project.objects.get(id='sections')
    game, _ = Game.objects.update_or_create(
        id=CENSORLY_GAME_ID,
        defaults={
            'name': 'Цензурки',
            'outside_name': 'Цензурки',
            'theme': 'test',
            'project': project,
            'author': 'test',
            'is_ready': False,
            'is_playable': True,
            'is_tournament': False,
        },
    )
    puzzle = build_puzzle_payload(wiki_title=title, body_text=body, wiki_pageid=1)
    checker = CheckerType.objects.get(pk='censorly')
    tg = TaskGroup.objects.create(label='censorly:test', checker=checker, points=1)
    task = Task.objects.create(
        task_group=tg,
        number='1',
        task_type=CENSORLY_TASK_TYPE,
        checker=checker,
        answer=title,
        tags={CENSORLY_TAGS_KEY: puzzle},
        points=1,
    )
    share_hash = allocate_share_hash()
    RandomCensorlyGame.objects.create(
        wiki_title=title,
        share_hash=share_hash,
        task_group=tg,
    )
    GameTaskGroup.objects.create(
        game=game,
        task_group=tg,
        number=share_hash,
        name=f'Цензурка: {title}',
        share_hash=share_hash,
    )
    return game, task, share_hash, puzzle


class CensorlyEngineTests(TestCase):
    def test_stopwords_and_tokenize(self):
        self.assertTrue(is_stop_word('в'))
        self.assertTrue(is_stop_word('И'))
        self.assertFalse(is_stop_word('Москва'))
        payload = build_puzzle_payload(
            wiki_title='Москва',
            body_text='В городе Москва живут кошки.',
        )
        kinds = [t['kind'] for t in payload['body_tokens'] if t['kind'] != 'space']
        self.assertIn('stop', kinds)
        self.assertIn('content', kinds)
        self.assertEqual(title_content_lemmas(payload), {lemma_of('Москва')})

    def test_redact_hides_content(self):
        payload = build_puzzle_payload(
            wiki_title='Кот',
            body_text='Кот и собака.',
        )
        view = build_public_view(payload, revealed_lemmas=set(), won=False)
        title = view['title_tokens']
        content = [t for t in title if t['kind'] == 'content']
        self.assertTrue(content)
        self.assertFalse(content[0]['revealed'])
        self.assertIn('length', content[0])
        self.assertNotIn('text', content[0])
        self.assertNotIn('lemma', content[0])

        view2 = build_public_view(
            payload,
            revealed_lemmas={lemma_of('кот')},
            last_lemma=lemma_of('кот'),
            won=False,
        )
        content2 = [t for t in view2['title_tokens'] if t['kind'] == 'content'][0]
        self.assertTrue(content2['revealed'])
        self.assertTrue(content2['just_revealed'])

    def test_lemma_match_opens_forms(self):
        payload = build_puzzle_payload(
            wiki_title='Москва',
            body_text='Москва и Москве и Москвы.',
        )
        lemma = lemma_of('Москва')
        matched = lemmas_matching_guess(payload, lemma, normalize_surface('москве'))
        self.assertIn(lemma, matched)

    def test_guess_round_trip_to_win(self):
        game, task, _hash, puzzle = _make_puzzle_task(
            title='Кот',
            body='Кот сидит на окне. Коты любят сон.',
        )
        user = User.objects.create_user('cz_player', password='x')
        miss = apply_guess(game=game, task=task, word='собака', user=user)
        self.assertEqual(miss['status'], 'miss')
        self.assertFalse(miss['won'])

        hit = apply_guess(game=game, task=task, word='коты', user=user)
        self.assertEqual(hit['status'], 'won')
        self.assertTrue(hit['won'])
        self.assertGreater(hit['hits'], 0)
        self.assertEqual(hit.get('wiki_title'), 'Кот')

        state = get_play_state(game=game, task=task, user=user)
        self.assertTrue(state['won'])
        title_content = [t for t in state['title_tokens'] if t['kind'] == 'content']
        self.assertTrue(all(t['revealed'] for t in title_content))


class CensorlyAccessTests(TestCase):
    def setUp(self):
        self.game, self.task, self.share_hash, _ = _make_puzzle_task()
        # Soft-launch gate ignores is_ready; production migration sets ready=True.
        self.game.is_ready = True
        self.game.save(update_fields=['is_ready'])
        self.staff = User.objects.create_user('cz_staff', password='x', is_staff=True)
        self.plain = User.objects.create_user('cz_plain', password='x', is_staff=False)
        self.client = Client()

    def test_anon_gets_redirect_or_404(self):
        url = f'/censorly/r/{self.share_hash}/'
        resp = self.client.get(url)
        self.assertIn(resp.status_code, (302, 404))

    def test_plain_user_404_even_when_ready(self):
        self.client.force_login(self.plain)
        resp = self.client.get(f'/censorly/r/{self.share_hash}/')
        self.assertEqual(resp.status_code, 404)

    def test_staff_can_play_and_guess(self):
        self.client.force_login(self.staff)
        play = self.client.get(f'/censorly/r/{self.share_hash}/')
        self.assertEqual(play.status_code, 200)
        self.assertContains(play, 'censorly-root')
        self.assertContains(play, 'https://redactle.net')
        self.assertContains(play, 'мы благодарны им за идею цензурок')

        guess = self.client.post(
            f'/censorly/r/{self.share_hash}/guess/',
            data=json.dumps({'word': 'столица'}),
            content_type='application/json',
        )
        self.assertEqual(guess.status_code, 200)
        data = guess.json()
        self.assertIn(data['status'], ('hit', 'miss', 'won'))

    def test_hub_staff_only_when_ready(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get('/censorly/').status_code, 404)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/censorly/').status_code, 200)


class CensorlyPuzzleStorageTests(TestCase):
    def test_puzzle_roundtrip_in_tags(self):
        _game, task, _h, puzzle = _make_puzzle_task()
        loaded = puzzle_from_task(task)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['wiki_title'], puzzle['wiki_title'])
        self.assertEqual(len(loaded['title_tokens']), len(puzzle['title_tokens']))


class CensorlyWikiHelperTests(TestCase):
    def test_parse_index_php_title_url(self):
        from games.censorly.wiki import title_from_user_input
        title = title_from_user_input(
            'https://ru.wikipedia.org/w/index.php?title=%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0'
        )
        self.assertEqual(title, 'Москва')

    def test_disambiguation_heuristic(self):
        from games.censorly.wiki import _looks_like_disambiguation
        self.assertTrue(_looks_like_disambiguation(
            'Москва — многозначный термин. Существует несколько значений.'
        ))
        self.assertFalse(_looks_like_disambiguation(
            'Москва — столица России. Город на Москве-реке.'
        ))

    def test_trim_extract_caps_size(self):
        from games.censorly.wiki import MAX_BODY_CHARS, _trim_extract
        big = ('абзац текста. ' * 5000)
        trimmed, truncated = _trim_extract(big)
        self.assertTrue(truncated)
        self.assertLessEqual(len(trimmed), MAX_BODY_CHARS)

    def test_trim_strips_tail_sections_and_orphan_heading(self):
        from games.censorly.wiki import _trim_extract
        text = (
            'Вводный абзац про тему.\n\n'
            + ('Ещё текст. ' * 80)
            + '\n\n== История ==\n'
            + ('Исторический абзац. ' * 40)
            + '\n\n== Примечания ==\n'
            + '1. Сноска\n'
            + '== Ссылки ==\n'
            + '* https://example.com\n'
        )
        trimmed, _truncated = _trim_extract(text)
        self.assertNotIn('Сноска', trimmed)
        self.assertNotIn('example.com', trimmed)
        self.assertIn('Вводный абзац', trimmed)
        # Orphan heading at end of a long cut should not leave a bare section title.
        long_body = ('Лид абзац про климат региона. ' * 2000) + '\n\n== Климат ==\n'
        trimmed2, truncated2 = _trim_extract(long_body)
        self.assertTrue(truncated2)
        self.assertFalse(trimmed2.rstrip().endswith('Климат'))

    def test_trim_keeps_literary_review_section(self):
        from games.censorly.wiki import _trim_extract
        text = (
            'Лид статьи.\n\n'
            + ('Текст. ' * 50)
            + '\n\n== Литературный обзор ==\n'
            + 'Обзор книг.\n'
        )
        trimmed, _truncated = _trim_extract(text)
        self.assertIn('Обзор книг', trimmed)
        self.assertIn('Литературный обзор', trimmed)

    def test_clean_wiki_extract_strips_math_displaystyle_dumps(self):
        """Wikipedia explaintext leaves MathML glyph lines + {\\displaystyle …}."""
        from games.censorly.wiki import clean_wiki_extract, _trim_extract

        # Shape mirrors ruwiki «Магнетизм» / TextExtracts math dumps.
        dirty = (
            'Магнитное поле с микроскопической напряжённостью h описывается '
            'системой из двух уравнений (СГС):\n'
            '\n'
            '  \n'
            '    \n'
            '      \n'
            '        div\n'
            '        \u2061\n'
            '        \n'
            '          h\n'
            '        \n'
            '        =\n'
            '        0\n'
            '        ,\n'
            '      \n'
            '    \n'
            '    {\\displaystyle \\operatorname {div} \\mathbf {h} =0,'
            '\\quad \\operatorname {rot} \\mathbf {h} ='
            '{\\frac {1}{c}}{\\frac {\\partial \\mathbf {e} }{\\partial t}}+'
            '{\\frac {4\\pi }{c}}\\rho \\mathbf {v} ,}\n'
            '  \n'
            '\n'
            'где e — микроскопическая напряжённость электрического поля, '
            'а произведение плотности электрических зарядов на их скорость \n'
            '  \n'
            '    \n'
            '      \n'
            '        ρ\n'
            '        \n'
            '          v\n'
            '        \n'
            '      \n'
            '    \n'
            '    {\\displaystyle \\rho \\mathbf {v} }\n'
            '  \n'
            ' соответствует плотности тока. '
            'При этом среднюю напряжённость называют магнитной индукцией:\n'
            '\n'
            '  \n'
            '    \n'
            '      \n'
            '        B\n'
            '        .\n'
            '      \n'
            '    \n'
            '    {\\displaystyle {\\overline {\\mathbf {h} }}=\\mathbf {B} .}\n'
            '  \n'
            '\n'
            '\n'
            '==== Токи намагничивания ====\n'
            '\n'
            'Усреднённые по объёму молекулярные токи называют токами намагничивания.\n'
        )
        cleaned = clean_wiki_extract(dirty)
        self.assertNotIn('displaystyle', cleaned)
        self.assertNotIn('\\mathbf', cleaned)
        self.assertNotIn('\u2061', cleaned)
        self.assertNotIn('\\', cleaned)
        # Indented glyph dump and the TeX line are both gone; the sentence closes.
        self.assertNotIn('\n        div\n', cleaned)
        self.assertNotIn('div h', cleaned)
        self.assertIn('системой из двух уравнений (СГС):', cleaned)
        self.assertIn('где e — микроскопическая напряжённость', cleaned)
        self.assertIn('скорость соответствует плотности тока', cleaned)
        self.assertIn('магнитной индукцией:', cleaned)
        self.assertIn('==== Токи намагничивания ====', cleaned)
        self.assertIn('токами намагничивания', cleaned)
        # Still works through the full trim pipeline (headings → markers).
        trimmed, truncated = _trim_extract(dirty)
        self.assertFalse(truncated)
        self.assertNotIn('displaystyle', trimmed)
        self.assertIn('соответствует плотности тока', trimmed)

    def test_strip_numeric_footnotes_keeps_miller_indices(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            'Минимум энергии достигается в направлениях рёбер куба '
            '[100], [010] и [001][1][12], то есть существует три оси.[99]\n'
            'Вода обладает потенциалом поверхности[уточнить]. '
            'Некоторые учёные[кто?] считают иначе.'
        )
        cleaned = clean_wiki_extract(raw)
        self.assertIn('[100]', cleaned)
        self.assertIn('[010]', cleaned)
        self.assertIn('[001]', cleaned)
        self.assertNotIn('[1]', cleaned)
        self.assertNotIn('[12]', cleaned)
        self.assertNotIn('[99]', cleaned)
        self.assertNotIn('[уточнить]', cleaned)
        self.assertNotIn('[кто?]', cleaned)
        self.assertIn('куба [100], [010] и [001], то есть', cleaned)
        self.assertIn('поверхности.', cleaned)
        self.assertIn('учёные считают', cleaned)

    def test_fixtures_magnetism_math_dump(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = _load_censorly_testdata('magnetism_math_raw.txt')
        self.assertIn('displaystyle', raw)
        cleaned = clean_wiki_extract(raw)
        self.assertNotIn('displaystyle', cleaned)
        self.assertNotIn('\\mathbf', cleaned)
        self.assertIn('уравнениями Лоренца', cleaned)
        self.assertIn('плотности тока', cleaned)

    def test_fixtures_magnetism_keeps_miller_directions(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = _load_censorly_testdata('magnetism_miller_raw.txt')
        self.assertIn('[100]', raw)
        cleaned = clean_wiki_extract(raw)
        self.assertIn('[100]', cleaned)
        self.assertIn('[010]', cleaned)
        self.assertIn('[001]', cleaned)
        self.assertNotIn('displaystyle', cleaned)
        self.assertIn('рёбер куба', cleaned)

    def test_fixtures_water_editorial_and_footnote(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = _load_censorly_testdata('water_editorial_raw.txt')
        self.assertIn('[уточнить]', raw)
        self.assertIn('[12]', raw)
        cleaned = clean_wiki_extract(raw)
        self.assertNotIn('[уточнить]', cleaned)
        self.assertNotIn('[12]', cleaned)
        self.assertIn('потенциалом поверхности.', cleaned)
        self.assertIn('поверхностное натяжение', cleaned)

    def test_fixtures_moscow_strips_mfa_pronunciation(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = _load_censorly_testdata('moscow_lead_raw.txt')
        self.assertIn('МФА:', raw)
        cleaned = clean_wiki_extract(raw)
        self.assertNotIn('МФА:', cleaned)
        self.assertNotIn('mɐ', cleaned)
        self.assertIn('Москва', cleaned)
        self.assertIn('столица России', cleaned)
        self.assertNotIn('()', cleaned)

    def test_strip_empty_mfa_label_keeps_foreign_name(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = 'Париж (фр. Paris МФА: ) — столица и крупнейший город Франции.'
        cleaned = clean_wiki_extract(raw)
        self.assertNotIn('МФА', cleaned)
        self.assertIn('Paris', cleaned)
        self.assertIn('(фр. Paris)', cleaned)
        self.assertIn('столица', cleaned)
        self.assertNotIn('()', cleaned)

    def test_strip_language_pronunciation_keeps_name_and_dates(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            'Уильям Шекспир (англ. William Shakespeare, '
            'английское произношение: [ˌwɪljəm ˈʃeɪkspɪə(r)]; '
            '26 апреля 1564 года — 23 апреля 1616) — английский поэт.'
        )
        cleaned = clean_wiki_extract(raw)
        self.assertNotIn('произношение', cleaned)
        self.assertNotIn('ʃeɪkspɪə', cleaned)
        self.assertNotIn('ˈ', cleaned)
        self.assertIn('William Shakespeare', cleaned)
        self.assertIn('26 апреля 1564', cleaned)
        self.assertIn('английский поэт', cleaned)

    def test_strip_unlabeled_ipa_bracket_keeps_miller(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = 'Слово [mɐˈskva] рядом с направлением [100] и сноской[1].'
        cleaned = clean_wiki_extract(raw)
        self.assertNotIn('mɐ', cleaned)
        self.assertNotIn('[mɐ', cleaned)
        self.assertIn('[100]', cleaned)
        self.assertNotIn('[1]', cleaned)
        self.assertIn('Слово', cleaned)
        self.assertIn('рядом', cleaned)

    def test_public_payload_hides_wiki_pageid_until_won(self):
        from games.censorly.play import public_payload
        from games.censorly.tokenize import build_puzzle_payload
        payload = build_puzzle_payload(
            wiki_title='Кот',
            body_text='Кот и собака живут вместе в доме.',
            wiki_pageid=12345,
            truncated=True,
        )
        before = public_payload({'revealed_lemmas': [], 'guesses': [], 'won': False}, payload)
        self.assertTrue(before['truncated'])
        self.assertIsNone(before.get('wiki_pageid'))
        self.assertIsNone(before.get('wiki_title'))
        after = public_payload(
            {'revealed_lemmas': list(title_content_lemmas(payload)), 'guesses': [], 'won': True},
            payload,
        )
        self.assertEqual(after.get('wiki_pageid'), 12345)
        self.assertEqual(after.get('wiki_title'), 'Кот')


class CensorlyLatinGuessTests(TestCase):
    def test_latin_title_word_is_guessable(self):
        game, task, _h, _p = _make_puzzle_task(
            title='USB',
            body='USB — стандарт передачи данных. Кабель USB удобен.',
        )
        user = User.objects.create_user('cz_latin', password='x')
        result = apply_guess(game=game, task=task, word='usb', user=user)
        self.assertEqual(result['status'], 'won')

    def test_already_open_lemma_does_not_recount_hits(self):
        game, task, _h, _p = _make_puzzle_task(
            title='Чёрный кот',
            body='Кот и коты сидят. Чёрный цвет популярен.',
        )
        user = User.objects.create_user('cz_open', password='x')
        a = apply_guess(game=game, task=task, word='кот', user=user)
        self.assertEqual(a['status'], 'hit')
        self.assertGreater(a['hits'], 0)
        attempts_after_hit = a['attempts']
        b = apply_guess(game=game, task=task, word='коты', user=user)
        self.assertEqual(b['status'], 'already_open')
        self.assertEqual(b['hits'], 0)
        self.assertFalse(b['won'])
        self.assertEqual(b['attempts'], attempts_after_hit)


class CensorlySupportViewTests(TestCase):
    def setUp(self):
        Project.objects.get_or_create(id='sections')
        CheckerType.objects.get_or_create(id='censorly')
        project = Project.objects.get(id='sections')
        Game.objects.update_or_create(
            id=CENSORLY_GAME_ID,
            defaults={
                'name': 'Цензурки',
                'outside_name': 'Цензурки',
                'theme': 'test',
                'project': project,
                'author': 'test',
                'is_ready': False,
                'is_playable': True,
                'is_tournament': False,
            },
        )
        self.staff = User.objects.create_superuser('cz_sup', 'a@b.c', 'x')
        self.client = Client()
        self.client.force_login(self.staff)

    def test_generate_random_does_not_shadow_service(self):
        """Regression: view must call service, not recurse into itself."""
        from unittest.mock import patch
        from games.support.services.censorly import CensorlyRandomRow

        fake = CensorlyRandomRow(
            id=1,
            wiki_title='Тест',
            share_hash='abcd1234abcd1234',
            play_url='/censorly/r/abcd1234abcd1234/',
            created_at='2026-01-01 00:00',
            task_id=1,
            token_count=10,
            payload_bytes=100,
        )
        with patch(
            'games.support.views.censorly_create_random',
            return_value=fake,
        ) as mocked:
            resp = self.client.post(
                '/support/censorly/generate/',
                data=b'{}',
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertEqual(resp.json()['row']['wiki_title'], 'Тест')
        mocked.assert_called_once_with()


class CensorlyUxDailyTests(TestCase):
    def setUp(self):
        self.game, self.task, self.share_hash, self.puzzle = _make_puzzle_task(
            title='Кот',
            body='Кот сидит на окне. Собака лает. Птица летит.',
        )
        from datetime import timedelta
        from django.utils import timezone
        start = (timezone.now() + timedelta(days=30)).date()
        tags = dict(self.game.tags or {})
        tags['censorly_publish_start'] = f'{start.isoformat()}T00:00:00+03:00'
        self.game.tags = tags
        self.game.is_ready = True
        self.game.save(update_fields=['tags', 'is_ready'])
        link = GameTaskGroup.objects.filter(
            game=self.game, task_group=self.task.task_group,
        ).first()
        link.number = '1'
        link.name = 'Цензурка #1'
        link.save(update_fields=['number', 'name'])
        self.staff = User.objects.create_user('cz_ux_staff', password='x', is_staff=True)
        self.plain = User.objects.create_user('cz_ux_plain', password='x', is_staff=False)
        self.client = Client()
        self.task.points = 20
        self.task.save(update_fields=['points'])
        self.task.task_group.points = 20
        self.task.task_group.save(update_fields=['points'])

    def test_staff_opens_unpublished_number(self):
        self.client.force_login(self.staff)
        resp = self.client.get('/censorly/1/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'censorly-root')

    def test_results_page_survives_random_hash_sibling(self):
        RandomCensorlyGame.objects.filter(task_group=self.task.task_group).delete()
        checker = CheckerType.objects.get(pk='censorly')
        random_group = TaskGroup.objects.create(
            label='censorly:random:extra', checker=checker, points=1,
        )
        share_hash = 'dc41833082925e97'
        RandomCensorlyGame.objects.create(
            wiki_title='Случайная статья',
            share_hash=share_hash,
            task_group=random_group,
        )
        GameTaskGroup.objects.create(
            game=self.game,
            task_group=random_group,
            number=share_hash,
            name='Случайная цензурка',
            share_hash=share_hash,
        )
        numbers = [link.number for link in GameTaskGroup.sorted_links(game=self.game)]
        listed = [
            link.number for link in GameTaskGroup.sorted_links(
                list(GameTaskGroup.objects.filter(game=self.game)),
            )
        ]
        ordered = [
            link.number for link in GameTaskGroup.order_queryset_by_number(
                GameTaskGroup.objects.filter(game=self.game),
            )
        ]
        self.assertEqual(numbers, ['1'])
        self.assertEqual(listed, ['1'])
        self.assertEqual(ordered, ['1'])
        self.client.force_login(self.staff)
        resp = self.client.get('/censorly/1/results/')
        self.assertEqual(resp.status_code, 200)
        random_results = self.client.get(f'/censorly/{share_hash}/results/')
        self.assertEqual(random_results.status_code, 200)

    def test_plain_cannot_open_unpublished(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get('/censorly/1/').status_code, 404)

    def test_accent_length_ignores_combining_marks(self):
        from games.censorly.tokenize import letter_length, tokenize_text
        self.assertEqual(letter_length('мо́ре'), 4)
        toks = tokenize_text('мо́ре')
        content = [t for t in toks if t['kind'] == 'content'][0]
        self.assertEqual(content['length'], 4)
        # Surfaces are stored without combining accents.
        self.assertNotIn('\u0301', content['surface'])
        self.assertEqual(content['surface'], 'море')

    def test_hyphen_splits_and_unusual_symbols_stay_visible(self):
        from games.censorly.tokenize import tokenize_text

        def kinds(text):
            return [
                (t['kind'], t['surface'])
                for t in tokenize_text(text)
                if t['kind'] != 'space'
            ]

        self.assertEqual(
            kinds('Санкт-Петербург и по-русски'),
            [
                ('content', 'Санкт'),
                ('punct', '-'),
                ('content', 'Петербург'),
                ('stop', 'и'),
                ('stop', 'по'),
                ('punct', '-'),
                ('content', 'русски'),
            ],
        )
        self.assertEqual(
            kinds('сло\u00adво и слово\u200bслово'),
            [
                ('content', 'слово'),
                ('stop', 'и'),
                ('content', 'словослово'),
            ],
        )
        self.assertEqual(
            kinds('поле α, H₂O, mc², 東京 и файл_имя'),
            [
                ('content', 'поле'),
                ('content', 'α'),
                ('punct', ','),
                ('content', 'H'),
                ('punct', '₂'),
                ('content', 'O'),
                ('punct', ','),
                ('content', 'mc'),
                ('punct', '²,'),
                ('content', '東京'),
                ('stop', 'и'),
                ('content', 'файл_имя'),
            ],
        )

    def test_title_lemmas_marked_without_colors(self):
        from games.censorly.redact import build_public_view
        payload = build_puzzle_payload(
            wiki_title='Красная площадь Москва',
            body_text='Красная площадь в Москве.',
        )
        self.assertNotIn('title_color_map', payload)
        view = build_public_view(payload, revealed_lemmas=set(), won=False)
        title_masks = [t for t in view['title_tokens'] if t.get('kind') == 'content']
        self.assertTrue(title_masks)
        self.assertTrue(all(t.get('title_lemma') for t in title_masks))
        self.assertTrue(all('title_color' not in t for t in title_masks))

    def test_hint_forbidden_on_title_lemma(self):
        from games.censorly.play import apply_hint
        from games.censorly.tokenize import title_content_lemmas
        title_lemmas = title_content_lemmas(self.puzzle)
        title_tok = next(
            t for t in self.puzzle['title_tokens']
            if t.get('kind') == 'content' and t.get('lemma') in title_lemmas
        )
        result = apply_hint(
            game=self.game, task=self.task, token_id=title_tok['id'], user=self.staff,
        )
        self.assertEqual(result['status'], 'error')

        body_tok = next(
            t for t in self.puzzle['body_tokens']
            if t.get('kind') == 'content'
            and t.get('lemma')
            and t.get('lemma') not in title_lemmas
        )
        ok = apply_hint(
            game=self.game, task=self.task, token_id=body_tok['id'], user=self.staff,
        )
        self.assertEqual(ok['status'], 'hint')
        self.assertEqual(ok['hints_taken'], 1)
        self.assertEqual(ok['points'], 19)

    def test_hint_forbidden_on_any_title_lemma_in_body(self):
        """Title words repeated in the body stay unhintable."""
        from games.censorly.play import apply_hint, puzzle_from_task
        title = 'Альфа Бета Гамма Дельта Эпсилон Дзета'
        body = 'Альфа и дзета встречаются в тексте.'
        puzzle = build_puzzle_payload(wiki_title=title, body_text=body)
        self.task.tags = {CENSORLY_TAGS_KEY: puzzle}
        self.task.answer = title
        self.task.save(update_fields=['tags', 'answer'])
        from games.censorly.tokenize import title_content_lemmas
        lemmas = title_content_lemmas(puzzle)
        self.assertGreaterEqual(len(lemmas), 6)
        tok = next(
            t for t in puzzle['body_tokens']
            if t.get('kind') == 'content' and t.get('lemma') in lemmas
        )
        result = apply_hint(
            game=self.game, task=self.task, token_id=tok['id'], user=self.staff,
        )
        self.assertEqual(result['status'], 'error')
        self.assertIsNotNone(puzzle_from_task(self.task))

    def test_mask_endings_and_heading_tokens(self):
        from games.censorly.redact import build_public_view
        from games.censorly.tokenize import HEADING_END, HEADING_START, build_puzzle_payload
        payload = build_puzzle_payload(
            wiki_title='Кот',
            body_text=(
                f'Красивого кота.\n{HEADING_START}История{HEADING_END}\n'
                'Дальше текст.'
            ),
        )
        heading_words = [
            t for t in payload['body_tokens']
            if t.get('in_heading') and t.get('kind') == 'content'
        ]
        self.assertEqual(len(heading_words), 1)
        self.assertEqual(heading_words[0]['surface'], 'История')
        self.assertTrue(any(t.get('kind') == 'heading_break' for t in payload['body_tokens']))
        content = next(
            t for t in payload['body_tokens']
            if t.get('kind') == 'content' and t.get('ending')
        )
        self.assertEqual(content['ending'], 'ого')
        view = build_public_view(payload, revealed_lemmas=set(), won=False, show_endings=True)
        masked = next(t for t in view['body_tokens'] if t.get('id') == content['id'])
        self.assertEqual(masked.get('ending'), content['ending'])
        self.assertFalse(masked.get('revealed'))
        heading_view = next(
            t for t in view['body_tokens']
            if t.get('in_heading') and t.get('kind') == 'content'
        )
        self.assertFalse(heading_view.get('revealed'))
        self.assertTrue(heading_view.get('in_heading'))

    def test_short_lemma_endings_are_shown(self):
        from games.censorly.normalize import split_stem_ending
        from games.censorly.redact import build_public_view
        from games.censorly.tokenize import build_puzzle_payload
        # 1-letter and short leftovers from the lemma split are real endings.
        self.assertEqual(split_stem_ending('города')[1], 'а')
        self.assertEqual(split_stem_ending('кошек')[1], 'ек')
        self.assertEqual(split_stem_ending('бежал')[1], 'л')
        self.assertEqual(split_stem_ending('красивого')[1], 'ого')
        self.assertEqual(split_stem_ending('кошками')[1], 'ми')
        payload = build_puzzle_payload(wiki_title='Кот', body_text='Красивого вида.')
        view = build_public_view(payload, revealed_lemmas=set(), won=False, show_endings=True)
        masked = next(
            t for t in view['body_tokens']
            if t.get('kind') == 'content' and t.get('ending') == 'ого'
        )
        self.assertEqual(masked.get('ending'), 'ого')
        # Non-letter junk still must not appear as an ending hint.
        target = next(t for t in payload['body_tokens'] if t.get('id') == masked['id'])
        target['ending'] = 'а1'
        target['stem_length'] = 5
        view2 = build_public_view(payload, revealed_lemmas=set(), won=False, show_endings=True)
        masked2 = next(t for t in view2['body_tokens'] if t.get('id') == target['id'])
        self.assertNotIn('ending', masked2)

    def test_only_guessed_words_marked_after_win(self):
        from games.censorly.redact import build_public_view
        from games.censorly.normalize import lemma_of
        payload = build_puzzle_payload(
            wiki_title='Кот',
            body_text='Кот и собака рядом.',
        )
        guessed = {lemma_of('кот')}
        view = build_public_view(payload, revealed_lemmas=guessed, won=True)
        by_lemma = {
            t.get('lemma'): t
            for t in view['body_tokens']
            if t.get('kind') == 'content'
        }
        self.assertTrue(by_lemma[lemma_of('кот')]['guessed'])
        self.assertTrue(by_lemma[lemma_of('собака')]['revealed'])
        self.assertFalse(by_lemma[lemma_of('собака')]['guessed'])

    def test_accented_guess_is_accepted(self):
        from games.censorly.normalize import is_guessable_word, normalize_surface
        self.assertTrue(is_guessable_word('мо́ре'))
        self.assertTrue(is_guessable_word('θέρμη'))
        self.assertTrue(is_guessable_word('東京'))
        self.assertFalse(is_guessable_word('Санкт-Петербург'))
        self.assertFalse(is_guessable_word('mc²'))
        self.assertEqual(normalize_surface('мо́ре'), 'море')
        game, task, _h, _p = _make_puzzle_task(
            title='Море',
            body='Море шумит. В море плавают.',
        )
        # Rebuild with accented surface in body
        puzzle = build_puzzle_payload(
            wiki_title='Море',
            body_text='Мо́ре шумит у берега.',
        )
        task.tags = {CENSORLY_TAGS_KEY: puzzle}
        task.save(update_fields=['tags'])
        user = User.objects.create_user('cz_accent', password='x')
        result = apply_guess(game=game, task=task, word='мо́ре', user=user)
        self.assertIn(result['status'], ('hit', 'won'))

    def test_results_adapter_registered(self):
        from games.daily.results import get_daily_results_adapter
        adapter = get_daily_results_adapter('censorly')
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.task_variant, 'alphabetty')

    def test_statistics_adapter_smoke(self):
        from games.daily.statistics import DAILY_STATISTICS_ADAPTERS, _censorly
        self.assertIn('censorly', DAILY_STATISTICS_ADAPTERS)
        data = _censorly(self.task, self.game, {})
        self.assertEqual(data['kind'], 'censorly')
        self.assertEqual(data['solved'], 0)


class CensorlyPoolExtractTests(SimpleTestCase):
    """Frozen ruwiki extracts from the article pool. No live fetch."""

    def test_pool_extracts_stay_readable_after_clean(self):
        from games.censorly.tokenize import tokenize_text
        from games.censorly.wiki import _trim_extract

        pool = _CENSORLY_TESTDATA / 'pool'
        files = sorted(pool.glob('*.txt'))
        self.assertEqual(len(files), 15)
        for path in files:
            raw = path.read_text(encoding='utf-8')
            cleaned, _truncated = _trim_extract(raw)
            self.assertGreater(len(cleaned), 3500, path.name)
            self.assertNotIn('displaystyle', cleaned, path.name)
            self.assertNotIn('\\mathbf', cleaned, path.name)
            self.assertNotIn('МФА:', cleaned, path.name)
            content = [
                t for t in tokenize_text(cleaned)
                if t['kind'] == 'content'
            ]
            self.assertGreater(len(content), 200, path.name)


class CensorlyHeadingLevelTests(SimpleTestCase):
    def test_stacked_wiki_headings_keep_level(self):
        from games.censorly.tokenize import HEADING_END, HEADING_START
        from games.censorly.wiki import _headings_to_marked

        marked = _headings_to_marked(
            'Абзац.\n\n== Раздел ==\n=== Подраздел ===\n==== Подподраздел ====\nДальше.\n'
        )
        payload = build_puzzle_payload(wiki_title='Кот', body_text=marked)
        words = [
            t for t in payload['body_tokens']
            if t.get('in_heading') and t.get('kind') == 'content'
        ]
        self.assertEqual(
            [(t['surface'], t['heading_level']) for t in words],
            [('Раздел', 2), ('Подраздел', 3), ('Подподраздел', 4)],
        )
        view = build_public_view(payload, revealed_lemmas=set(), won=False)
        view_words = [
            t for t in view['body_tokens']
            if t.get('in_heading') and t.get('kind') == 'content'
        ]
        self.assertEqual([t['heading_level'] for t in view_words], [2, 3, 4])
        # Older markers without a level stay a section heading.
        legacy = build_puzzle_payload(
            wiki_title='Кот',
            body_text=f'Текст.\n{HEADING_START}История{HEADING_END}\nЕщё.',
        )
        legacy_word = next(
            t for t in legacy['body_tokens']
            if t.get('in_heading') and t.get('kind') == 'content'
        )
        self.assertEqual(legacy_word['heading_level'], 2)


class CensorlyShareExcerptTests(SimpleTestCase):
    def _visible(self, runs):
        return ''.join(run.get('text', '') for run in runs if run.get('kind') == 'text')

    def test_excerpt_masks_title_and_first_paragraph(self):
        from games.censorly.redact import unsolved_share_runs

        payload = build_puzzle_payload(
            wiki_title='Пари\u0301ж',
            body_text=(
                'Столица и крупнейший город Франции.\n\n'
                'Второй абзац про историю не должен попасть на карточку.'
            ),
        )
        title, lead = unsolved_share_runs(payload)
        blob = json.dumps({'title': title, 'lead': lead}, ensure_ascii=False)
        self.assertTrue(any(run.get('kind') == 'mask' and run.get('title') for run in title))
        self.assertNotIn('Париж', blob)
        self.assertNotIn('Столица', blob)
        self.assertNotIn('Второй', blob)
        self.assertNotIn('историю', blob)
        self.assertIn(' и ', self._visible(lead))
        self.assertTrue(any(run.get('kind') == 'break' for run in lead))
        self.assertGreater(sum(1 for run in lead if run.get('kind') == 'mask'), 4)

    def test_excerpt_keeps_later_paragraphs_masked(self):
        from games.censorly.tokenize import HEADING_END, HEADING_START
        from games.censorly.redact import unsolved_share_runs

        payload = build_puzzle_payload(
            wiki_title='Кот',
            body_text=f'Первый абзац.\n{HEADING_START}История{HEADING_END}\nДальше текст.',
        )
        title, lead = unsolved_share_runs(payload)
        blob = json.dumps({'title': title, 'lead': lead}, ensure_ascii=False)
        self.assertTrue(any(run.get('kind') == 'mask' for run in title))
        self.assertNotIn('Кот', blob)
        self.assertNotIn('История', blob)
        self.assertNotIn('Дальше', blob)
        self.assertNotIn('Первый', blob)
        self.assertIn('.', self._visible(lead))
        self.assertGreaterEqual(sum(1 for run in lead if run.get('kind') == 'break'), 1)
        self.assertGreaterEqual(sum(1 for run in lead if run.get('kind') == 'mask'), 3)

    def test_solved_result_includes_share_card(self):
        from datetime import date
        from types import SimpleNamespace
        from unittest.mock import patch

        from games.censorly import CENSORLY_TAGS_KEY
        from games.censorly.play import attach_solve_meta

        puzzle = build_puzzle_payload(
            wiki_title='Пари\u0301ж',
            body_text='Столица Франции.\n\nВторой абзац.',
        )
        view = build_public_view(puzzle, won=True)
        view['won'] = True
        view['attempts'] = 4
        view['hints'] = 0
        task = SimpleNamespace(
            task_group_id=None,
            get_points=lambda: 20,
            tags={CENSORLY_TAGS_KEY: puzzle},
            checker_data='',
        )
        actor = {'user': object(), 'replay_slot': None}
        with patch('games.censorly.play.elapsed_seconds_for_actor', return_value=90), \
             patch('games.daily_share_card.publish_date_for', return_value=date(2026, 10, 1)):
            attach_solve_meta(
                view,
                game=SimpleNamespace(),
                task=task,
                number=3,
                actor=actor,
            )
        card = view['share_card']
        blob = json.dumps(card, ensure_ascii=False)
        self.assertEqual(card['kind'], 'censorly')
        self.assertTrue(card['article_title_runs'][0].get('title'))
        self.assertNotIn('Париж', blob)
        self.assertNotIn('Столица', blob)
        self.assertNotIn('Второй', blob)
        self.assertEqual(card['headline'], 'Цензурка #3 решена за 1:30')
        self.assertEqual(card['brand'], 'interoves.com/censorly/3')

    def test_replay_omits_share_card(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from games.censorly.play import attach_solve_meta

        puzzle = build_puzzle_payload(wiki_title='Кот', body_text='Кот сидит.')
        view = build_public_view(puzzle, won=True)
        view['won'] = True
        view['attempts'] = 1
        view['hints'] = 0
        task = SimpleNamespace(task_group_id=None, get_points=lambda: 20)
        with patch('games.censorly.play.elapsed_seconds_for_actor', return_value=30):
            attach_solve_meta(
                view,
                game=SimpleNamespace(),
                task=task,
                number=1,
                actor={'user': object(), 'replay_slot': 1},
            )
        self.assertNotIn('share_card', view)
        self.assertNotIn('share_text', view)
