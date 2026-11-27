"""Цензурка as a task of an ordinary game (games.site.views.game.censorly)."""

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from games.models import (
    Attempt,
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    ProfileTeamMembership,
    Project,
    Registration,
    Task,
    TaskGroup,
    Team,
)
from games.puzzles.censorly.ids import CENSORLY_TAGS_KEY, CENSORLY_TASK_TYPE
from games.puzzles.censorly.play import guess_limit, hint_penalty_for, points_for_state
from games.puzzles.censorly.text.tokenize import build_puzzle_payload


def _setup_db():
    Project.objects.get_or_create(pk='main', defaults={'name': 'main'})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='censorly')


class CensorlyGameTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _setup_db()
        now = timezone.now()
        with patch('games.realtime.track_compat.track_task_change'):
            cls.game = Game.objects.create(
                id='censorly_tenfold',
                name='Десяточка с цензурками',
                author='test',
                project_id='main',
                is_ready=True,
                is_playable=True,
                is_tournament=True,
                is_registrable=True,
                start_time=now - timedelta(hours=1),
                end_time=now + timedelta(days=1),
            )
            checker = CheckerType.objects.get(pk='censorly')
            cls.tg = TaskGroup.objects.create(
                label='censorly_tenfold_tg', checker=checker, points=1, max_attempts=3,
            )
            GameTaskGroup.objects.create(game=cls.game, task_group=cls.tg, number='1', name='Цензурки')
            puzzle = build_puzzle_payload(
                wiki_title='Кот',
                body_text='Кот сидит у окна. Окно выходит во двор.',
                wiki_pageid=1,
            )
            cls.task = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type=CENSORLY_TASK_TYPE,
                checker=checker,
                answer='Кот',
                tags={CENSORLY_TAGS_KEY: puzzle},
                points=Decimal('2'),
            )
        cls.team = Team.objects.create(name='censorly_team', visible_name='C')
        cls.user = User.objects.create_user('censorly_player', password='x')
        profile = Profile.objects.create(user=cls.user, team_on=cls.team)
        ProfileTeamMembership.objects.get_or_create(profile=profile, team=cls.team)
        Registration.objects.create(game=cls.game, team=cls.team)
        cls.base = f'/games/{cls.game.id}/censorly/{cls.task.id}/'

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['play_mode_main'] = 'team'
        session.save()

    def _guess(self, word):
        return self.client.post(
            self.base + 'guess/', json.dumps({'word': word}),
            content_type='application/json',
        )

    def test_page_renders_board_without_the_answer(self):
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="censorly-root"', html)
        self.assertIn('data-play-mode="team"', html)
        self.assertIn(f'{self.base}guess/', html)
        # The rules open through base.html, which needs the storage key, with
        # the daily game's compact «?» trigger; no replay form here.
        self.assertIn('id="rules-modal" data-storage-key="section_rules_seen_censorly"', html)
        self.assertIn('new-rules-trigger--compact', html)
        self.assertNotIn('data-replay-submit', html)
        bootstrap = response.context['bootstrap']
        self.assertEqual(bootstrap['max_attempts'], 3)
        self.assertFalse(bootstrap['won'])
        self.assertIsNone(bootstrap['wiki_title'])

    def test_team_shares_one_tournament_board(self):
        response = self._guess('окно')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['status'], 'hit')
        row = ChainTaskState.objects.get(task=self.task, game=self.game)
        self.assertEqual(row.team_id, self.team.pk)
        self.assertIsNone(row.user_id)
        self.assertEqual(row.game_mode, 'tournament')
        attempt = Attempt.manager.get(task=self.task)
        self.assertEqual(attempt.team_id, self.team.pk)

    def test_guess_limit_counts_guesses_not_hints(self):
        payload = self.task.tags[CENSORLY_TAGS_KEY]
        body_token = next(
            tok for tok in payload['body_tokens']
            if tok.get('kind') == 'content' and not tok.get('title_lemma')
            and (tok.get('lemma') or '') not in ('кот',)
        )
        hint = self.client.post(
            self.base + 'hint/', json.dumps({'token_id': body_token['id']}),
            content_type='application/json',
        )
        self.assertEqual(hint.json()['status'], 'hint', hint.content)
        for word in ('собака', 'дом', 'река'):
            self.assertEqual(self._guess(word).json()['status'], 'miss')
        refused = self._guess('стол')
        self.assertEqual(refused.json()['status'], 'limit')
        self.assertEqual(refused.json()['error'], 'Попытки закончились')
        state = json.loads(ChainTaskState.objects.get(task=self.task, game=self.game).state)
        self.assertEqual(len(state['guesses']), 3)
        self.assertEqual(state['hints_taken'], 1)

    def test_win_scores_task_points_minus_a_twentieth_per_hint(self):
        payload = self.task.tags[CENSORLY_TAGS_KEY]
        body_token = next(
            tok for tok in payload['body_tokens']
            if tok.get('kind') == 'content' and (tok.get('lemma') or '') != 'кот'
        )
        self.client.post(
            self.base + 'hint/', json.dumps({'token_id': body_token['id']}),
            content_type='application/json',
        )
        won = self._guess('кот').json()
        self.assertEqual(won['status'], 'won')
        self.assertNotIn('share_card', won)
        info = Attempt.manager.get_attempts_info(
            team=self.team, task=self.task, mode='tournament', game=self.game,
        )
        # 2 points − 2/20 for the hint.
        self.assertEqual(Decimal(str(info.get_result_points())), Decimal('1.9'))

    def test_unregistered_team_cannot_play(self):
        other = Team.objects.create(name='censorly_other', visible_name='O')
        user = User.objects.create_user('censorly_other_player', password='x')
        profile = Profile.objects.create(user=user, team_on=other)
        ProfileTeamMembership.objects.get_or_create(profile=profile, team=other)
        self.client.force_login(user)
        session = self.client.session
        session['play_mode_main'] = 'team'
        session.save()
        response = self._guess('окно')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ChainTaskState.objects.filter(task=self.task).exists())

    def test_daily_games_are_unlimited_and_keep_the_daily_penalty(self):
        daily = Game(id='censorly', project_id='sections')
        self.assertIsNone(guess_limit(self.task, daily))
        self.assertEqual(guess_limit(self.task, self.game), 3)
        daily_task = Task(points=Decimal('20'))
        self.assertEqual(hint_penalty_for(daily_task), Decimal('1'))
        self.assertEqual(points_for_state({'hints_taken': 3}, task=daily_task), Decimal('17'))

    def test_task_card_links_to_the_task_page(self):
        response = self.client.get(f'/games/{self.game.id}/1/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.base, response.content.decode())


class CensorlyAdminFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _setup_db()
        cls.tg = TaskGroup.objects.create(label='censorly_admin_tg', points=1, max_attempts=100)

    def _form(self, **extra):
        from games.site.admin import WordSaladTaskForm
        data = {
            'task_group': self.tg.pk,
            'number': '1',
            'task_type': CENSORLY_TASK_TYPE,
            'points': '1',
            'tags': '{}',
            'text': '',
            **extra,
        }
        return WordSaladTaskForm(data=data)

    def test_article_title_builds_the_puzzle_and_answer(self):
        puzzle = build_puzzle_payload(wiki_title='Кот', body_text='Кот сидит.', wiki_pageid=1)
        with patch(
            'games.puzzles.censorly.authoring.puzzle_for_title',
            return_value=('Кот', puzzle),
        ):
            form = self._form(censorly_wiki_title='Кот')
            self.assertTrue(form.is_valid(), form.errors)
        task = form.save()
        self.assertEqual(task.answer, 'Кот')
        self.assertEqual(task.checker_id, 'censorly')
        self.assertEqual(task.tags[CENSORLY_TAGS_KEY]['wiki_title'], puzzle['wiki_title'])

    def test_censorly_without_an_article_is_refused(self):
        form = self._form()
        self.assertFalse(form.is_valid())
        self.assertIn('censorly_wiki_title', form.errors)
