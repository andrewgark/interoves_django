"""Tests for Цензурки engine and staff access."""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase

from games.censorly import CENSORLY_GAME_ID, CENSORLY_TAGS_KEY, CENSORLY_TASK_TYPE
from games.censorly.normalize import lemma_of, normalize_surface
from games.censorly.play import apply_guess, apply_hint, get_play_state, hub_progress_for_actor, puzzle_from_task, reset_progress
from games.censorly.redact import build_public_view, lemmas_matching_guess
from games.censorly.stopwords import is_stop_word
from games.censorly.tokenize import build_puzzle_payload, title_content_lemmas
from games.analytics import game_instance_id_for_task_group
from games.models import Attempt, BugReport, ChainTaskState, CheckerType, DailySolveTiming, Game, GameTaskGroup, PlayerCompletedGame, Profile, Project, RandomCensorlyGame, Task, TaskGroup
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
            'rules': None,
            'tournament_rules': None,
            'general_rules': None,
            'section_default_rules': None,
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


class CensorlySymbolTokenTests(SimpleTestCase):
    def test_structural_symbols_are_visible_but_pictograms_stay_masked(self):
        from games.censorly.tokenize import tokenize_text

        tokens = [
            token for token in tokenize_text('+ = − № $ € ₽ < > ^ © ™ ♄ ♕')
            if token['kind'] != 'space'
        ]
        self.assertEqual(
            [(token['surface'], token['kind']) for token in tokens],
            [
                ('+', 'punct'), ('=', 'punct'), ('−', 'punct'),
                ('№', 'punct'), ('$', 'punct'), ('€', 'punct'),
                ('₽', 'punct'), ('<', 'punct'), ('>', 'punct'),
                ('^', 'punct'), ('©', 'punct'), ('™', 'punct'),
                ('♄', 'symbol'), ('♕', 'symbol'),
            ],
        )
        payload = build_puzzle_payload(
            wiki_title='Знаки', body_text='+23 pH = 4,5 дом № 252 $100 ♄ ♕',
        )
        view = build_public_view(payload, revealed_lemmas=set(), won=False)
        visible = ''.join(
            token.get('text', '') for token in view['body_tokens'] if token['revealed']
        )
        self.assertIn('+', visible)
        self.assertIn('=', visible)
        self.assertIn('№', visible)
        self.assertIn('$', visible)
        self.assertNotIn('♄', visible)
        self.assertNotIn('♕', visible)

    def test_degree_and_punctuation_stay_visible_next_to_masked_tokens(self):
        from games.censorly.tokenize import tokenize_text

        tokens = tokenize_text('(примерно 45°), ♕), (×)')
        self.assertEqual(
            [(token['surface'], token['kind']) for token in tokens],
            [
                ('(', 'punct'), ('примерно', 'content'), (' ', 'space'),
                ('45', 'content'), ('°', 'punct'), ('),', 'punct'),
                (' ', 'space'), ('♕', 'symbol'), ('),', 'punct'),
                (' ', 'space'), ('(', 'punct'), ('×', 'stop'), (')', 'punct'),
            ],
        )
        payload = build_puzzle_payload(wiki_title='Немецкая овчарка', body_text='(примерно 45°),')
        view = build_public_view(payload, revealed_lemmas=set(), won=False)
        visible = [token['text'] for token in view['body_tokens'] if token['revealed']]
        self.assertEqual(visible, ['(', ' ', '°', '),'])

    def test_chess_symbols_are_masked_but_math_service_symbols_stay_visible(self):
        from games.censorly import CENSORLY_SPLITTER_VERSION
        from games.censorly.tokenize import resolve_puzzle_payload, tokenize_text

        tokens = [
            token for token in tokenize_text('♕ ♛ ∑ → ± √')
            if token['kind'] != 'space'
        ]
        self.assertEqual(
            [(token['kind'], token['surface']) for token in tokens],
            [
                ('symbol', '♕'), ('symbol', '♛'),
                ('stop', '∑'), ('stop', '→'), ('stop', '±'), ('stop', '√'),
            ],
        )

        payload = build_puzzle_payload(wiki_title='Фигуры', body_text='♕ ♛ ∑ → ± √')
        view = build_public_view(payload, revealed_lemmas=set(), won=False)
        symbols = [token for token in view['body_tokens'] if token['kind'] == 'symbol']
        math = [token for token in view['body_tokens'] if token['kind'] == 'stop']
        self.assertEqual(len(symbols), 2)
        self.assertTrue(all(not token['revealed'] and 'text' not in token for token in symbols))
        self.assertEqual([token['text'] for token in math], ['∑', '→', '±', '√'])

        from games.censorly.redact import unsolved_share_runs
        _title, lead = unsolved_share_runs(payload)
        self.assertEqual(
            [run['length'] for run in lead if run.get('kind') == 'mask'],
            [1, 1],
        )
        self.assertNotIn('♕', json.dumps(lead, ensure_ascii=False))
        self.assertNotIn('♛', json.dumps(lead, ensure_ascii=False))
        self.assertIn('∑', json.dumps(lead, ensure_ascii=False))

        refreshed = resolve_puzzle_payload({
            'wiki_title': 'Фигуры',
            'body_text': '♕',
            'splitter_version': CENSORLY_SPLITTER_VERSION - 1,
        })
        self.assertEqual(refreshed['body_tokens'][0]['kind'], 'symbol')


class CensorlyEngineTests(TestCase):
    def test_guess_response_compacts_unchanged_article(self):
        from games.views.censorly_views import _compact_play_response

        result = {
            'status': 'hit',
            'title_tokens': [{'id': 1, 'revealed': False}],
            'body_tokens': [
                {'id': 2, 'revealed': True, 'text': 'слово'},
                {'id': 3, 'revealed': False},
            ],
            'newly_revealed': [2],
            'guesses': [{'word': 'слово', 'token_ids': [2]}],
        }
        compact = _compact_play_response(result)
        self.assertNotIn('title_tokens', compact)
        self.assertNotIn('body_tokens', compact)
        self.assertEqual(compact['state_delta']['token_updates'], [result['body_tokens'][0]])
        self.assertEqual(_compact_play_response({'status': 'won'}), {'status': 'won'})

    def test_hub_progress_reports_partial_and_solved_states(self):
        game, task, _hash, puzzle = _make_puzzle_task(
            title='Кот', body='Кот сидит у окна.',
        )
        ChainTaskState.objects.create(
            task=task, game=game, anon_key='censorly-hub',
            game_mode='general',
            state=json.dumps({'guesses': [{'word': 'дом', 'hits': 0}], 'won': False}),
        )
        partial = hub_progress_for_actor(
            game=game, numbers_and_tasks=[(3, task)], anon_key='censorly-hub',
        )
        self.assertEqual(partial[3]['row_class'], 'new-task--partial')
        self.assertFalse(partial[3]['is_solved'])

        state = ChainTaskState.objects.get(task=task, game=game, anon_key='censorly-hub')
        state.state = json.dumps({
            'guesses': [{'word': 'кот', 'hits': 1}],
            'revealed_lemmas': ['кот'],
            'won': True,
        })
        state.save(update_fields=['state'])
        solved = hub_progress_for_actor(
            game=game, numbers_and_tasks=[(3, task)], anon_key='censorly-hub',
        )
        self.assertEqual(solved[3]['row_class'], 'new-task--solved')
        self.assertTrue(solved[3]['is_solved'])

    def test_reset_progress_returns_deleted_attempts_atomically(self):
        game, task, _hash, _puzzle = _make_puzzle_task()
        Attempt.manager.create(
            task=task, game=game, anon_key='censorly-reset', text='one',
            status='Ok', points=1,
        )
        ChainTaskState.objects.create(
            task=task, game=game, anon_key='censorly-reset',
            game_mode='general', state='{}',
        )

        with patch(
            'games.targeted_completion_reconciliation.reconcile_task_group_actors',
        ) as reconcile:
            deleted = reset_progress(game=game, task=task, anon_key='censorly-reset')

        self.assertEqual(deleted, 1)
        self.assertFalse(Attempt.manager.filter(task=task).exists())
        self.assertFalse(ChainTaskState.objects.filter(task=task).exists())
        reconcile.assert_called_once_with(
            game_id=game.pk,
            task_group_id=task.task_group_id,
            actor_keys={(None, None, 'censorly-reset')},
        )

    def test_stopwords_and_tokenize(self):
        self.assertTrue(is_stop_word('в'))
        self.assertTrue(is_stop_word('И'))
        self.assertTrue(is_stop_word('что'))
        self.assertTrue(is_stop_word('чтобы'))
        self.assertTrue(is_stop_word('то'))
        self.assertFalse(is_stop_word('Москва'))
        for pronoun in ('нем', 'нём', 'он', 'него', 'себя', 'этот', 'кто', 'чьё', 'все', 'это'):
            self.assertFalse(is_stop_word(pronoun), pronoun)
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

    def test_yura_opens_yuryevich(self):
        from games.censorly.lexical.semantics import explain

        self.assertEqual(explain('Юра', 'Юрьевич'), 'proper')

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

    def test_guess_ignores_autocorrect_punctuation(self):
        game, task, _hash, puzzle = _make_puzzle_task(
            title='Кот',
            body='Кот сидит на окне. Собака спит.',
        )
        user = User.objects.create_user('cz_punct', password='x')
        hit = apply_guess(game=game, task=task, word='«Собака.» ', user=user)
        self.assertEqual(hit['status'], 'hit')
        self.assertEqual(hit['guess_word'], 'собака')

        hyphen = apply_guess(game=game, task=task, word='кот-воркот', user=user)
        self.assertEqual(hyphen['status'], 'invalid')
        self.assertIn('дефис', hyphen['error'])

    def test_ibn_stays_masked_until_guessed(self):
        from django.test import override_settings

        game, task, _hash, puzzle = _make_puzzle_task(
            title='Ибн Сина',
            body='Сабит ибн Курра. ИБН.',
        )
        user = User.objects.create_user('cz_ibn', password='x')
        with override_settings(CENSORLY_LEXICAL_RESOLVER=True):
            initial = get_play_state(game=game, task=task, user=user)
            result = apply_guess(game=game, task=task, word='ибн', user=user)

        self.assertEqual(result['status'], 'hit')
        self.assertEqual(result['attempts'], 1)
        for section in ('title_tokens', 'body_tokens'):
            for token in puzzle[section]:
                if normalize_surface(token['surface']) != 'ибн':
                    continue
                with self.subTest(section=section, surface=token['surface']):
                    before = next(t for t in initial[section] if t['id'] == token['id'])
                    self.assertFalse(before['revealed'])
                    self.assertNotIn('text', before)
                    after = next(t for t in result[section] if t['id'] == token['id'])
                    self.assertTrue(after['revealed'])
                    self.assertEqual(after['text'], token['surface'])

    def test_selected_particles_stay_masked_until_guessed(self):
        from django.test import override_settings

        user = User.objects.create_user('cz_hidden_particles', password='x')
        for word in ('бин', 'дель', 'ле', 'эль', 'ин', 'герне', 'мол', 'ка', 'ок', 'окей'):
            with self.subTest(word=word), override_settings(CENSORLY_LEXICAL_RESOLVER=True):
                game, task, _hash, _puzzle = _make_puzzle_task(
                    title=f'Корзина {word}',
                    body=f'{word} {word.title()} {word.upper()}',
                )
                initial = get_play_state(game=game, task=task, user=user)
                content = [t for t in initial['body_tokens'] if t['kind'] == 'content']
                self.assertEqual(len(content), 3)
                for token in content:
                    self.assertFalse(token['revealed'])
                    self.assertNotIn('text', token)

                result = apply_guess(game=game, task=task, word=word, user=user)

                self.assertEqual(result['status'], 'hit')
                self.assertEqual(result['attempts'], 1)
                opened = [t for t in result['body_tokens'] if t['kind'] == 'content']
                self.assertTrue(all(t['revealed'] for t in opened))
                self.assertEqual([t['text'] for t in opened], [word, word.title(), word.upper()])

    def test_initially_open_stop_word_is_rejected_without_attempt(self):
        game, task, _hash, _puzzle = _make_puzzle_task(
            title='Кот',
            body='Кот и собака живут в доме.',
        )
        user = User.objects.create_user('cz_stop_word', password='x')

        for word in ('И', 'в', 'или'):
            result = apply_guess(game=game, task=task, word=word, user=user)
            self.assertEqual(result['status'], 'initially_open')
            self.assertEqual(result['guess_word'], word.lower())
            self.assertEqual(result['error'], 'Это слово уже открыто изначально')
            self.assertEqual(result['attempts'], 0)
        self.assertFalse(Attempt.manager.filter(task=task, user=user).exists())
        self.assertFalse(ChainTaskState.objects.filter(task=task, user=user).exists())

    def test_uppercase_po_can_be_guessed_despite_lowercase_preposition(self):
        game, task, _hash, puzzle = _make_puzzle_task(
            title='ПО',
            body='ПО и по.',
        )
        # The acronym is an exact, closed reading; the lowercase preposition
        # stays visible and must not reject the same input before matching.
        user = User.objects.create_user('cz_po_acronym', password='x')

        result = apply_guess(game=game, task=task, word='по', user=user)

        self.assertEqual(result['status'], 'won')
        self.assertGreaterEqual(result['hits'], 1)
        self.assertEqual(result['attempts'], 1)


class CensorlyAccessTests(TestCase):
    def setUp(self):
        self.game, self.task, self.share_hash, self.puzzle = _make_puzzle_task()
        # Soft-launch gate ignores is_ready; production migration sets ready=True.
        self.game.is_ready = True
        self.game.save(update_fields=['is_ready'])
        self.staff = User.objects.create_user('cz_staff', password='x', is_staff=True)
        self.plain = User.objects.create_user('cz_plain', password='x', is_staff=False)
        self.client = Client()

    def test_word_match_report_requires_open_target_and_stores_diagnostic(self):
        self.client.force_login(self.staff)
        opened = apply_guess(
            game=self.game, task=self.task, word='столица', user=self.staff,
        )
        self.assertEqual(opened['status'], 'hit')
        state = get_play_state(game=self.game, task=self.task, user=self.staff)
        target = next(
            token for token in state['body_tokens']
            if token.get('revealed') and token.get('lemma') == 'столица'
        )
        with patch('games.telegram.admin_alert_events.publish_admin_alert', return_value=True) as publish, \
                patch('games.telegram.signals.notify_new_bug_report') as notify, \
                self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f'/censorly/word-report/{self.task.id}/',
                data=json.dumps({
                    'reason': 'FALSE_POSITIVE',
                    'target_surface': target['text'],
                    'token_id': target['id'],
                    'other_word': 'столица',
                    'comment': 'Проверка пользовательского report flow',
                    'game_id': CENSORLY_GAME_ID,
                }),
                content_type='application/json',
                HTTP_X_INTEROVES_GAME=CENSORLY_GAME_ID,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        report = BugReport.objects.get(pk=response.json()['report_id'])
        self.assertEqual(report.report_type, BugReport.REPORT_TYPE_CENSORLY_WORD_MATCH)
        self.assertEqual(report.metadata['reason'], 'FALSE_POSITIVE')
        self.assertEqual(report.metadata['target_surface'], target['text'])
        # The worker fills the diagnostic in and notifies the admin chat.
        self.assertEqual(report.metadata['diagnostic'], {'status': 'pending'})
        publish.assert_called_once()
        self.assertEqual(publish.call_args.kwargs['payload'], {'report_id': report.pk})
        notify.assert_not_called()

    def test_word_match_report_rejects_closed_target_and_missing_other_word(self):
        self.client.force_login(self.staff)
        state = get_play_state(game=self.game, task=self.task, user=self.staff)
        target = next(token for token in state['body_tokens'] if not token.get('revealed'))
        response = self.client.post(
            f'/censorly/word-report/{self.task.id}/',
            data=json.dumps({
                'reason': 'FALSE_NEGATIVE',
                'target_surface': target.get('text') or 'скрытое',
                'token_id': target['id'],
                'game_id': CENSORLY_GAME_ID,
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_GAME=CENSORLY_GAME_ID,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(BugReport.objects.filter(report_type=BugReport.REPORT_TYPE_CENSORLY_WORD_MATCH).exists())

    def test_plain_user_gets_public_random_page(self):
        url = f'/censorly/r/{self.share_hash}/'
        self.client.force_login(self.plain)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Случайная игра')
        self.assertContains(resp, '/subscription/')

    def test_staff_can_play_and_guess(self):
        self.client.force_login(self.staff)
        tags = dict(self.task.tags)
        payload = dict(tags[CENSORLY_TAGS_KEY])
        payload['fetched_at'] = '2026-10-05T01:30:00+00:00'
        tags[CENSORLY_TAGS_KEY] = payload
        self.task.tags = tags
        self.task.save(update_fields=['tags'])
        play = self.client.get(f'/censorly/r/{self.share_hash}/')
        self.assertEqual(play.status_code, 200)
        self.assertContains(play, 'censorly-root')
        self.assertContains(play, 'id="censorly-fetched"')
        match = re.search(
            r'<script id="censorly-bootstrap" type="application/json">(.*?)</script>',
            play.content.decode(),
        )
        self.assertIsNotNone(match)
        bootstrap = json.loads(match.group(1))
        self.assertEqual(
            bootstrap.get('wiki_fetched_label'),
            'Версия статьи в Википедии на момент 5 октября 2026 года',
        )
        self.assertContains(play, 'https://redactle.net')
        self.assertContains(play, 'мы благодарны им за идею цензурок')
        self.assertContains(play, 'aria-pressed="true"')
        self.assertContains(play, 'placeholder="Слово"')
        self.assertNotContains(play, 'Полная статья на Википедии')
        self.assertNotContains(play, 'id="censorly-attempts"')

        context_match = re.search(
            r'id="censorly-root"[^>]*data-context-token="([^"]+)"',
            play.content.decode(),
        )
        self.assertIsNotNone(context_match)
        context_token = unescape(context_match.group(1))
        timing_context_match = re.search(
            r'data-gameplay-context="([^"]+)"', play.content.decode(),
        )
        self.assertIsNotNone(timing_context_match)
        timing_context_token = unescape(timing_context_match.group(1))
        session_id = str(uuid4())
        timing = self.client.post(
            f'/censorly/r/{self.share_hash}/timing/',
            data=json.dumps({
                'action': 'start',
                'session_id': session_id,
                'event_id': str(uuid4()),
                'seq': 1,
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=timing_context_token,
        )
        self.assertEqual(timing.status_code, 200)

        guess = self.client.post(
            f'/censorly/r/{self.share_hash}/guess/',
            data=json.dumps({'word': 'столица', 'timing_session_id': session_id}),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=context_token,
        )
        self.assertEqual(guess.status_code, 200)
        data = guess.json()
        self.assertIn(data['status'], ('hit', 'miss', 'won'))
        solved_data = data
        if data['status'] != 'won':
            self.assertNotIn('title_tokens', data)
            self.assertNotIn('body_tokens', data)
            self.assertIn('state_delta', data)
            solved = self.client.post(
                f'/censorly/r/{self.share_hash}/guess/',
                data=json.dumps({
                    'word': self.puzzle['wiki_title'],
                    'timing_session_id': session_id,
                }),
                content_type='application/json',
                HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=context_token,
            )
            self.assertEqual(solved.status_code, 200)
            solved_data = solved.json()
        self.assertEqual(solved_data['status'], 'won')
        self.assertTrue(solved_data['daily_timing']['completed'])
        self.assertIsNotNone(solved_data['daily_timing']['frozen_ms'])

    def test_hint_response_carries_only_changed_tokens(self):
        from games.censorly.redact import title_opening_lemmas

        self.client.force_login(self.staff)
        title_lemmas = title_opening_lemmas(self.puzzle)
        target = next(
            token for token in self.puzzle['body_tokens']
            if token.get('kind') == 'content'
            and token.get('lemma') not in title_lemmas
            and token.get('initially_open') is not True
        )

        response = self.client.post(
            f'/censorly/r/{self.share_hash}/hint/',
            data=json.dumps({'token_id': target['id']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.json())
        data = response.json()
        self.assertEqual(data['status'], 'hint')
        self.assertNotIn('title_tokens', data)
        self.assertNotIn('body_tokens', data)
        updated = {token['id'] for token in data['state_delta']['token_updates']}
        self.assertIn(target['id'], updated)
        self.assertEqual(updated, set(data['newly_revealed']))

    def test_initially_open_word_api_does_not_record_attempt(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            f'/censorly/r/{self.share_hash}/guess/',
            data=json.dumps({'word': 'и'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'initially_open')
        self.assertEqual(data['error'], 'Это слово уже открыто изначально')
        self.assertEqual(data['state_delta']['attempts'], 0)
        self.assertFalse(Attempt.manager.filter(task=self.task, user=self.staff).exists())
        self.assertFalse(
            ChainTaskState.objects.filter(task=self.task, user=self.staff).exists()
        )

    def test_hub_is_public_when_ready(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get('/censorly/').status_code, 200)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/censorly/').status_code, 200)

    def test_hub_renders_calendar_and_actor_progress(self):
        link = GameTaskGroup.objects.get(task_group=self.task.task_group)
        RandomCensorlyGame.objects.filter(task_group=self.task.task_group).delete()
        link.number = '1'
        link.name = 'Цензурка #1'
        link.save(update_fields=['number', 'name'])
        self.game.tags = {'censorly_publish_start': '2026-10-01T00:00:00+03:00'}
        self.game.save(update_fields=['tags'])
        ChainTaskState.objects.create(
            task=self.task, game=self.game, user=self.staff,
            game_mode='general',
            state=json.dumps({'guesses': [{'word': 'кот', 'hits': 1}], 'won': True}),
        )

        self.client.force_login(self.staff)
        response = self.client.get('/censorly/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'daily-archive-censorly')
        self.assertContains(response, '🔄 1 попытка')
        self.assertContains(response, 'data-fully-solved="1"')

    def test_solved_archive_row_stays_open_when_club_archive_is_enabled(self):
        from datetime import timedelta
        from django.utils import timezone

        link = GameTaskGroup.objects.get(task_group=self.task.task_group)
        RandomCensorlyGame.objects.filter(task_group=self.task.task_group).delete()
        link.number = '1'
        link.name = 'Цензурка #1'
        link.save(update_fields=['number', 'name'])
        tags = dict(self.game.tags or {})
        tags['censorly_public'] = True
        tags['censorly_publish_start'] = (
            f'{(timezone.localdate() - timedelta(days=1)).isoformat()}T00:00:00+03:00'
        )
        self.game.tags = tags
        self.game.save(update_fields=['tags'])
        ChainTaskState.objects.create(
            task=self.task, game=self.game, user=self.plain,
            game_mode='general',
            state=json.dumps({'guesses': [{'word': 'кот', 'hits': 1}], 'won': True}),
        )
        self.client.force_login(self.plain)

        with patch('games.club_access.scheduled_number_requires_club', return_value=True), \
             patch('games.club_access.user_can_access_scheduled_number', return_value=False):
            response = self.client.get('/censorly/')

        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.context['rows'] if row['number'] == 1)
        self.assertTrue(row['is_fully_solved'])
        self.assertFalse(row['is_archive_locked'])

    def test_hub_hides_unpublished_slots_during_soft_launch(self):
        link = GameTaskGroup.objects.get(task_group=self.task.task_group)
        link.number = '1'
        link.name = 'Цензурка #1'
        link.save(update_fields=['number', 'name'])
        self.game.tags = {'censorly_publish_start': '2026-10-30T00:00:00+03:00'}
        self.game.save(update_fields=['tags'])

        self.client.force_login(self.staff)
        response = self.client.get('/censorly/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'censorly-1')
        self.assertContains(response, 'Случайная игра')


class CensorlyPuzzleStorageTests(TestCase):
    def test_puzzle_roundtrip_in_tags(self):
        _game, task, _h, puzzle = _make_puzzle_task()
        loaded = puzzle_from_task(task)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['wiki_title'], puzzle['wiki_title'])
        self.assertEqual(len(loaded['title_tokens']), len(puzzle['title_tokens']))
        self.assertEqual(loaded['body_text'], puzzle['body_text'])
        self.assertEqual(loaded['splitter_version'], puzzle['splitter_version'])

    def test_saved_puzzle_without_text_resplits_endings(self):
        from games.censorly import CENSORLY_SPLITTER_VERSION
        from games.censorly.tokenize import HEADING_END, HEADING_LEVEL_SEP, HEADING_START

        title = 'Тактика (военное дело)'
        body = (
            f'Тактика изучает дело армии.\n'
            f'{HEADING_START}2{HEADING_LEVEL_SEP}История города{HEADING_END}\n'
            'Дальше одно сражение.'
        )
        _game, task, _h, puzzle = _make_puzzle_task(title=title, body=body)
        stale_tokens = []
        for key in ('title_tokens', 'body_tokens'):
            bare = []
            for tok in puzzle[key]:
                item = {k: v for k, v in tok.items() if k not in ('ending', 'stem_length')}
                bare.append(item)
            stale_tokens.append(bare)
        task.tags = {
            CENSORLY_TAGS_KEY: {
                'wiki_title': title,
                'wiki_pageid': 70183,
                'truncated': False,
                'title_tokens': stale_tokens[0],
                'body_tokens': stale_tokens[1],
            },
        }
        task.save(update_fields=['tags'])

        loaded = puzzle_from_task(task)
        title_endings = {
            tok['surface']: tok.get('ending') or ''
            for tok in loaded['title_tokens']
            if tok.get('kind') == 'content'
        }
        self.assertEqual(title_endings['Тактика'], 'а')
        self.assertEqual(title_endings['дело'], 'о')
        self.assertEqual(title_endings['военное'], 'ое')
        heading = [
            tok['surface']
            for tok in loaded['body_tokens']
            if tok.get('in_heading') and tok.get('kind') == 'content'
        ]
        self.assertEqual(heading, ['История', 'города'])
        self.assertEqual(loaded['splitter_version'], CENSORLY_SPLITTER_VERSION)
        self.assertIn('Тактика изучает дело', loaded['body_text'])
        task.refresh_from_db()
        stored = task.tags[CENSORLY_TAGS_KEY]
        self.assertEqual(stored['splitter_version'], CENSORLY_SPLITTER_VERSION)
        self.assertEqual(stored['wiki_pageid'], 70183)
        self.assertIsNone(stored.get('wiki_revid'))

    def test_stale_version_resplits_stored_text(self):
        _game, task, _h, puzzle = _make_puzzle_task(
            title='Тактика',
            body='Тактика ведёт дело.',
        )
        payload = dict(task.tags[CENSORLY_TAGS_KEY])
        payload['splitter_version'] = 0
        payload['body_text'] = 'Тактика ведёт дело.'
        for tok in payload['title_tokens']:
            tok.pop('ending', None)
            tok.pop('stem_length', None)
        task.tags = {CENSORLY_TAGS_KEY: payload}
        task.save(update_fields=['tags'])

        loaded = puzzle_from_task(task)
        title = next(tok for tok in loaded['title_tokens'] if tok['surface'] == 'Тактика')
        self.assertEqual(title.get('ending'), 'а')
        self.assertEqual(loaded['body_text'], 'Тактика ведёт дело.')

    def test_current_split_cache_is_not_rebuilt(self):
        _game, task, _h, _puzzle = _make_puzzle_task(
            title='Тактика',
            body='Тактика ведёт дело.',
        )
        payload = dict(task.tags[CENSORLY_TAGS_KEY])
        for tok in payload['title_tokens']:
            if tok.get('surface') == 'Тактика':
                tok.pop('ending', None)
                tok.pop('stem_length', None)
        task.tags = {CENSORLY_TAGS_KEY: payload}
        task.save(update_fields=['tags'])

        loaded = puzzle_from_task(task)
        title = next(tok for tok in loaded['title_tokens'] if tok['surface'] == 'Тактика')
        self.assertNotIn('ending', title)

    def test_current_puzzle_payload_is_cached_until_task_revision_changes(self):
        from games.censorly.play import _cached_current_puzzle_payload

        _game, task, _h, _puzzle = _make_puzzle_task(
            title='Тактика', body='Тактика ведёт дело.',
        )
        _cached_current_puzzle_payload.cache_clear()
        with self.assertNumQueries(1):
            first = puzzle_from_task(task)
        with self.assertNumQueries(0):
            second = puzzle_from_task(task)
        self.assertEqual(first, second)

        payload = dict(task.tags[CENSORLY_TAGS_KEY])
        payload['body_text'] = 'Тактика ведёт новое дело.'
        task.tags = {CENSORLY_TAGS_KEY: payload}
        task.save(update_fields=['tags'])
        with self.assertNumQueries(1):
            refreshed = puzzle_from_task(task)
        self.assertEqual(refreshed['body_text'], 'Тактика ведёт новое дело.')


class CensorlyWikiHelperTests(TestCase):
    def test_fetch_article_requests_html_extract_and_converts_its_lists(self):
        from games.censorly.wiki import fetch_article

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    'query': {
                        'pages': {
                            '42': {
                                'pageid': 42,
                                'lastrevid': 123,
                                'title': 'Пример',
                                'extract': (
                                    '<p>' + ('Достаточно длинный вводный абзац. ' * 20)
                                    + '</p><ul><li>Первый пункт.</li>'
                                    '<li>Второй пункт.</li></ul>'
                                ),
                            },
                        },
                    },
                }

        class Session:
            params = None

            def get(self, _url, *, params, headers, timeout):
                self.params = params
                return Response()

        session = Session()
        article = fetch_article('Пример', session=session)

        self.assertNotIn('explaintext', session.params)
        self.assertEqual(session.params['exsectionformat'], 'wiki')
        self.assertIn('• Первый пункт.\u2028• Второй пункт.', article.extract)
        self.assertEqual(article.revid, 123)

    def test_html_extract_keeps_math_lists_tables_and_drops_reference_marks(self):
        from games.censorly.wiki import _html_extract_to_text, clean_wiki_extract

        raw = (
            '<p>Формула <math alttext="{\\displaystyle x = 1}">'
            '<semantics><annotation encoding="application/x-tex">ignored'
            '</annotation></semantics></math>'
            '<sup class="reference"><a>[7]</a></sup></p>'
            '<ol><li>Первый</li><li>Второй</li></ol>'
            '<table><tr><th>Имя</th><th>Значение</th></tr>'
            '<tr><td>А</td><td>1</td></tr></table>'
        )

        marked = _html_extract_to_text(raw)
        self.assertNotIn('[7]', marked)
        self.assertIn('{\\displaystyle x = 1}', marked)
        self.assertIn('# Первый\n# Второй', marked)
        self.assertIn('| Имя | Значение', marked)
        self.assertIn('| А | 1', marked)
        cleaned = clean_wiki_extract(marked, mark_formulas=True)
        self.assertIn('1. Первый\u20282. Второй', cleaned)
        self.assertIn('Имя | Значение', cleaned)
        self.assertIn('А | 1', cleaned)
        self.assertIn('Имя | Значение\u2028А | 1', cleaned)

    def test_html_extract_keeps_large_table_rows_and_mathml_alttext(self):
        from games.censorly.wiki import _html_extract_to_text, clean_wiki_extract

        rows = ''.join(
            f'<tr><td><b>{index}</b></td><td>Элемент {index}</td>'
            f'<td><math alttext="{{\\displaystyle x_{index} = {index}}}">'
            '<semantics><mi>x</mi><annotation>ignored</annotation></semantics>'
            '</math></td></tr>'
            for index in range(1, 121)
        )
        raw = '<table><tr><th>№</th><th>Название</th><th>Формула</th></tr>' + rows + '</table>'

        marked = _html_extract_to_text(raw)
        self.assertEqual(sum(line.startswith('| ') for line in marked.splitlines()), 121)
        self.assertIn('| 120 | Элемент 120 | {\\displaystyle x_120 = 120}', marked)
        self.assertNotIn('ignored', marked)

        cleaned = clean_wiki_extract(marked, mark_formulas=True)
        self.assertIn('Элемент 1 |', cleaned)
        # TeX subscripts consume one unbraced atom: x_120 means x₁ followed by 20.
        self.assertIn('x₁20 = 120', cleaned)

    def test_root_article_html_extract_preserves_real_list_items(self):
        from games.censorly.tokenize import tokenize_text
        from games.censorly.wiki import _html_extract_to_text, clean_wiki_extract

        raw_html = _load_censorly_testdata('root_functions_extract.html')
        marked = _html_extract_to_text(raw_html)
        self.assertIn('== Основные функции корня ==', marked)
        self.assertIn(
            '* Закрепление растения в субстрате.\n'
            '* Всасывание, проведение воды и минеральных веществ.\n'
            '* Запас питательных веществ в главном корне.',
            marked,
        )

        cleaned = clean_wiki_extract(marked)
        self.assertIn(
            '• Закрепление растения в субстрате.\u2028'
            '• Всасывание, проведение воды и минеральных веществ.\u2028'
            '• Запас питательных веществ в главном корне.',
            cleaned,
        )
        tokens = tokenize_text(cleaned)
        self.assertEqual(
            sum(1 for token in tokens if token['kind'] == 'space' and '\u2028' in token['surface']),
            5,
        )

    def test_wikipedia_bullet_lists_keep_each_item_on_its_own_line(self):
        from games.censorly.tokenize import tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        # Snapshot-shaped extract from «Корень»: TextExtracts emits each wiki
        # bullet as a line, but prose line wraps should still collapse.
        raw = (
            'Обычный абзац, перенесённый\n'
            'на следующую строку.\n\n'
            '* Закрепление растения в субстрате.\n'
            '* Всасывание, проведение воды и минеральных веществ.\n'
            '* Запас питательных веществ в главном корне.\n'
            'Следующий абзац начинается здесь.'
        )

        cleaned = clean_wiki_extract(raw)
        self.assertEqual(
            cleaned,
            'Обычный абзац, перенесённый на следующую строку.\n\n'
            '• Закрепление растения в субстрате.\u2028'
            '• Всасывание, проведение воды и минеральных веществ.\u2028'
            '• Запас питательных веществ в главном корне.\n'
            'Следующий абзац начинается здесь.',
        )
        tokens = tokenize_text(cleaned)
        self.assertEqual(
            sum(1 for token in tokens if token['kind'] == 'space' and '\n' in token['surface']),
            2,
        )

    def test_wikipedia_ordered_nested_and_definition_lists_are_formatted(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            '# Первый пункт\n'
            '## Вложенный пункт\n'
            '# Второй пункт\n\n'
            '* Внешний пункт\n'
            '** Вложенный пункт\n'
            '; Термин\n'
            ': его определение'
        )

        self.assertEqual(
            clean_wiki_extract(raw),
            '1. Первый пункт\u2028'
            '  1. Вложенный пункт\u2028'
            '2. Второй пункт\n\n'
            '• Внешний пункт\u2028'
            '  • Вложенный пункт\u2028'
            'Термин — его определение',
        )

    def test_wikipedia_mixed_markers_and_wrapped_list_items_are_bounded(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            '*# Смешанный нумерованный подпункт\n'
            '*# Следующий подпункт\n'
            '#* Вложенный маркированный пункт\n'
            '# Возврат к верхнему уровню\n'
            '* Очень\n'
            'длинное продолжение того же пункта.\n'
            'Следующий абзац начинается отдельно.\n'
            '********* Чрезмерная вложенность'
        )

        self.assertEqual(
            clean_wiki_extract(raw),
            '  1. Смешанный нумерованный подпункт\u2028'
            '  2. Следующий подпункт\u2028'
            '  • Вложенный маркированный пункт\u2028'
            '1. Возврат к верхнему уровню\u2028'
            '• Очень длинное продолжение того же пункта.\n'
            'Следующий абзац начинается отдельно.\n'
            '          • Чрезмерная вложенность',
        )

    def test_lead_editorial_notes_remain_separate_paragraphs(self):
        from games.censorly.wiki import clean_wiki_extract

        # ru.wikipedia.org snapshots: Троянский конь (page 85276,
        # revision 154264746) and Дона (page 8582684, revision 151083100).
        extracts = [
            (
                'Это статья по античной мифологии. По вредоносным компьютерным '
                'программам см. Троянская программа\n\n'
                'Троянский конь — в древнегреческой мифологии огромный '
                'деревянный конь, с постройкой которого связан один из '
                'финальных эпизодов Троянской войны.'
            ),
            (
                'Дона — многозначный термин:\n\n'
                'Дона — одно из названий лекарства Глюкозамин.\n'
                'Дона — город в Германии, в федеральной земле Саксония.'
            ),
        ]

        for raw in extracts:
            with self.subTest(first_paragraph=raw.splitlines()[0]):
                cleaned = clean_wiki_extract(raw)
                self.assertIn('\n\n', cleaned)
                self.assertEqual(cleaned.count('\n'), 2)

        # Ordinary internal line wraps still join into continuous prose.
        self.assertEqual(
            clean_wiki_extract('Обычное предложение\n\nпродолжается здесь.'),
            'Обычное предложение продолжается здесь.',
        )

    def test_ramanujan_wikipedia_extract_keeps_the_display_formula_on_one_line(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import _trim_extract, clean_wiki_extract

        # Snapshot from ru.wikipedia.org, page 366175, revision 151975208.
        # TextExtracts includes a vertical MathML fallback and canonical TeX.
        raw = _load_censorly_testdata('ramanujan_first_formula_extract.txt')
        cleaned = clean_wiki_extract(raw)

        expected_formula = (
            '1 + (1)/(1 · 3) + (1)/(1 · 3 · 5) + (1)/(1 · 3 · 5 · 7) + '
            '(1)/(1 · 3 · 5 · 7 · 9) +… + (1)/(1 + (1)/(1 + (2)/(1 + '
            '(3)/(1 + (4)/(1 + (5)/(1 +…)))))) = √((e · π)/(2)).'
        )
        self.assertIn(expected_formula, cleaned)
        self.assertNotIn('1\n +\n', cleaned)
        self.assertNotIn('\n\n', cleaned)
        self.assertEqual(cleaned.count('\n'), 1)

        marked, _truncated = _trim_extract(raw, mark_formulas=True)
        formula = expected_formula
        self.assertIn(f'{FORMULA_START}{formula}{FORMULA_END}', marked)
        formula_tokens = [
            token for token in tokenize_text(marked)
            if token['surface'] == formula
        ]
        self.assertEqual(len(formula_tokens), 1)
        self.assertEqual(formula_tokens[0]['kind'], 'punct')

    def test_display_matrix_keeps_its_brackets_and_is_one_token(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \begin{pmatrix} a & b \\ c & d \end{pmatrix}}'
        cleaned = clean_wiki_extract(raw, mark_formulas=True)
        expected = '(a b; c d)'

        self.assertEqual(cleaned, f'{FORMULA_START}{expected}{FORMULA_END}')
        tokens = tokenize_text(cleaned)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]['surface'], expected)

    def test_piecewise_formula_with_strict_inequality_is_one_token(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \begin{cases}0&x<0,\\1&x>0.\end{cases}}'
        cleaned = clean_wiki_extract(raw, mark_formulas=True)
        expected = '{0 x < 0; 1 x > 0.'

        self.assertEqual(cleaned, f'{FORMULA_START}{expected}{FORMULA_END}')
        tokens = tokenize_text(cleaned)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]['surface'], expected)

    def test_strict_comparison_spacing_does_not_change_arrow_rendering(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle x->0}'

        self.assertEqual(clean_wiki_extract(raw), 'x → 0')

    def test_tex_relations_and_arrows_get_readable_spacing(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle x\to0; y\in A; A\cup B}'

        self.assertEqual(clean_wiki_extract(raw), 'x → 0; y ∈ A; A ∪ B')

    def test_less_common_greek_and_empty_set_symbols_are_rendered(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \varkappa+\digamma+\Digamma+\varnothing+\nexists+\nparallel}'

        self.assertEqual(clean_wiki_extract(raw), 'ϰ + ϝ + Ϝ + ∅ + ∄ + ∦')

    def test_text_wrappers_keep_underscores_and_unescape_tex_punctuation(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \text{H_2 \& CO_2, 50\%} + x}'

        self.assertEqual(clean_wiki_extract(raw), 'H_2 & CO_2, 50% + x')

    def test_negative_thin_space_does_not_leave_a_regular_space(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle a \! b + c\, d}'

        self.assertEqual(clean_wiki_extract(raw), 'ab + c d')

    def test_overset_and_underset_render_as_scripts(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            r'{\displaystyle \underset{x\to0}{\lim} f(x); '
            r'\overset{!}{=}}'
        )

        self.assertEqual(clean_wiki_extract(raw), 'lim₍x → 0₎ f(x); =^{!}')

    def test_overbrace_and_underbrace_keep_their_direction_and_annotations(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            r'{\displaystyle \overbrace{a+b}^{n}; '
            r'\underbrace{x+y}_{0}}'
        )
        cleaned = clean_wiki_extract(raw, mark_formulas=True)
        expected = '⏞(a + b)ⁿ; ⏟(x + y)₀'

        self.assertEqual(cleaned, f'{FORMULA_START}{expected}{FORMULA_END}')
        tokens = tokenize_text(cleaned)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]['surface'], expected)

    def test_stackrel_and_modular_notation_render_readably(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        formulas = (
            (r'{\displaystyle x \stackrel{def}{=} y}', 'x =ᵈᵉᶠ y'),
            (r'{\displaystyle a\equiv b\pmod{m}}', 'a ≡ b (mod m)'),
            (r'{\displaystyle a\bmod m}', 'a mod m'),
        )
        for raw, expected in formulas:
            with self.subTest(expected=expected):
                cleaned = clean_wiki_extract(raw, mark_formulas=True)
                self.assertEqual(cleaned, f'{FORMULA_START}{expected}{FORMULA_END}')
                tokens = tokenize_text(cleaned)
                self.assertEqual(len(tokens), 1)
                self.assertEqual(tokens[0]['surface'], expected)

    def test_mathbb_number_sets_keep_double_struck_identity(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle x\in\mathbb{R}; \mathbb{N}; \mathbb{Z}; \mathbb{Q}; \mathbb{C}}'
        expected = 'x ∈ ℝ; ℕ; ℤ; ℚ; ℂ'
        cleaned = clean_wiki_extract(raw, mark_formulas=True)

        self.assertEqual(cleaned, f'{FORMULA_START}{expected}{FORMULA_END}')
        tokens = tokenize_text(cleaned)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]['surface'], expected)

    def test_mathbb_preserves_other_latin_letters_and_digits(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \mathbb{A}+\mathbb{a}+\mathbb{1}}'

        self.assertEqual(clean_wiki_extract(raw), '𝔸 + 𝕒 + 𝟙')

    def test_fraction_followed_by_an_atom_has_an_explicit_product_separator(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \frac{a}{b}x + \dfrac{c}{d}{y}}'

        self.assertEqual(
            clean_wiki_extract(raw),
            '(a)/(b) · x + (c)/(d) · y',
        )

    def test_primitive_fraction_followed_by_an_atom_has_separator(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle {a \over b}x}'

        self.assertEqual(clean_wiki_extract(raw), '(a)/(b) · x')

    def test_fraction_followed_by_tex_symbol_or_formatted_atom_has_separator(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \frac{a}{b}\alpha + \frac{c}{d}\mathbf{x}}'

        self.assertEqual(
            clean_wiki_extract(raw),
            '(a)/(b) · α + (c)/(d) · x',
        )

    def test_mathbb_styles_each_character_in_multi_character_groups(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \mathbb{ABCxyz012}}'

        self.assertEqual(clean_wiki_extract(raw), '𝔸𝔹ℂ𝕩𝕪𝕫𝟘𝟙𝟚')

    def test_absolute_value_and_norm_delimiters_render_as_bars(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \lvert x\rvert; \lVert y\rVert}'
        expected = '|x|; ‖y‖'
        cleaned = clean_wiki_extract(raw, mark_formulas=True)

        self.assertEqual(cleaned, f'{FORMULA_START}{expected}{FORMULA_END}')
        tokens = tokenize_text(cleaned)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]['surface'], expected)

    def test_floor_and_ceiling_delimiters_render_without_inner_spaces(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \lceil x \rceil; \lfloor y \rfloor}'
        expected = '⌈x⌉; ⌊y⌋'
        cleaned = clean_wiki_extract(raw, mark_formulas=True)

        self.assertEqual(cleaned, f'{FORMULA_START}{expected}{FORMULA_END}')
        tokens = tokenize_text(cleaned)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]['surface'], expected)

    def test_array_column_specification_is_not_rendered_as_formula_text(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \begin{array}{|c|r|}a&b\\c&d\end{array}}'

        self.assertEqual(clean_wiki_extract(raw), 'a b; c d')

    def test_set_relation_formulas_are_marked_as_single_tokens(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        formulas = (
            (r'{\displaystyle x \in A}', 'x ∈ A'),
            (r'{\displaystyle x \notin A}', 'x ∉ A'),
            (r'{\displaystyle A \subseteq B}', 'A ⊆ B'),
            (r'{\displaystyle A \cup B}', 'A ∪ B'),
        )
        for raw, expected in formulas:
            with self.subTest(expected=expected):
                cleaned = clean_wiki_extract(raw, mark_formulas=True)
                self.assertEqual(cleaned, f'{FORMULA_START}{expected}{FORMULA_END}')
                tokens = tokenize_text(cleaned)
                self.assertEqual(len(tokens), 1)
                self.assertEqual(tokens[0]['surface'], expected)

    def test_binomial_coefficient_is_rendered_as_combination_notation(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \binom{n}{k}}'
        cleaned = clean_wiki_extract(raw, mark_formulas=True)

        self.assertEqual(cleaned, f'{FORMULA_START}C(n, k){FORMULA_END}')
        tokens = tokenize_text(cleaned)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]['surface'], 'C(n, k)')

    def test_substack_rows_remain_grouped_in_sum_index(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle \sum_{\substack{i=1\\i\ne j}} a_{ij}}'
        cleaned = clean_wiki_extract(raw, mark_formulas=True)
        expected = '∑₍i = 1; i ≠ j₎ aᵢⱼ'

        self.assertEqual(cleaned, f'{FORMULA_START}{expected}{FORMULA_END}')
        tokens = tokenize_text(cleaned)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]['surface'], expected)

    def test_clean_wiki_extract_removes_unindented_chemistry_math_dump(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = _load_censorly_testdata('silver_math_raw.txt')
        cleaned = clean_wiki_extract(raw)

        self.assertIn('Ag + 2HNO', cleaned)
        self.assertIn('⟶', cleaned)
        self.assertIn('NO₂↑', cleaned)
        self.assertIn('H₂O', cleaned)
        self.assertNotIn('uparrow', cleaned)
        self.assertNotIn('\nA\ng\n+', cleaned)

    def test_parse_index_php_title_url(self):
        from games.censorly.wiki import title_from_user_input
        title = title_from_user_input(
            'https://ru.wikipedia.org/w/index.php?title=%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0'
        )
        self.assertEqual(title, 'Москва')


class CensorlyRandomGameTests(TestCase):
    def test_used_titles_reads_censorly_tasks_once(self):
        from games.censorly.random_game import used_censorly_titles

        Task.objects.create(
            number='1', task_type=CENSORLY_TASK_TYPE, answer='  Ответ  ',
            tags={CENSORLY_TAGS_KEY: {
                'wiki_title': 'Название из пазла',
                'source_pool_title': 'Название из пула',
            }},
        )
        Task.objects.create(
            number='2', task_type=CENSORLY_TASK_TYPE, answer='Удалённое',
            is_removed=True,
        )
        Task.objects.create(number='3', task_type='default', answer='Другая задача')

        with self.assertNumQueries(2):  # random-game titles + one Task scan
            titles = used_censorly_titles()

        self.assertTrue({'Ответ', 'Название из пазла', 'Название из пула'} <= titles)
        self.assertNotIn('Удалённое', titles)
        self.assertNotIn('Другая задача', titles)

    def test_invalid_fetched_article_does_not_abort_pool_scan(self):
        from unittest.mock import patch

        from games.censorly.random_game import get_or_create_random_game
        from games.censorly.wiki import WikiArticle, WikiFetchError

        expected = object()
        articles = [
            WikiArticle(title='Плохая статья', pageid=1, extract=''),
            WikiArticle(title='Хорошая статья', pageid=2, extract='body'),
        ]
        with patch('games.censorly.pool_admin.active_pool_titles', return_value=tuple(
            article.title for article in articles
        )), patch('games.censorly.pool_admin.pool_rows', return_value={
            article.title: {
                'title': article.title, 'topics': [], 'types': [],
                'difficulty': 'средне', 'enabled': True,
            }
            for article in articles
        }), patch('games.censorly.random_game._get_game', return_value=object()), \
             patch('games.censorly.random_game.fetch_article', side_effect=articles), \
             patch(
                 'games.censorly.random_game._create_from_article',
                 side_effect=[WikiFetchError('нет угадываемых слов'), expected],
             ):
            result = get_or_create_random_game()

        self.assertIs(result, expected)

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

    def test_clean_wiki_extract_renders_math_displaystyle_dumps(self):
        """TextExtracts MathML glyph lines collapse to readable tokenizable TeX."""
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
        # The duplicate glyph dump is removed; TeX is retained as raw formula text.
        self.assertIn('div h = 0', cleaned)
        self.assertIn('rot h = (1)/(c)', cleaned)
        self.assertIn('(∂ e)/(∂ t)', cleaned)
        self.assertIn('h¯ = B', cleaned)
        self.assertIn('системой из двух уравнений (СГС):', cleaned)
        self.assertIn('где e — микроскопическая напряжённость', cleaned)
        self.assertIn('скорость ρ v соответствует плотности тока', cleaned)
        self.assertIn('магнитной индукцией:', cleaned)
        self.assertIn('==== Токи намагничивания ====', cleaned)
        self.assertIn('токами намагничивания', cleaned)
        # Still works through the full trim pipeline (headings → markers).
        trimmed, truncated = _trim_extract(dirty)
        self.assertFalse(truncated)
        self.assertNotIn('displaystyle', trimmed)
        self.assertIn('rot h = (1)/(c)', trimmed)
        self.assertIn('ρ v соответствует плотности тока', trimmed)

    def test_math_cleanup_keeps_short_ascii_prose_before_formula(self):
        from games.censorly.wiki import _strip_indented_math_lines

        text = (
            'NASA studies planetary systems.\n'
            'A simple relation is x = y.\n'
            '{\\displaystyle x = y}\n'
        )

        cleaned = _strip_indented_math_lines(text)

        self.assertIn('NASA studies planetary systems.', cleaned)
        self.assertIn('A simple relation is x = y.', cleaned)

    def test_latex_fraction_integral_and_relation_are_readable_tokens(self):
        from games.censorly.tokenize import tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            r'{\displaystyle P={\frac {24\times 10^{15}\times 3600}'
            r'{24\times 365\times 3600}}=2,7\times 10^{12}}; '
            r'{\displaystyle \int \limits _{a}^{b}f(x)dx}; '
            r'{\displaystyle H(p,q)\leq E}'
        )
        cleaned = clean_wiki_extract(raw)
        self.assertIn('P = (24 × 10¹⁵ × 3600)/(24 × 365 × 3600) = 2,7 × 10¹²', cleaned)
        self.assertIn('∫ₐᵇ f(x)dx', cleaned)
        self.assertIn('H(p,q) ≤ E', cleaned)
        self.assertNotIn('\\frac', cleaned)
        self.assertNotIn('\\int', cleaned)

        tokens = tokenize_text(cleaned)
        service = {tok['surface']: tok['kind'] for tok in tokens if tok['surface'] in ('∫', '≤')}
        self.assertEqual(service, {'∫': 'stop', '≤': 'stop'})
        self.assertTrue(any(tok['surface'] == 'P' and tok['kind'] == 'content' for tok in tokens))

    def test_cfrac_supports_optional_alignment_argument(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            r'{\displaystyle \cfrac{1}{2}; '
            r'\cfrac[l]{a+b}{c}; \cfrac[r]{x}{y+z}}'
        )

        self.assertEqual(
            clean_wiki_extract(raw),
            '(1)/(2); (a + b)/(c); (x)/(y + z)',
        )

    def test_equation_tags_labels_and_number_suppression_are_removed(self):
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            r'{\displaystyle x=1\tag{1}\label{eq:x}}; '
            r'{\displaystyle y=2\tag*{A}\nonumber}; '
            r'{\displaystyle z=3\notag}'
        )

        self.assertEqual(clean_wiki_extract(raw), 'x = 1; y = 2; z = 3')

    def test_operatorname_separates_function_name_from_its_argument(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            r'{\displaystyle \operatorname{rank}A + '
            r'\operatorname*{argmax}_{x} f(x)}'
        )
        expected = 'rank A + argmaxₓ f(x)'
        cleaned = clean_wiki_extract(raw, mark_formulas=True)

        self.assertEqual(cleaned, f'{FORMULA_START}{expected}{FORMULA_END}')
        tokens = tokenize_text(cleaned)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]['surface'], expected)

    def test_aligned_array_and_cases_drop_trailing_row_separators(self):
        from games.censorly.wiki import clean_wiki_extract

        formulas = (
            (
                r'{\displaystyle \begin{aligned}a&=b\\c&=d\\\end{aligned}}',
                'a = b; c = d',
            ),
            (
                r'{\displaystyle \begin{array}{cc}a&b\\c&d\\\end{array}}',
                'a b; c d',
            ),
            (
                r'{\displaystyle \begin{cases}0&x<0\\1&x\geq0\\\end{cases}}',
                '{0 x < 0; 1 x ≥ 0',
            ),
        )
        for raw, expected in formulas:
            with self.subTest(expected=expected):
                self.assertEqual(clean_wiki_extract(raw), expected)

    def test_negated_and_proportional_relations_render_as_single_tokens(self):
        from games.censorly.tokenize import FORMULA_END, FORMULA_START, tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        formulas = (
            (r'{\displaystyle A\not\subseteq B}', 'A ⊈ B'),
            (r'{\displaystyle A\nsubseteq B}', 'A ⊈ B'),
            (r'{\displaystyle x\not\in A}', 'x ∉ A'),
            (r'{\displaystyle x\propto y}', 'x ∝ y'),
        )
        for raw, expected in formulas:
            with self.subTest(expected=expected, raw=raw):
                cleaned = clean_wiki_extract(raw, mark_formulas=True)
                self.assertEqual(cleaned, f'{FORMULA_START}{expected}{FORMULA_END}')
                tokens = tokenize_text(cleaned)
                self.assertEqual(len(tokens), 1)
                self.assertEqual(tokens[0]['surface'], expected)

    def test_latex_primitive_fractions_spacing_and_hat_are_readable(self):
        from games.censorly.tokenize import tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            r'{\displaystyle {1 \over r^{2}} + {a+b \over c}; '
            r'\Phi_{E,S}~\equiv~\oint_{ S}E; \hat{H}}'
        )
        cleaned = clean_wiki_extract(raw)
        self.assertIn('(1)/(r²) + (a + b)/(c)', cleaned)
        self.assertIn('Φ₍E,S₎ ≡ ∮₍S₎ E', cleaned)
        self.assertIn('Ĥ', cleaned)
        self.assertNotIn('~', cleaned)

        token = next(token for token in tokenize_text(cleaned) if token['surface'] == '≡')
        self.assertEqual(token['kind'], 'stop')

        laplace = clean_wiki_extract(_load_censorly_testdata('pool/laplace.txt'))
        self.assertNotIn(' / ', laplace)
        self.assertIn('(∂²u)/(∂ x²)', laplace)
        gauss = clean_wiki_extract(_load_censorly_testdata('pool/gauss.txt'))
        self.assertNotIn('₍ ', gauss)
        self.assertNotIn('~', gauss)
        self.assertIn('∫₍V₎ 4π ρ dV', gauss)
        self.assertIn('∫₍V₎ ∇ · E', gauss)

    def test_pool_formula_fixtures_keep_equations_instead_of_vertical_glyph_dumps(self):
        from games.censorly.wiki import clean_wiki_extract

        orbital = _load_censorly_testdata('pool/orbital.txt')
        orbital_clean = clean_wiki_extract(orbital)
        self.assertIn('E = −(1/2)', orbital_clean)
        self.assertIn('mₑe⁴', orbital_clean)
        self.assertIn('n²ℏ²', orbital_clean)

        schrodinger = _load_censorly_testdata('pool/schrodinger.txt')
        schrodinger_clean = clean_wiki_extract(schrodinger)
        self.assertIn('∑', schrodinger_clean)
        self.assertIn('∂', schrodinger_clean)
        self.assertIn('√', schrodinger_clean)
        self.assertNotIn('\\displaystyle', schrodinger_clean)

    def test_latex_brackets_arrows_and_relations_render_as_open_math_tokens(self):
        from games.censorly.tokenize import tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            r'{\displaystyle \langle x\rangle '
            r'\Longleftrightarrow \Rightarrow \longrightarrow '
            r'\subseteq \perp \otimes}'
        )
        cleaned = clean_wiki_extract(raw)
        self.assertIn('⟨x⟩ ⟺ ⇒ ⟶ ⊆ ⊥ ⊗', cleaned)

        token_kinds = {
            token['surface']: token['kind']
            for token in tokenize_text(cleaned)
            if token['surface'] in {'⟨', '⟩', '⟺', '⇒', '⟶', '⊆', '⊥', '⊗'}
        }
        self.assertEqual(
            token_kinds,
            {'⟨': 'stop', '⟩': 'stop', '⟺': 'stop', '⇒': 'stop', '⟶': 'stop',
             '⊆': 'stop', '⊥': 'stop', '⊗': 'stop'},
        )

        stat_mech = clean_wiki_extract(_load_censorly_testdata('pool/stat_mech.txt'))
        self.assertIn('⟨f⟩', stat_mech)
        self.assertNotIn('langle', stat_mech)
        self.assertNotIn('rangle', stat_mech)
        self.assertIn('∫₍H(p,q) ≤ E₎ dp dq', stat_mech)

        pythagoras = clean_wiki_extract(_load_censorly_testdata('pool/pythagoras.txt'))
        self.assertIn('⟺ a² + b² = c²', pythagoras)
        self.assertNotIn('Longleftrightarrow', pythagoras)

    def test_latex_accents_and_unavailable_subscripts_stay_compact_and_tokenizable(self):
        from games.censorly.tokenize import tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            r'{\displaystyle \Phi_{E,S} + \dot{q_i} + \overline{N} + \ddot{x}'
            r' + \vec{r} + \vec{\lambda} + \vec{AB} + \overline{E=mc^{2}}}'
        )
        cleaned = clean_wiki_extract(raw)
        self.assertIn('Φ₍E,S₎ + qᵢ˙ + N¯ + x¨ + r⃗ + λ⃗ + vec(AB)', cleaned)
        self.assertIn('overline(E = mc²)', cleaned)

        service = {token['surface']: token['kind'] for token in tokenize_text(cleaned)}
        for symbol in ('₍', '₎', '˙', '¯', '¨'):
            self.assertEqual(service[symbol], 'stop')
        self.assertEqual(service['qᵢ'], 'content')
        self.assertEqual(service['r⃗'], 'content')
        vector_token = next(token for token in tokenize_text(cleaned) if token['surface'] == 'r⃗')
        self.assertEqual(vector_token['length'], 1)

        stat_mech = clean_wiki_extract(_load_censorly_testdata('pool/stat_mech.txt'))
        self.assertIn('qᵢ˙', stat_mech)
        entropy = clean_wiki_extract(_load_censorly_testdata('pool/entropy.txt'))
        self.assertIn('N¯', entropy)
        quantum = clean_wiki_extract(_load_censorly_testdata('pool/quantum.txt'))
        self.assertIn('λ⃗', quantum)
        self.assertIn('X̂', quantum)
        self.assertIn('P̂', quantum)
        self.assertNotIn('hat(X)', quantum)
        self.assertNotIn('hat(P)', quantum)
        self.assertIn('∫₍−∞₎^(+∞) C(k)', quantum)
        schrodinger = clean_wiki_extract(_load_censorly_testdata('pool/schrodinger.txt'))
        self.assertIn('r⃗', schrodinger)

    def test_latex_function_names_are_separated_from_variables_and_arguments(self):
        from games.censorly.tokenize import tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = r'{\displaystyle p_i\log p_i + \sin x + \cos{\frac{a}{R}}}'
        cleaned = clean_wiki_extract(raw)
        self.assertIn('pᵢ log pᵢ + sin x + cos (a)/(R)', cleaned)
        self.assertNotIn('cos ·', cleaned)

        words = [
            token['surface'] for token in tokenize_text(cleaned)
            if token['kind'] == 'content'
        ]
        self.assertIn('pᵢ', words)
        self.assertIn('log', words)
        self.assertIn('sin', words)

        entropy = clean_wiki_extract(_load_censorly_testdata('pool/entropy.txt'))
        self.assertIn('pᵢ log pᵢ', entropy)
        pythagoras = clean_wiki_extract(_load_censorly_testdata('pool/pythagoras.txt'))
        self.assertIn('cos (c)/(R)', pythagoras)

    def test_latex_chemistry_and_text_wrappers_render_without_command_leaks(self):
        from games.censorly.tokenize import tokenize_text
        from games.censorly.wiki import clean_wiki_extract

        raw = (
            r'{\displaystyle {\ce {H2O\ -\ e^{-}->H^{+}{}+{\dot {O}}H}}}; '
            r'{\displaystyle {\ce {4{\dot {O}}H->O2{}+2H2O}}}; '
            r'{\displaystyle {\begin{cases}0&{\mbox{при}}\ H<E,\\'
            r'C&{\mbox{при}}\ H\leq E.\end{cases}}}'
        )
        cleaned = clean_wiki_extract(raw)
        self.assertIn('H₂O − e^(−) → H⁺ + O˙H', cleaned)
        self.assertIn('4O˙H → O₂ + 2H₂O', cleaned)
        self.assertIn('0 при H < E; C при H ≤ E.', cleaned)
        self.assertNotIn('\\ce', cleaned)
        self.assertNotIn('mbox', cleaned)
        self.assertNotIn('.;', cleaned)

        arrow_tokens = [
            token for token in tokenize_text(cleaned)
            if token['surface'] == '→'
        ]
        self.assertEqual(len(arrow_tokens), 2)
        self.assertTrue(all(token['kind'] == 'stop' for token in arrow_tokens))

        photosynthesis = clean_wiki_extract(_load_censorly_testdata('pool/photosynthesis.txt'))
        self.assertIn('H₂O − e^(−) → H⁺ + O˙H', photosynthesis)
        self.assertNotIn('ce H2O', photosynthesis)

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
        self.assertIn('rot h =', cleaned)
        self.assertIn('уравнениями Лоренца', cleaned)
        self.assertIn('ρ v соответствует плотности тока', cleaned)

    def test_mathml_dump_without_canonical_tex_does_not_leave_vertical_garbage(self):
        from games.censorly.wiki import _tex_group, clean_wiki_extract

        raw = _load_censorly_testdata('magnetism_math_raw.txt')
        marker = r'{\displaystyle'
        start = raw.find(marker)
        self.assertGreaterEqual(start, 0)
        group = _tex_group(raw, start)
        self.assertIsNotNone(group)
        mathml_only = raw[:start] + raw[group[1]:]
        cleaned = clean_wiki_extract(mathml_only)

        self.assertIn('описывается системой из двух уравнений (СГС):', cleaned)
        self.assertIn('где e — микроскопическая напряжённость', cleaned)
        self.assertNotIn('rot', cleaned)
        self.assertNotIn('displaystyle', cleaned)
        self.assertNotIn('\n        div\n', cleaned)

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
        body_words = [
            token for token in payload['body_tokens']
            if token.get('kind') == 'content'
            or (token.get('kind') == 'stop' and token.get('length', 0) > 0)
        ]
        body_views = {token['id']: token for token in before['body_tokens']}
        self.assertEqual(before['total_words_count'], len(body_words))
        self.assertEqual(
            before['opened_words_count'],
            sum(bool(body_views[token['id']].get('revealed')) for token in body_words),
        )
        self.assertGreater(before['opened_words_count'], 0)  # «и» is open from the start.
        self.assertTrue(before['truncated'])
        self.assertIsNone(before.get('wiki_pageid'))
        self.assertIsNone(before.get('wiki_title'))
        self.assertEqual(before.get('wiki_fetched_label'), '')
        payload['fetched_at'] = '2026-10-05T01:30:00+00:00'
        dated = public_payload({'revealed_lemmas': [], 'guesses': [], 'won': False}, payload)
        self.assertEqual(
            dated.get('wiki_fetched_label'),
            'Версия статьи в Википедии на момент 5 октября 2026 года',
        )
        after = public_payload(
            {'revealed_lemmas': list(title_content_lemmas(payload)), 'guesses': [], 'won': True},
            payload,
        )
        self.assertEqual(after.get('wiki_pageid'), 12345)
        self.assertEqual(after.get('wiki_title'), 'Кот')

        with_guess = public_payload(
            {'revealed_lemmas': ['кот'], 'guesses': [], 'won': False}, payload,
        )
        guessed_views = {token['id']: token for token in with_guess['body_tokens']}
        self.assertEqual(
            with_guess['opened_words_count'],
            sum(bool(guessed_views[token['id']].get('revealed')) for token in body_words),
        )
        self.assertGreaterEqual(with_guess['opened_words_count'], before['opened_words_count'])

    def test_revealed_title_lemmas_are_treated_as_won_for_legacy_state(self):
        from games.censorly.play import public_payload
        from games.censorly.tokenize import build_puzzle_payload

        payload = build_puzzle_payload(
            wiki_title='Кот', body_text='Кот и собака.', wiki_pageid=12345,
        )
        state = {
            'revealed_lemmas': list(title_content_lemmas(payload)),
            'guesses': [],
            'won': False,
        }
        view = public_payload(state, payload)
        self.assertTrue(view['won'])
        self.assertEqual(view['wiki_pageid'], 12345)


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
        self.assertEqual(b['guess_word'], 'коты')
        self.assertEqual(b['active_guess_index'], 0)
        self.assertTrue(b['active_token_ids'])
        self.assertEqual(b['hits'], 0)
        self.assertFalse(b['won'])
        self.assertEqual(b['attempts'], attempts_after_hit)
        c = apply_guess(game=game, task=task, word='кот', user=user)
        self.assertEqual(c['status'], 'duplicate')
        self.assertEqual(c['active_guess_index'], 0)
        self.assertEqual(c['guess_word'], 'кот')

    def test_already_open_from_hint_returns_token_ids(self):
        game, task, _h, puzzle = _make_puzzle_task(
            title='Заголовок',
            body='Слово спрятано в тексте. Слово повторяется.',
        )
        user = User.objects.create_user('cz_hint_open', password='x')
        token = next(
            tok for tok in puzzle['body_tokens']
            if tok.get('kind') == 'content' and tok.get('lemma') == lemma_of('слово')
        )
        first = apply_hint(game=game, task=task, token_id=token['id'], user=user)
        self.assertEqual(first['status'], 'hint')
        repeated = apply_hint(game=game, task=task, token_id=token['id'], user=user)
        self.assertEqual(repeated['status'], 'already_open')
        self.assertEqual(len(repeated['active_token_ids']), 2)


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
                'rules': None,
                'tournament_rules': None,
                'general_rules': None,
                'section_default_rules': None,
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

    def test_json_endpoints_reject_non_object_payloads(self):
        endpoints = (
            '/support/censorly/reorder/',
            '/support/censorly/from-title/',
            '/support/censorly/reset-progress/',
            '/support/censorly/refetch-text/',
            '/support/censorly/publish-start/',
            '/support/censorly/schedule/create/',
            '/support/censorly/schedule/generate/',
            '/support/censorly/offers/1/reject/',
        )
        for url in endpoints:
            with self.subTest(url=url):
                response = self.client.post(
                    url,
                    data=b'[]',
                    content_type='application/json',
                )

                self.assertEqual(response.status_code, 400)

    def test_numeric_party_identifiers_are_handled_as_bad_requests(self):
        response = self.client.post(
            '/support/censorly/reset-progress/',
            data=json.dumps({'number': 123}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            '/support/censorly/refetch-text/',
            data=json.dumps({'share_hash': 123}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_defer_and_restore_schedule_slot(self):
        from games.support.services.censorly import defer_censorly, restore_censorly

        game, task, _hash, _puzzle = _make_puzzle_task(title='Слот')
        link = GameTaskGroup.objects.get(game=game, task_group=task.task_group)
        RandomCensorlyGame.objects.filter(task_group=task.task_group).delete()
        link.number = '1'
        link.name = 'Цензурка #1'
        link.save(update_fields=['number', 'name'])
        deferred_rows = defer_censorly(link.pk)
        link.refresh_from_db()
        self.assertTrue(link.is_deferred)
        self.assertEqual(link.deferred_number, '1')
        self.assertEqual(deferred_rows[0].number, 1)
        self.assertTrue(deferred_rows[0].is_deferred)

        restored_rows = restore_censorly(link.pk)
        link.refresh_from_db()
        self.assertFalse(link.is_deferred)
        self.assertEqual(link.number, '1')
        self.assertFalse(restored_rows[0].is_deferred)

    def test_defer_and_restore_endpoints_return_schedule_rows(self):
        game, task, _hash, _puzzle = _make_puzzle_task(title='HTTP слот')
        link = GameTaskGroup.objects.get(game=game, task_group=task.task_group)
        RandomCensorlyGame.objects.filter(task_group=task.task_group).delete()
        link.number = '1'
        link.name = 'Цензурка #1'
        link.save(update_fields=['number', 'name'])

        response = self.client.post(f'/support/censorly/{link.pk}/defer/', data={})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertTrue(response.json()['rows'][0]['is_deferred'])

        response = self.client.post(f'/support/censorly/{link.pk}/restore/', data={})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertFalse(response.json()['rows'][0]['is_deferred'])

    def test_deferred_queue_has_independent_order_and_restore_appends(self):
        from games.support.services.censorly import defer_censorly, restore_censorly

        game = Game.objects.get(pk=CENSORLY_GAME_ID)
        links = []
        for index in range(3):
            _game, task, _hash, _puzzle = _make_puzzle_task(
                title=f'Очередь {index}',
            )
            link = GameTaskGroup.objects.get(game=game, task_group=task.task_group)
            RandomCensorlyGame.objects.filter(task_group=task.task_group).delete()
            link.number = str(index + 1)
            link.name = f'Цензурка #{index + 1}'
            link.save(update_fields=['number', 'name'])
            links.append(link)

        with patch(
            'games.support.services.censorly.is_censorly_number_published',
            return_value=False,
        ):
            defer_censorly(links[1].pk)
            defer_censorly(links[2].pk)

            links[1].refresh_from_db()
            links[2].refresh_from_db()
            self.assertEqual(links[1].deferred_number, '1')
            self.assertEqual(links[2].deferred_number, '2')

            restore_censorly(links[1].pk)

        links[1].refresh_from_db()
        links[2].refresh_from_db()
        self.assertFalse(links[1].is_deferred)
        self.assertEqual(links[1].number, '2')
        self.assertTrue(links[2].is_deferred)
        self.assertEqual(links[2].deferred_number, '1')

    def test_defer_rejects_already_deferred_slot(self):
        from games.support.services.censorly import (
            CensorlySupportError,
            defer_censorly,
        )

        game, task, _hash, _puzzle = _make_puzzle_task(title='Повторная очередь')
        link = GameTaskGroup.objects.get(game=game, task_group=task.task_group)
        RandomCensorlyGame.objects.filter(task_group=task.task_group).delete()
        link.number = '1'
        link.save(update_fields=['number'])

        with patch(
            'games.support.services.censorly.is_censorly_number_published',
            return_value=False,
        ):
            defer_censorly(link.pk)
            with self.assertRaises(CensorlySupportError):
                defer_censorly(link.pk)

    def test_refetch_replaces_article_text_and_keeps_attempts(self):
        from games.censorly.wiki import WikiArticle
        from games.models import RandomCensorlyGame

        game, task, share_hash, puzzle = _make_puzzle_task(
            title='Кот',
            body='Старый текст про кота и дом, где живёт кот.',
        )
        tags = dict(task.tags)
        stored = dict(tags[CENSORLY_TAGS_KEY])
        stored['source_pool_title'] = 'Кот (пул)'
        tags[CENSORLY_TAGS_KEY] = stored
        task.tags = tags
        task.points = 7
        task.save(update_fields=['tags', 'points'])
        old_revision = task.attempt_revision
        Attempt.manager.create(
            task=task, game=game, anon_key='cz-refetch', text='дом',
            status='Ok', points=1,
        )
        ChainTaskState.objects.create(
            task=task, game=game, anon_key='cz-refetch',
            game_mode='general',
            state='{"guesses":["дом"],"won":false}',
        )
        page = self.client.get('/support/censorly/')
        self.assertContains(page, 'support_censorly.js')
        support_script = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'support_censorly.js').read_text(encoding='utf-8')
        self.assertIn('Скачать текст заново', support_script)
        self.assertIn("actions('number', row.number)", support_script)
        self.assertIn("actions('hash', row.share_hash)", support_script)

        fresh = 'Новый текст статьи: кот сидит у окна и смотрит на двор.'
        article = WikiArticle(
            title='Кот (другое имя)',
            pageid=99,
            extract=fresh,
            truncated=True,
            revid=123,
        )
        with patch('games.support.services.censorly.fetch_article', return_value=article):
            resp = self.client.post(
                '/support/censorly/refetch-text/',
                data=json.dumps({'share_hash': share_hash}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertEqual(resp.json()['wiki_title'], 'Кот')

        task.refresh_from_db()
        self.assertNotEqual(task.attempt_revision, old_revision)
        saved = task.tags[CENSORLY_TAGS_KEY]
        self.assertEqual(saved['body_text'], fresh)
        self.assertEqual(saved['wiki_title'], 'Кот')
        self.assertEqual(saved['wiki_pageid'], 99)
        self.assertEqual(saved['wiki_revid'], 123)
        self.assertTrue(saved['truncated'])
        self.assertEqual(saved['source_pool_title'], 'Кот (пул)')
        self.assertTrue(saved.get('fetched_at'))
        self.assertEqual(task.answer, 'Кот')
        self.assertEqual(task.points, 7)
        self.assertEqual(
            RandomCensorlyGame.objects.get(share_hash=share_hash).wiki_title,
            'Кот',
        )
        attempt = Attempt.manager.get(task=task, anon_key='cz-refetch')
        self.assertEqual(attempt.text, 'дом')
        state = ChainTaskState.objects.get(task=task, anon_key='cz-refetch')
        self.assertEqual(state.state, '{"guesses":["дом"],"won":false}')


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

    def _official_page_context(self):
        page = self.client.get('/censorly/1/')
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        timing_match = re.search(r'data-gameplay-context="([^"]+)"', html)
        gameplay_match = re.search(
            r'id="censorly-root"[^>]*data-context-token="([^"]+)"', html,
        )
        self.assertIsNotNone(timing_match)
        self.assertIsNotNone(gameplay_match)
        return unescape(timing_match.group(1)), unescape(gameplay_match.group(1))

    def _start_official_timer(self, context_token, session_id):
        return self.client.post(
            '/censorly/1/timing/',
            data=json.dumps({
                'action': 'start',
                'session_id': session_id,
                'event_id': str(uuid4()),
                'seq': 1,
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=context_token,
        )

    def test_timer_client_report_is_logged_and_alerted_once(self):
        from unittest.mock import patch
        from django.core.cache import cache

        cache.clear()
        self.client.force_login(self.staff)
        report = {
            'action': 'client_report',
            'reason': 'start_failed',
            'details': {
                'online': False,
                'last_status': 403,
                'last_error': 'club_required',
                'session_id': str(uuid4()),
                'cookie': 'must not be kept',
            },
        }
        with patch(
            'games.telegram.admin_alert_events.publish_admin_alert',
            return_value=True,
        ) as publish, self.assertLogs('interoves.auth', level='INFO') as logs:
            # Accepted before any target/access checks: a refused target is
            # one of the failures the report exists to surface.
            first = self.client.post(
                '/censorly/999/timing/', data=json.dumps(report),
                content_type='application/json',
            )
            second = self.client.post(
                '/censorly/999/timing/', data=json.dumps(report),
                content_type='application/json',
            )

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(publish.call_count, 1)
        kwargs = publish.call_args.kwargs
        self.assertEqual(kwargs['alert'], 'daily_timer_client_report')
        self.assertIn('Таймер не запустился после всех попыток', kwargs['payload']['text'])
        self.assertIn('club_required', kwargs['payload']['text'])
        logged = [line for line in logs.output if 'daily_timer_client_report' in line]
        self.assertEqual(len(logged), 2)
        self.assertIn('club_required', logged[0])
        self.assertNotIn('must not be kept', logged[0])
        self.assertFalse(DailySolveTiming.objects.filter(user=self.staff).exists())

    def test_timer_client_report_rejects_unknown_reason(self):
        response = self.client.post(
            '/censorly/1/timing/',
            data=json.dumps({'action': 'client_report', 'reason': 'anything'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'bad_reason')

    def test_timing_rejects_non_uuid_session_id(self):
        from datetime import timedelta
        from django.utils import timezone

        tags = dict(self.game.tags or {})
        tags['censorly_publish_start'] = (
            f'{(timezone.localdate() - timedelta(days=1)).isoformat()}T00:00:00+03:00'
        )
        self.game.tags = tags
        self.game.save(update_fields=['tags'])
        self.client.force_login(self.staff)
        timing_context_token, _gameplay_context_token = self._official_page_context()

        # The timing core keys leases by UUID; anything else used to return
        # 200 while silently never starting the clock.
        response = self._start_official_timer(timing_context_token, '1729000000000-123456789')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'bad_session_id')
        self.assertFalse(DailySolveTiming.objects.filter(
            game=self.game, task_group=self.task.task_group, user=self.staff,
        ).exists())

    def test_daily_play_page_renders_pause_overlay_around_censorly_board(self):
        from datetime import timedelta
        from django.utils import timezone

        tags = dict(self.game.tags or {})
        tags['censorly_publish_start'] = (
            f'{(timezone.localdate() - timedelta(days=1)).isoformat()}T00:00:00+03:00'
        )
        self.game.tags = tags
        self.game.save(update_fields=['tags'])
        self.client.force_login(self.staff)

        response = self.client.get('/censorly/1/')

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('data-daily-pause-overlay', html)
        self.assertIn('data-daily-solve-board', html)
        self.assertIn('data-timing-url="/censorly/1/timing/"', html)
        self.assertLess(
            html.index('data-daily-pause-overlay'),
            html.index('data-daily-solve-board'),
        )
        self.assertLess(
            html.index('data-daily-solve-board'),
            html.index('id="censorly-root"'),
        )

    def test_guess_is_accepted_when_timer_failed_but_rejected_while_manually_paused(self):
        from datetime import timedelta
        from django.utils import timezone

        tags = dict(self.game.tags or {})
        tags['censorly_publish_start'] = (
            f'{(timezone.localdate() - timedelta(days=1)).isoformat()}T00:00:00+03:00'
        )
        self.game.tags = tags
        self.game.save(update_fields=['tags'])
        self.client.force_login(self.staff)
        timing_context_token, gameplay_context_token = self._official_page_context()

        # The timer never started (network, browser quirk): not the player's
        # fault, so solving continues and the time just goes uncounted.
        without_timer = self.client.post(
            '/censorly/1/guess/',
            data=json.dumps({'word': 'собака', 'timing_session_id': str(uuid4())}),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=gameplay_context_token,
        )

        self.assertEqual(without_timer.status_code, 200, without_timer.json())
        self.assertIn(without_timer.json()['status'], ('hit', 'miss', 'won'))

        session_id = str(uuid4())
        started = self._start_official_timer(timing_context_token, session_id)
        self.assertEqual(started.status_code, 200)
        self.assertTrue(started.json()['is_authoritative'])

        auto_paused = self.client.post(
            '/censorly/1/timing/',
            data=json.dumps({
                'action': 'auto_pause',
                'session_id': session_id,
                'event_id': str(uuid4()),
                'seq': 2,
                'claimed_ms': 0,
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=timing_context_token,
        )
        self.assertEqual(auto_paused.status_code, 200)

        after_auto_pause = self.client.post(
            '/censorly/1/guess/',
            data=json.dumps({
                'word': 'кошка',
                'timing_session_id': session_id,
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=gameplay_context_token,
        )
        self.assertEqual(after_auto_pause.status_code, 200, after_auto_pause.json())

        paused = self.client.post(
            '/censorly/1/timing/',
            data=json.dumps({
                'action': 'pause',
                'session_id': session_id,
                'event_id': str(uuid4()),
                'seq': 3,
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=timing_context_token,
        )
        self.assertEqual(paused.status_code, 200)
        attempts_before = Attempt.manager.filter(task=self.task, user=self.staff).count()

        blocked_while_paused = self.client.post(
            '/censorly/1/guess/',
            data=json.dumps({
                'word': 'дом',
                'timing_session_id': session_id,
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=gameplay_context_token,
        )
        self.assertEqual(blocked_while_paused.status_code, 409)
        self.assertEqual(blocked_while_paused.json()['error_code'], 'timer_not_running')
        self.assertTrue(blocked_while_paused.json()['daily_timing']['manually_paused'])
        self.assertEqual(
            Attempt.manager.filter(task=self.task, user=self.staff).count(),
            attempts_before,
        )

        resumed = self.client.post(
            '/censorly/1/timing/',
            data=json.dumps({
                'action': 'resume',
                'session_id': session_id,
                'event_id': str(uuid4()),
                'seq': 4,
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=timing_context_token,
        )
        self.assertEqual(resumed.status_code, 200)

        accepted = self.client.post(
            '/censorly/1/guess/',
            data=json.dumps({
                'word': 'дом',
                'timing_session_id': session_id,
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=gameplay_context_token,
        )

        self.assertEqual(accepted.status_code, 200)
        self.assertIn(accepted.json()['status'], ('hit', 'miss', 'won'))
        self.assertTrue(Attempt.manager.filter(task=self.task, user=self.staff).exists())

    def test_hint_is_rejected_while_official_timer_is_manually_paused(self):
        from datetime import timedelta
        from django.utils import timezone

        tags = dict(self.game.tags or {})
        tags['censorly_publish_start'] = (
            f'{(timezone.localdate() - timedelta(days=1)).isoformat()}T00:00:00+03:00'
        )
        self.game.tags = tags
        self.game.save(update_fields=['tags'])
        self.client.force_login(self.staff)
        timing_context_token, gameplay_context_token = self._official_page_context()
        session_id = str(uuid4())
        started = self._start_official_timer(timing_context_token, session_id)
        self.assertEqual(started.status_code, 200)

        paused = self.client.post(
            '/censorly/1/timing/',
            data=json.dumps({
                'action': 'pause',
                'session_id': session_id,
                'event_id': str(uuid4()),
                'seq': 2,
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=timing_context_token,
        )
        self.assertEqual(paused.status_code, 200)
        from games.censorly.redact import title_opening_lemmas
        title_lemmas = title_opening_lemmas(self.puzzle)
        target = next(
            token for token in self.puzzle['body_tokens']
            if token.get('kind') == 'content'
            and token.get('lemma') not in title_lemmas
            and token.get('initially_open') is not True
        )

        response = self.client.post(
            '/censorly/1/hint/',
            data=json.dumps({
                'token_id': target['id'],
                'timing_session_id': session_id,
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=gameplay_context_token,
        )

        self.assertEqual(response.status_code, 409, response.json())
        self.assertEqual(response.json()['error_code'], 'timer_not_running')
        self.assertFalse(ChainTaskState.objects.filter(task=self.task, user=self.staff).exists())

    def test_stale_timer_lease_does_not_block_guesses(self):
        from datetime import timedelta
        from django.utils import timezone

        tags = dict(self.game.tags or {})
        tags['censorly_publish_start'] = (
            f'{(timezone.localdate() - timedelta(days=1)).isoformat()}T00:00:00+03:00'
        )
        self.game.tags = tags
        self.game.save(update_fields=['tags'])
        self.client.force_login(self.staff)
        timing_context_token, gameplay_context_token = self._official_page_context()
        session_id = str(uuid4())
        started = self._start_official_timer(timing_context_token, session_id)
        self.assertEqual(started.status_code, 200)

        timing = DailySolveTiming.objects.get(
            game=self.game,
            task_group=self.task.task_group,
            user=self.staff,
        )
        session = timing.sessions.get(session_id=session_id)
        session.last_heartbeat_at = timezone.now() - timedelta(seconds=60)
        session.save(update_fields=['last_heartbeat_at'])

        response = self.client.post(
            '/censorly/1/guess/',
            data=json.dumps({
                'word': 'собака',
                'timing_session_id': session_id,
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=gameplay_context_token,
        )

        self.assertEqual(response.status_code, 200, response.json())
        self.assertIn(response.json()['status'], ('hit', 'miss', 'won'))

    def test_random_hash_timing_context_uses_random_timing_route(self):
        self.client.force_login(self.staff)

        response = self.client.get(f'/censorly/r/{self.share_hash}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'data-timing-url="/censorly/r/{self.share_hash}/timing/"',
        )
        self.assertContains(response, 'data-daily-pause-overlay')

    def test_orphan_random_hash_cannot_start_official_timer(self):
        self.client.force_login(self.staff)
        RandomCensorlyGame.objects.filter(task_group=self.task.task_group).delete()

        response = self.client.post(
            f'/censorly/r/{self.share_hash}/timing/',
            data=json.dumps({
                'action': 'start',
                'session_id': str(uuid4()),
                'event_id': str(uuid4()),
                'seq': 1,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(DailySolveTiming.objects.filter(
            game=self.game, task_group=self.task.task_group, user=self.staff,
        ).exists())

    def test_daily_share_hash_can_start_timer_without_random_game_marker(self):
        self.client.force_login(self.staff)
        RandomCensorlyGame.objects.filter(task_group=self.task.task_group).delete()

        response = self.client.post(
            f'/censorly/{self.share_hash}/timing/',
            data=json.dumps({
                'action': 'start',
                'session_id': str(uuid4()),
                'event_id': str(uuid4()),
                'seq': 1,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(DailySolveTiming.objects.filter(
            game=self.game, task_group=self.task.task_group, user=self.staff,
        ).exists())

    def test_replay_page_token_allows_guess_after_starting_over(self):
        from datetime import timedelta
        from django.utils import timezone

        tags = dict(self.game.tags or {})
        tags['censorly_publish_start'] = (
            f'{(timezone.localdate() - timedelta(days=1)).isoformat()}T00:00:00+03:00'
        )
        self.game.tags = tags
        self.game.save(update_fields=['tags'])
        self.client.force_login(self.staff)
        Profile.objects.create(user=self.staff, first_name='Staff', last_name='Tester')
        PlayerCompletedGame.objects.create(
            user=self.staff,
            game=self.game,
            task_group=self.task.task_group,
            game_kind=CENSORLY_GAME_ID,
            game_instance_id=game_instance_id_for_task_group(self.game, self.task.task_group),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )

        page = self.client.get('/censorly/1/')
        self.assertEqual(page.status_code, 200)
        started = self.client.post('/censorly/1/replay/')
        self.assertEqual(started.status_code, 302)
        replay_page = self.client.get(started['Location'])
        self.assertEqual(replay_page.status_code, 200)
        match = re.search(r'data-context-token="([^"]+)"', replay_page.content.decode())
        self.assertIsNotNone(match)

        guess = self.client.post(
            '/censorly/1/guess/',
            data=json.dumps({'word': 'собака'}),
            content_type='application/json',
            HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=unescape(match.group(1)),
        )

        self.assertEqual(guess.status_code, 200)
        self.assertIn(guess.json()['status'], ('hit', 'miss', 'won'))
        self.assertFalse(guess.json().get('reload_required'))

    def test_active_replay_control_is_visible_without_official_completion(self):
        from django.template.loader import render_to_string

        html = render_to_string('new/partials/replay_controls.html', {
            'replay_active': True,
            'official_completed': False,
            'custom_reset_enabled': False,
            'replay_url': '/censorly/1/replay/',
            'replay_exit_url': '/censorly/1/replay/exit/',
        })
        controls = re.search(r'<div class="new-replay-controls[^>]*>', html)

        self.assertIsNotNone(controls)
        self.assertNotIn(' hidden', controls.group(0))
        self.assertIn('Вернуться к первому решению', html)

    def test_staff_opens_unpublished_number(self):
        self.client.force_login(self.staff)
        resp = self.client.get('/censorly/1/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'censorly-root')
        self.assertContains(resp, 'data-like-btn')
        self.assertContains(resp, 'data-dislike-btn')

    def test_like_dislike_endpoint_accepts_censorly_task(self):
        self.client.force_login(self.staff)
        resp = self.client.post(
            f'/like-dislike/{self.task.id}/',
            {'game_id': CENSORLY_GAME_ID, 'likes': 1, 'dislikes': 0},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['likes'], 1)
        self.assertTrue(resp.json()['liked'])

    def test_like_dislike_endpoint_rejects_malformed_reaction(self):
        self.client.force_login(self.staff)
        resp = self.client.post(
            f'/like-dislike/{self.task.id}/',
            {'game_id': CENSORLY_GAME_ID, 'likes': 'oops', 'dislikes': 0},
        )
        self.assertEqual(resp.status_code, 400)

    def test_random_game_post_redirects_non_subscriber(self):
        from unittest.mock import patch

        with patch('games.views.censorly_views.has_club_access', return_value=False):
            response = self.client.post('/censorly/random/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/subscription/')

    def test_random_game_post_redirects_subscriber_to_random_game(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        random_game = SimpleNamespace(share_hash='abcd1234abcd1234')
        with patch('games.views.censorly_views.has_club_access', return_value=True), \
             patch('games.censorly.random_game.get_or_create_random_game', return_value=random_game):
            response = self.client.post('/censorly/random/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/censorly/r/abcd1234abcd1234/')

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

    def test_results_page_renders_through_compat_sql_aggregate_module(self):
        from games.results_sql_aggregate import get_sql_aggregated_game_actor_rows

        link = GameTaskGroup.objects.get(task_group=self.task.task_group)
        RandomCensorlyGame.objects.filter(task_group=self.task.task_group).delete()
        link.number = '1'
        link.name = 'Цензурка #1'
        link.save(update_fields=['number', 'name'])
        self.client.force_login(self.staff)
        with patch(
            'games.results_sql_aggregate.get_sql_aggregated_game_actor_rows',
            wraps=get_sql_aggregated_game_actor_rows,
        ) as aggregate:
            response = self.client.get('/censorly/1/results/')
        self.assertEqual(response.status_code, 200)
        aggregate.assert_called()

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
        # Grammatical tails, not the leftover after the lemma.
        # кошек and бежал have a zero ending; кошками keeps the full -ами.
        self.assertEqual(split_stem_ending('города'), ('город', 'а'))
        self.assertEqual(split_stem_ending('кошек'), ('кошек', ''))
        self.assertEqual(split_stem_ending('бежал'), ('бежал', ''))
        self.assertEqual(split_stem_ending('красивого'), ('красив', 'ого'))
        self.assertEqual(split_stem_ending('кошками'), ('кошк', 'ами'))
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
        self.assertFalse(by_lemma[lemma_of('собака')].get('guessed'))

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

    def test_paris_lead_endings_are_inflectional(self):
        """Paris lead: столиц[а], крупнейш[ий], город[∅], франци[и]."""
        from games.censorly.redact import build_public_view
        from games.censorly.tokenize import build_puzzle_payload
        from games.censorly.wiki import _trim_extract

        raw = (_CENSORLY_TESTDATA / 'pool' / 'paris.txt').read_text(encoding='utf-8')
        cleaned, _truncated = _trim_extract(raw)
        payload = build_puzzle_payload(wiki_title='Париж', body_text=cleaned)
        content = [
            t for t in payload['body_tokens']
            if t.get('kind') == 'content'
        ]

        def surface(tok):
            return normalize_surface(tok.get('surface') or '')

        idx = next(
            i for i, tok in enumerate(content)
            if surface(tok) == 'столица'
            and surface(content[i + 1]) == 'крупнейший'
            and surface(content[i + 2]) == 'город'
            and surface(content[i + 3]) == 'франции'
        )
        capital, largest, city, france = content[idx:idx + 4]
        self.assertEqual(capital.get('ending'), 'а')
        self.assertEqual(capital.get('stem_length'), len('столиц'))
        self.assertEqual(largest.get('ending'), 'ий')
        self.assertEqual(largest.get('stem_length'), len('крупнейш'))
        self.assertNotIn('ending', city)
        self.assertEqual(france.get('ending'), 'и')
        self.assertEqual(france.get('stem_length'), len('франци'))

        view = build_public_view(
            payload, revealed_lemmas=set(), won=False, show_endings=True,
        )
        by_id = {t['id']: t for t in view['body_tokens']}
        self.assertEqual(by_id[capital['id']].get('ending'), 'а')
        self.assertEqual(by_id[largest['id']].get('ending'), 'ий')
        self.assertNotIn('ending', by_id[city['id']])
        self.assertEqual(by_id[france['id']].get('ending'), 'и')
        self.assertEqual(by_id[france['id']].get('stem_length'), len('франци'))


class CensorlyHeadingLevelTests(SimpleTestCase):
    def test_formula_cleanup_preserves_heading_after_formula(self):
        from games.censorly.tokenize import HEADING_START
        from games.censorly.wiki import _trim_extract

        body, truncated = _trim_extract(
            'Формула {\\displaystyle x = 1}.\n\n'
            '=== Подзаголовок ===\n\n'
            'Текст после заголовка.'
        )

        self.assertFalse(truncated)
        self.assertIn(HEADING_START, body)
        self.assertNotIn('===', body)

    def test_old_saved_extract_repairs_heading_joined_to_following_text(self):
        from games.censorly import CENSORLY_SPLITTER_VERSION
        from games.censorly.tokenize import resolve_puzzle_payload

        resolved = resolve_puzzle_payload({
            'wiki_title': 'Статья',
            'body_text': 'Формула x = 1.\n\n=== Подзаголовок === Текст после заголовка.',
            'splitter_version': CENSORLY_SPLITTER_VERSION - 1,
        })

        heading_words = [
            token for token in resolved['body_tokens']
            if token.get('in_heading') and token.get('kind') == 'content'
        ]
        self.assertEqual([token['surface'] for token in heading_words], ['Подзаголовок'])
        self.assertNotIn('===', resolved['body_text'])

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
