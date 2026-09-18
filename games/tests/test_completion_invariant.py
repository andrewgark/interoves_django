import json
from datetime import datetime, timezone
from io import StringIO
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase

from games.analytics import (
    _backfill_supported_game_completions,
    is_task_group_complete,
    register_completed_game,
)
from games.models import (
    Attempt,
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    PlayerCompletedGame,
    Project,
    Task,
    TaskGroup,
)


class CompletionInvariantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project, _ = Project.objects.get_or_create(pk='main')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        cls.checker, _ = CheckerType.objects.get_or_create(
            pk='equals_with_possible_spaces',
        )
        cls.user = User.objects.create_user(username='completion-invariant-user')

    def _game(self, game_id):
        if game_id not in {'replacements', 'ladder', 'alphabetty', 'salad'}:
            game_id = 'replacements'
        game, _ = Game.objects.get_or_create(
            id=game_id,
            defaults={
                'name': game_id,
                'author': 'test',
                'project': self.project,
                'is_ready': True,
                'is_tournament': False,
                'requires_ticket': False,
            },
        )
        return game

    def _group(self, game, task_specs):
        group = TaskGroup.objects.create(label='completion-invariant-group')
        number = str(GameTaskGroup.objects.filter(game=game).count() + 1)
        GameTaskGroup.objects.create(game=game, task_group=group, number=number, name=number)
        tasks = []
        for number, task_type in task_specs:
            kwargs = {
                'task_group': group,
                'number': str(number),
                'task_type': task_type,
                'checker': self.checker,
            }
            if task_type == 'replacements_lines':
                kwargs.update(text='_A_', checker_data='_B_')
            elif task_type == 'raddle':
                kwargs.update(
                    checker_data=json.dumps({
                        'lengths': [3, 3, 3, 3],
                        'hints': ['A ____', '____ C', '____ D'],
                        'words': ['AAA', 'BBB', 'CCC', 'DDD'],
                    }),
                )
            elif task_type == 'word_salad':
                kwargs.update(checker_data=json.dumps({
                    'grid': list('ABCDEFGHIJKLMNOP'),
                    'words': ['ABCD', 'EFGH'],
                }))
            tasks.append(Task.objects.create(**kwargs))
        return group, tasks

    def _set_state(self, task, game, *, user=None, anon_key=None, complete=True):
        if user is None and anon_key is None:
            user = self.user
        if task.task_type == 'replacements_lines':
            state = {'solved_lines': [0] if complete else [], 'total': 1}
        elif task.task_type == 'raddle':
            state = {'solved_indices': list(range(4)) if complete else [], 'total': 4}
        elif task.task_type == 'alphabetty':
            state = {'won': complete}
        else:
            state = {
                'solved_indices': [0, 1] if complete else [0],
                'active': [],
            }
        return ChainTaskState.objects.create(
            task=task, game=game, user=user, anon_key=anon_key,
            game_mode='general', state=json.dumps(state),
        )

    def test_replacements_chain_completion_does_not_complete_group(self):
        game = self._game('replacements')
        group, tasks = self._group(
            game,
            [('0', 'replacements_lines')] + [(n, 'default') for n in range(1, 8)],
        )
        self._set_state(tasks[0], game)
        for task in tasks[4:]:
            Attempt.manager.create(
                user=self.user, game=game, task=task, status='Ok', text='ok', points=1,
            )

        register_completed_game(user=self.user, task=tasks[0], game=game)

        self.assertFalse(PlayerCompletedGame.objects.filter(user=self.user).exists())

        for task in tasks[1:4]:
            Attempt.manager.create(
                user=self.user, game=game, task=task, status='Ok', text='ok', points=1,
            )
        self.assertTrue(is_task_group_complete(
            task_group=tasks[0].task_group, game=game, user=self.user,
        ))
        register_completed_game(user=self.user, task=tasks[0], game=game)
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user).count(), 1)
        register_completed_game(user=self.user, task=tasks[0], game=game)
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user).count(), 1)

    def test_replacements_egor_shape_has_no_premature_completion(self):
        game = self._game('replacements_egor_shape')
        _group, tasks = self._group(
            game,
            [('0', 'replacements_lines')] + [(n, 'default') for n in range(1, 8)],
        )
        self._set_state(tasks[0], game)
        for task in tasks[4:]:
            Attempt.manager.create(
                user=self.user, game=game, task=task, status='Ok', text='ok', points=1,
            )
        _backfill_supported_game_completions(user=self.user)
        self.assertFalse(PlayerCompletedGame.objects.filter(user=self.user).exists())

    def test_anonymous_incomplete_group_is_not_completed(self):
        game = self._game('replacements_anon')
        _group, tasks = self._group(game, [('0', 'replacements_lines'), ('1', 'default')])
        anon_key = 'completion-invariant-anon'
        self._set_state(tasks[0], game, anon_key=anon_key)
        register_completed_game(anon_key=anon_key, task=tasks[0], game=game)
        self.assertFalse(PlayerCompletedGame.objects.filter(anon_key=anon_key).exists())

    def test_ordinary_n_minus_one_then_n_creates_once(self):
        game = self._game('ordinary_completion')
        _group, tasks = self._group(game, [('1', 'default'), ('2', 'default')])
        Attempt.manager.create(user=self.user, game=game, task=tasks[0], status='Ok', text='ok')
        register_completed_game(user=self.user, task=tasks[0], game=game)
        self.assertFalse(PlayerCompletedGame.objects.filter(user=self.user).exists())
        Attempt.manager.create(user=self.user, game=game, task=tasks[1], status='Ok', text='ok')
        register_completed_game(user=self.user, task=tasks[1], game=game)
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user).count(), 1)

    def test_backfill_requires_whole_group_for_chain_games(self):
        for game_id, task_type in (
            ('ladder', 'raddle'),
            ('alphabetty', 'alphabetty'),
            ('salad', 'word_salad'),
        ):
            game = self._game(game_id)
            _group, tasks = self._group(game, [('1', task_type), ('2', task_type)])
            self._set_state(tasks[0], game)
            self.assertFalse(is_task_group_complete(
                task_group=tasks[0].task_group, game=game, user=self.user,
            ), msg=game_id)
            _backfill_supported_game_completions(user=self.user)
            self.assertFalse(
                PlayerCompletedGame.objects.filter(user=self.user, game=game).exists(),
                msg=game_id,
            )
            self._set_state(tasks[1], game)
            self.assertTrue(is_task_group_complete(
                task_group=tasks[0].task_group, game=game, user=self.user,
            ), msg=game_id)
            _backfill_supported_game_completions(user=self.user)
            self.assertEqual(
                PlayerCompletedGame.objects.filter(user=self.user, game=game).count(), 1,
            )

    def test_forensic_suspect_audit_uses_multi_task_candidate_filter(self):
        game = self._game('forensic_batch')
        group, tasks = self._group(game, [('1', 'default'), ('2', 'default')])
        completion = PlayerCompletedGame.objects.create(
            user=self.user,
            game=game,
            task_group=group,
            game_kind='forensic_batch',
            game_instance_id='forensic-batch:1',
            is_backfilled=True,
        )
        PlayerCompletedGame.objects.filter(pk=completion.pk).update(
            completed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        before = list(Attempt.manager.filter(user=self.user).values_list('pk', 'status'))

        output = StringIO()
        call_command(
            'audit_player_completed_games', '--dry-run', '--suspect-only',
            '--id', str(completion.pk), stdout=output,
        )

        self.assertIn('candidate_count=1', output.getvalue())
        self.assertIn('total_suspect: 1', output.getvalue())
        self.assertIn('ambiguous: 1', output.getvalue())
        self.assertEqual(
            list(Attempt.manager.filter(user=self.user).values_list('pk', 'status')),
            before,
        )
        self.assertEqual(PlayerCompletedGame.objects.get(pk=completion.pk).task_group_id, group.pk)
        self.assertEqual(len(tasks), 2)

    def test_forensic_scope_excludes_structural_daily_completion(self):
        game = self._game('ladder')
        group, _tasks = self._group(game, [('1', 'raddle')])
        completion = PlayerCompletedGame.objects.create(
            user=self.user,
            game=game,
            task_group=group,
            game_kind='ladder',
            game_instance_id='ladder:forensic-scope:1',
        )

        output = StringIO()
        call_command(
            'audit_player_completed_games', '--dry-run', '--suspect-only',
            '--id', str(completion.pk), stdout=output,
        )

        self.assertIn('candidate_count=0', output.getvalue())
        self.assertIn('total_suspect: 0', output.getvalue())

    def _repair_fixture(self, pk, *, classification='invalid'):
        game = self._game('replacements')
        game.is_tournament = True
        game.save(update_fields=['is_tournament'])
        group, tasks = self._group(
            game,
            [('0', 'replacements_lines'), ('1', 'default')],
        )
        state = self._set_state(tasks[0], game)
        if classification == 'invalid':
            timestamp = datetime(2026, 8, 16, 11, 47, 52, tzinfo=timezone.utc)
            ChainTaskState.objects.filter(pk=state.pk).update(updated_at=timestamp)
            completed_at = timestamp.replace(microsecond=756257)
        elif classification == 'ambiguous':
            completed_at = datetime(2026, 9, 18, 1, 0, tzinfo=timezone.utc)
        else:
            Attempt.manager.create(
                user=self.user, game=game, task=tasks[1], status='Ok', text='ok', points=1,
            )
            completed_at = datetime(2026, 9, 18, 1, 0, tzinfo=timezone.utc)
        completion = PlayerCompletedGame.objects.create(
            pk=pk,
            user=self.user,
            game=game,
            task_group=group,
            game_kind='replacements',
            game_instance_id='replacements:repair:{}'.format(pk),
            is_backfilled=True,
        )
        PlayerCompletedGame.objects.filter(pk=completion.pk).update(completed_at=completed_at)
        return completion, state, tasks

    def test_repair_dry_run_revalidates_and_never_touches_ambiguous_or_valid(self):
        invalid, invalid_state, invalid_tasks = self._repair_fixture(5910)
        ambiguous, _ambiguous_state, _ = self._repair_fixture(6738, classification='ambiguous')
        valid, _valid_state, _ = self._repair_fixture(7491, classification='valid')
        before_attempts = Attempt.manager.count()
        before_states = ChainTaskState.objects.count()
        output = StringIO()
        with TemporaryDirectory() as tmpdir:
            call_command(
                'repair_invalid_player_completed_games', '--dry-run',
                '--ids', '5910,6738,7491', '--export', '{}/backup.json'.format(tmpdir),
                stdout=output,
            )
        payload = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(payload['eligible'], [5910])
        self.assertEqual(payload['skipped_ambiguous'], [6738])
        self.assertEqual(payload['skipped_now_valid'], [7491])
        self.assertEqual(payload['records_changed'], 0)
        self.assertEqual(PlayerCompletedGame.objects.filter(pk__in=[5910, 6738, 7491]).count(), 3)
        self.assertEqual(Attempt.manager.count(), before_attempts)
        self.assertEqual(ChainTaskState.objects.count(), before_states)
        self.assertTrue(ChainTaskState.objects.filter(pk=invalid_state.pk).exists())
        self.assertTrue(PlayerCompletedGame.objects.filter(pk=invalid.pk).exists())
        self.assertTrue(PlayerCompletedGame.objects.filter(pk=ambiguous.pk).exists())
        self.assertTrue(PlayerCompletedGame.objects.filter(pk=valid.pk).exists())
        self.assertEqual(invalid_tasks[1].task_group_id, invalid.task_group_id)

    def test_repair_apply_deletes_only_pcg_and_allows_real_recompletion(self):
        completion, state, tasks = self._repair_fixture(5910)
        attempt_count = Attempt.manager.count()
        state_count = ChainTaskState.objects.count()
        output = StringIO()
        with TemporaryDirectory() as tmpdir:
            call_command(
                'repair_invalid_player_completed_games', '--apply',
                '--ids', '5910', '--export', '{}/backup.json'.format(tmpdir),
                stdout=output,
            )
        payload = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(payload['eligible'], [5910])
        self.assertEqual(payload['records_changed'], 1)
        self.assertFalse(PlayerCompletedGame.objects.filter(pk=completion.pk).exists())
        self.assertEqual(Attempt.manager.count(), attempt_count)
        self.assertEqual(ChainTaskState.objects.count(), state_count)
        Attempt.manager.create(
            user=self.user, game=completion.game, task=tasks[1], status='Ok', text='ok', points=1,
        )
        register_completed_game(user=self.user, task=tasks[1], game=completion.game)
        self.assertEqual(
            PlayerCompletedGame.objects.filter(
                user=self.user, game=completion.game, task_group=completion.task_group,
            ).count(),
            1,
        )
        second_output = StringIO()
        with TemporaryDirectory() as tmpdir:
            call_command(
                'repair_invalid_player_completed_games', '--apply',
                '--ids', '5910', '--export', '{}/backup.json'.format(tmpdir),
                stdout=second_output,
            )
        second_payload = json.loads(second_output.getvalue().splitlines()[0])
        self.assertEqual(second_payload['missing'], [5910])
        self.assertEqual(second_payload['records_changed'], 0)

    def test_repair_apply_refuses_ambiguous_row(self):
        completion, _state, _tasks = self._repair_fixture(6738, classification='ambiguous')
        output = StringIO()
        with TemporaryDirectory() as tmpdir:
            call_command(
                'repair_invalid_player_completed_games', '--apply',
                '--ids', '6738', '--export', '{}/backup.json'.format(tmpdir),
                stdout=output,
            )
        payload = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(payload['eligible'], [])
        self.assertEqual(payload['records_changed'], 0)
        self.assertTrue(PlayerCompletedGame.objects.filter(pk=completion.pk).exists())

    def test_current_incomplete_repair_mode_allows_ambiguous_scope(self):
        completion, _state, _tasks = self._repair_fixture(21900, classification='ambiguous')
        output = StringIO()
        call_command(
            'repair_invalid_player_completed_games', '--dry-run',
            '--current-incomplete-only', '--ids', '21900', stdout=output,
        )
        payload = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(payload['eligible'], [21900])
        self.assertEqual(payload['records_changed'], 0)
        self.assertTrue(PlayerCompletedGame.objects.filter(pk=completion.pk).exists())

    def test_repair_requires_explicit_frozen_ids(self):
        with self.assertRaisesMessage(CommandError, 'selected frozen repair cohort'):
            call_command(
                'repair_invalid_player_completed_games', '--dry-run', '--ids', '999999',
            )

    def test_repair_missing_frozen_id_is_safe_skip(self):
        output = StringIO()
        call_command(
            'repair_invalid_player_completed_games', '--dry-run', '--ids', '5910',
            stdout=output,
        )
        payload = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(payload['missing'], [5910])
        self.assertEqual(payload['eligible'], [])
