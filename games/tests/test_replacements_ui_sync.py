"""Shared Замены drafts: /send_replacements_ui/ and the one-copy page data."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from games.models import (
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    ReplacementsUiState,
    Task,
    TaskGroup,
    Team,
)
from games.puzzles.replacements.lines import parse_replacements_lines_text
from games.puzzles.replacements.lines.ui_state import (
    apply_guess_changes,
    clean_guesses,
    parse_changes,
)
from games.task_group.replacements_lines_task_data import replacements_lines_data_by_task_id

TEXT = 'FOO and BAR\nFOO again _baz|b_'
CHECKER = json.dumps({'lines': [['ONE', 'TWO'], ['ONE', 'THREE']]})


class GuessMergeTests(SimpleTestCase):
    def test_changes_merge_per_slot_and_none_clears(self):
        merged = apply_guess_changes(
            {'0:0': 'A', '0:1': 'B'},
            {'0:1': None, '1:0': ' C ', '1:1': ''},
            [2, 2],
        )
        self.assertEqual(merged, {'0:0': 'A', '1:0': 'C'})

    def test_invalid_keys_and_solved_slots_are_ignored(self):
        merged = apply_guess_changes(
            {'0:0': 'KEEP'},
            {'0:0': 'X', '0:5': 'Y', '9:0': 'Z', 'bad': 'W', '1:0': 7},
            [2, 2],
            {0: {0}},
        )
        self.assertEqual(merged, {})
        self.assertEqual(clean_guesses({'0:1': 'A', '0:0': 'B'}, [2], {0: {0}}), {'0:1': 'A'})

    def test_long_guesses_are_truncated(self):
        merged = apply_guess_changes({}, {'0:0': 'Ж' * 500}, [1])
        self.assertEqual(len(merged['0:0']), 200)

    def test_parse_changes_requires_object(self):
        self.assertIsNone(parse_changes('[1]'))
        self.assertIsNone(parse_changes('{oops'))
        self.assertEqual(parse_changes('{"0:0": "A"}'), {'0:0': 'A'})


class ParserEncodedWordTests(SimpleTestCase):
    def test_slot_tokens_carry_encoded_word(self):
        parsed = parse_replacements_lines_text('FOO и _76_ и BUSINESSman и #DC#', '')
        slots = [t for t in parsed['right_tokens'][0] if t['type'] == 'slot']
        self.assertEqual([s['encoded'] for s in slots], ['FOO', '76', 'BUSINESS'])

    def test_html_entities_are_decoded(self):
        parsed = parse_replacements_lines_text('x _&#128019;_ y', '')
        slot = [t for t in parsed['right_tokens'][0] if t['type'] == 'slot'][0]
        self.assertEqual(slot['encoded'], '\U0001F413')


class ReplacementsUiSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        CheckerType.objects.get_or_create(pk='replacements_lines')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        now = timezone.now()
        with patch('games.realtime.track_compat.track_task_change'):
            cls.game = Game.objects.create(
                id='repl_ui_sync',
                name='Replacements UI sync',
                author='test',
                is_ready=True,
                is_playable=True,
                is_tournament=False,
                start_time=now - timedelta(hours=1),
                end_time=now + timedelta(hours=1),
            )
            group = TaskGroup.objects.create(label='repl_ui_sync')
            GameTaskGroup.objects.create(game=cls.game, task_group=group, number=1)
            cls.task = Task.objects.create(
                task_group=group,
                number='1',
                task_type='replacements_lines',
                checker_id='replacements_lines',
                text=TEXT,
                checker_data=CHECKER,
                points=1,
            )
        cls.team = Team.objects.create(name='repl_ui_sync_team')
        cls.user_one = User.objects.create_user('repl_ui_one', password='pw')
        cls.user_two = User.objects.create_user('repl_ui_two', password='pw')
        Profile.objects.create(user=cls.user_one, team_on=cls.team)
        Profile.objects.create(user=cls.user_two, team_on=cls.team)

    def _client(self, username):
        client = Client()
        self.assertTrue(client.login(username=username, password='pw'))
        return client

    def _post(self, client, changes):
        return client.post(
            f'/send_replacements_ui/{self.task.pk}/',
            {'game_id': self.game.pk, 'changes': json.dumps(changes)},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_teammates_share_one_state_merged_per_slot(self):
        first = self._client('repl_ui_one')
        second = self._client('repl_ui_two')
        with patch('games.site.views.game.replacements_ui.track_actor_task_change') as track:
            response = self._post(first, {'0:0': 'ONE', '1:0': 'ONE'})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data['status'], 'ok')
            payload = data['replacements_ui'][str(self.task.pk)]
            self.assertEqual(payload, {'guesses': {'0:0': 'ONE', '1:0': 'ONE'}, 'revision': 1})
            self.assertEqual(track.call_args.kwargs['team'], self.team)
            self.assertEqual(
                track.call_args.kwargs['update_html'],
                {'replacements_ui': data['replacements_ui']},
            )
            self.assertEqual(track.call_args.kwargs['reason'], 'replacements.ui_state')

            data = self._post(second, {'0:1': 'TWO', '1:0': None}).json()
        payload = data['replacements_ui'][str(self.task.pk)]
        self.assertEqual(payload, {'guesses': {'0:0': 'ONE', '0:1': 'TWO'}, 'revision': 2})
        row = ReplacementsUiState.objects.get(task=self.task, team=self.team)
        self.assertEqual(row.guesses, {'0:0': 'ONE', '0:1': 'TWO'})
        self.assertEqual(row.updated_by_profile, self.user_two.profile)
        self.assertEqual(ReplacementsUiState.objects.count(), 1)

    def test_solved_slots_refuse_drafts(self):
        ChainTaskState.objects.create(
            team=self.team, task=self.task, game=self.game, game_mode='general',
            state=json.dumps({'solved_slots': {'0': [0]}, 'solved_lines': [], 'total': 0}),
        )
        with patch('games.site.views.game.replacements_ui.track_actor_task_change'):
            data = self._post(self._client('repl_ui_one'), {'0:0': 'X', '0:1': 'Y'}).json()
        self.assertEqual(data['replacements_ui'][str(self.task.pk)]['guesses'], {'0:1': 'Y'})

    def test_malformed_and_empty_changes(self):
        client = self._client('repl_ui_one')
        response = client.post(
            f'/send_replacements_ui/{self.task.pk}/',
            {'game_id': self.game.pk, 'changes': 'nope'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.json()['status'], 'invalid_form')
        self.assertEqual(self._post(client, {}).json()['status'], 'empty')
        self.assertFalse(ReplacementsUiState.objects.exists())

    def test_completed_official_game_requires_replay(self):
        with patch('games.site.views.game.replacements_ui.official_game_completed', return_value=True):
            response = self._post(self._client('repl_ui_one'), {'0:0': 'ONE'})
        self.assertEqual(response.json().get('error'), 'replay_required')
        self.assertFalse(ReplacementsUiState.objects.exists())

    def test_edited_task_drops_old_drafts(self):
        with patch('games.site.views.game.replacements_ui.track_actor_task_change'):
            self._post(self._client('repl_ui_one'), {'0:0': 'ONE'})
        ReplacementsUiState.objects.filter(task=self.task).update(task_revision='old')
        data = self._page_data()
        self.assertEqual(json.loads(data['guesses_json']), {})
        with patch('games.site.views.game.replacements_ui.track_actor_task_change'):
            payload = self._post(self._client('repl_ui_one'), {'1:1': 'THREE'}).json()
        self.assertEqual(payload['replacements_ui'][str(self.task.pk)]['guesses'], {'1:1': 'THREE'})

    def _page_data(self):
        return replacements_lines_data_by_task_id(
            [self.task], {}, game=self.game, team=self.team, user=None, anon_key=None,
            mode='general',
        )[self.task.pk]

    def test_page_data_has_slot_states_without_leaking_answers(self):
        ChainTaskState.objects.create(
            team=self.team, task=self.task, game=self.game, game_mode='general',
            state=json.dumps({'solved_slots': {'0': [1]}, 'solved_lines': [], 'total': 0}),
        )
        with patch('games.site.views.game.replacements_ui.track_actor_task_change'):
            self._post(self._client('repl_ui_one'), {'0:0': 'guess'})
        data = self._page_data()
        slots = [t for line in data['lines'] for t in line['tokens'] if t['type'] == 'slot']
        self.assertEqual(
            [(s['key'], s['encoded'], s['correct'], s['answer'], s['guess']) for s in slots],
            [
                ('0:0', 'FOO', False, '', 'guess'),
                ('0:1', 'BAR', True, 'TWO', ''),
                ('1:0', 'FOO', False, '', ''),
                ('1:1', 'baz|b', False, '', ''),
            ],
        )
        self.assertEqual(data['ui_revision'], 1)

    def test_rendered_page_shows_one_copy_with_slot_buttons(self):
        with patch('games.site.views.game.replacements_ui.track_actor_task_change'):
            self._post(self._client('repl_ui_one'), {'0:0': 'ONE'})
        response = self._client('repl_ui_one').get(f'/games/{self.game.pk}/1/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('css/replacements.css', html)
        self.assertIn('data-repl-slot-key="0:0"', html)
        self.assertIn('<span class="unsubmitted-replacement">ONE</span>', html)
        self.assertIn('<span class="encoded-replacement">BAR</span>', html)
        self.assertNotIn('THREE', html)
        self.assertNotIn('new-replacements-block__left', html)
