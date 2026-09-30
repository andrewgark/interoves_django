"""Tests for Цензурки engine and staff access."""

from __future__ import annotations

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

from games.censorly import CENSORLY_GAME_ID, CENSORLY_TAGS_KEY, CENSORLY_TASK_TYPE
from games.censorly.normalize import lemma_of, normalize_surface
from games.censorly.play import apply_guess, get_play_state, puzzle_from_task
from games.censorly.redact import build_public_view, lemmas_matching_guess
from games.censorly.stopwords import is_stop_word
from games.censorly.tokenize import build_puzzle_payload, title_content_lemmas
from games.models import CheckerType, Game, GameTaskGroup, Project, RandomCensorlyGame, Task, TaskGroup
from games.placement_share import allocate_share_hash


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
        self.staff = User.objects.create_user('cz_staff', password='x', is_staff=True)
        self.plain = User.objects.create_user('cz_plain', password='x', is_staff=False)
        self.client = Client()

    def test_anon_gets_redirect_or_404(self):
        url = f'/censorly/r/{self.share_hash}/'
        resp = self.client.get(url)
        # login_required → redirect to login
        self.assertIn(resp.status_code, (302, 404))

    def test_plain_user_404(self):
        self.client.force_login(self.plain)
        resp = self.client.get(f'/censorly/r/{self.share_hash}/')
        self.assertEqual(resp.status_code, 404)

    def test_staff_can_play_and_guess(self):
        self.client.force_login(self.staff)
        play = self.client.get(f'/censorly/r/{self.share_hash}/')
        self.assertEqual(play.status_code, 200)
        self.assertContains(play, 'censorly-root')

        guess = self.client.post(
            f'/censorly/r/{self.share_hash}/guess/',
            data=json.dumps({'word': 'столица'}),
            content_type='application/json',
        )
        self.assertEqual(guess.status_code, 200)
        data = guess.json()
        self.assertIn(data['status'], ('hit', 'miss', 'won'))

    def test_hub_staff_only(self):
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
        trimmed = _trim_extract(big)
        self.assertLessEqual(len(trimmed), MAX_BODY_CHARS)


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
        b = apply_guess(game=game, task=task, word='коты', user=user)
        self.assertEqual(b['status'], 'already_open')
        self.assertEqual(b['hits'], 0)
        self.assertFalse(b['won'])


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
        from games.support.services.censorly import CensorlyRow

        fake = CensorlyRow(
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
