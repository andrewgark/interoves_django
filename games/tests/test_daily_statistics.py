import json

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase

from games.daily_statistics import build_attempt_histogram, build_daily_statistics
from games.models import (
    Attempt,
    CheckerType,
    DailySolveTiming,
    Game,
    GameTaskGroup,
    PlayerCompletedGame,
    Project,
    Task,
    TaskGroup,
)


class DailyStatisticsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project, _ = Project.objects.get_or_create(pk='sections')
        self.game = Game.objects.filter(id='alphabetty', project=self.project).first()
        if self.game is None:
            self.game = Game.objects.create(id='alphabetty', name='Алфавитка', project=self.project)
        self.users = [User.objects.create_user('stats{}'.format(i)) for i in range(3)]

    def _task(self, task_type, answer='СЛОВО', checker_data=None):
        tg = TaskGroup.objects.create(label='stats')
        GameTaskGroup.objects.create(game=self.game, task_group=tg, number=str(tg.pk), name='stats')
        checker, _ = CheckerType.objects.get_or_create(pk=task_type)
        task = Task.objects.create(
            task_group=tg, number='1', task_type=task_type,
            checker=checker, answer=answer, checker_data=checker_data,
        )
        return tg, task

    def _complete(self, tg, user):
        PlayerCompletedGame.objects.create(
            user=user, game=self.game, task_group=tg, game_kind='alphabet',
            game_instance_id='alphabetty:{}'.format(tg.pk),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )

    def test_alphabet_distribution_and_unique_popular_guesses(self):
        tg, task = self._task('alphabetty')
        for user, guesses in zip(self.users, (['А', 'Б', 'СЛОВО'], ['А', 'СЛОВО'], ['В', 'Г', 'Д', 'СЛОВО'])):
            for index, guess in enumerate(guesses):
                Attempt.manager.create(
                    user=user, game=self.game, task=task, text=guess,
                    status='Ok' if guess == 'СЛОВО' else 'Partial',
                    state=json.dumps({'guesses': guesses, 'won': guess == 'СЛОВО'}),
                )
            self._complete(tg, user)
        data = build_daily_statistics(self.game, tg)
        self.assertEqual(data['summary']['median_attempts'], 3)
        self.assertEqual([row['count'] for row in data['distribution']], [1, 1, 1])
        self.assertEqual(data['guesses'][0]['word'], 'А')
        self.assertEqual(data['guesses'][0]['players'], 2)
        self.assertNotIn('СЛОВО', {row['word'] for row in data['guesses']})

    def test_alphabet_histogram_keeps_small_range_and_internal_zeros(self):
        data = build_attempt_histogram([2, 3, 5])
        self.assertEqual([row['label'] for row in data], ['2', '3', '4', '5'])
        self.assertEqual([row['from'] for row in data], [2, 3, 4, 5])
        self.assertEqual([row['to'] for row in data], [2, 3, 4, 5])
        self.assertEqual([row['count'] for row in data], [1, 1, 0, 1])

    def test_alphabet_histogram_keeps_exactly_eight_values(self):
        data = build_attempt_histogram(range(1, 9))
        self.assertEqual(len(data), 8)
        self.assertEqual([row['label'] for row in data], [str(value) for value in range(1, 9)])
        self.assertNotIn('+', ''.join(row['label'] for row in data))

    def test_alphabet_histogram_folds_rare_tail_and_hides_outlier(self):
        values = [1] + [2] * 5 + [3] * 18 + [4] * 34 + [5] * 29 + [6] * 16 + [7] * 8 + [8] * 4 + [9] * 2 + [10, 14]
        data = build_attempt_histogram(values)
        self.assertEqual([row['label'] for row in data], ['1', '2', '3', '4', '5', '6', '7', '8+'])
        self.assertEqual(data[-1]['count'], 8)
        self.assertEqual(data[-1]['to'], None)
        self.assertEqual(sum(row['percent'] for row in data), 100.0)

        outlier_data = build_attempt_histogram([3] * 20 + [4] * 30 + [5] * 25 + [6] * 15 + [7] * 8 + [37])
        self.assertEqual(outlier_data[-1]['label'], '8+')
        self.assertEqual(outlier_data[-1]['count'], 1)

    def test_alphabet_histogram_prioritizes_eight_buckets_when_tail_is_large(self):
        data = build_attempt_histogram([1] * 10 + list(range(2, 21)))
        self.assertEqual(len(data), 8)
        self.assertEqual([row['label'] for row in data], ['1', '2', '3', '4', '5', '6', '7', '8+'])
        self.assertGreater(data[-1]['percent'], 10)

    def test_alphabet_histogram_handles_empty_single_and_deterministic_input(self):
        self.assertEqual(build_attempt_histogram([]), [])
        self.assertEqual(build_attempt_histogram([4]), [{
            'from': 4, 'to': 4, 'count': 1, 'label': '4',
            'percent': 100.0, 'bar_percent': 100.0,
        }])
        values = [1, 2, 2, 3, 9, 20]
        self.assertEqual(build_attempt_histogram(values), build_attempt_histogram(reversed(values)))

    def test_population_is_completed_players_and_salad_hint_rate(self):
        game = Game.objects.filter(id='salad', project=self.project).first()
        if game is None:
            game = Game.objects.create(id='salad', name='Салатик', project=self.project)
        tg = TaskGroup.objects.create(label='salad stats')
        GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='salad')
        checker, _ = CheckerType.objects.get_or_create(pk='word_salad')
        task = Task.objects.create(
            task_group=tg, number='1', task_type='word_salad', checker=checker,
            checker_data=json.dumps({'grid': list('ABCDEFGHIJKLMNOP'), 'words': ['ABCD', 'EFGH']}),
        )
        for user, state in zip(self.users[:2], (
            {'solved_indices': [0, 1], 'hint_counts': {}, 'found_extra': [], 'found_rare_words': []},
            {'solved_indices': [0, 1], 'hint_counts': {'1': 1}, 'found_extra': ['ИГРА'], 'found_rare_words': []},
        )):
            Attempt.manager.create(
                user=user, game=game, task=task, text=json.dumps({'action': 'solve', 'path': [0]}),
                status='Ok', state=json.dumps(state), active_time_ms=1000,
            )
            PlayerCompletedGame.objects.create(
                user=user, game=game, task_group=tg, game_kind='salad',
                game_instance_id='salad:{}'.format(tg.pk), result=PlayerCompletedGame.RESULT_SOLVED,
            )
        data = build_daily_statistics(game, tg)
        self.assertEqual(data['solved'], 2)
        self.assertEqual(data['words'][1]['hint_percent'], 50.0)
        self.assertEqual(data['off_topic'][0]['players'], 1)

    def test_ladder_uses_success_order_and_active_intervals(self):
        game = Game.objects.filter(id='ladder', project=self.project).first()
        if game is None:
            game = Game.objects.create(id='ladder', name='Лесенка', project=self.project)
        tg = TaskGroup.objects.create(label='ladder stats')
        GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='ladder')
        checker, _ = CheckerType.objects.get_or_create(pk='raddle')
        task = Task.objects.create(
            task_group=tg, number='1', task_type='raddle', checker=checker,
            checker_data=json.dumps({'lengths': [1, 1, 1, 1], 'hints': ['a', 'b', 'c'], 'words': ['А', 'Б', 'В', 'Г']}),
        )
        user = self.users[0]
        for solved, elapsed in (([0, 1], 1000), ([0, 1, 2], 3000), ([0, 1, 2, 3], 7000)):
            Attempt.manager.create(
                user=user, game=game, task=task, text=json.dumps({'word_index': solved[-1], 'word': 'x'}),
                status='Partial' if len(solved) < 4 else 'Ok',
                state=json.dumps({'solved_indices': solved, 'assist_tier': {}}),
                active_time_ms=elapsed,
            )
        PlayerCompletedGame.objects.create(
            user=user, game=game, task_group=tg, game_kind='ladder',
            game_instance_id='ladder:{}'.format(tg.pk), result=PlayerCompletedGame.RESULT_SOLVED,
        )
        data = build_daily_statistics(game, tg)
        self.assertEqual([row['median_time_seconds'] for row in data['words'][1:3]], [1.0, 2.0])
