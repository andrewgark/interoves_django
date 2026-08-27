import json
from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from games.difficulty import (
    _public_context,
    calculate_observed_metrics,
    historical_norm,
    rate_difficulty_metrics,
)
from games.models import (
    Attempt,
    CheckerType,
    DailyGameDifficulty,
    Game,
    GameTaskGroup,
    HTMLPage,
    Project,
    Task,
    TaskGroup,
)


def _metrics(**overrides):
    values = {
        'n': 100,
        'median_time': 100.0,
        'median_errors': 3.0,
        'help_rate': 0.20,
        'unfinished_rate': 0.20,
        'completed_n': 90,
        'unfinished_denominator': 100,
        'error_available': True,
    }
    values.update(overrides)
    return values


def _norm(**overrides):
    values = {
        'median_time': 100.0,
        'median_errors': 3.0,
        'help_rate': 0.20,
        'unfinished_rate': 0.20,
        'sources': {},
    }
    values.update(overrides)
    return values


class DifficultyScoringTests(SimpleTestCase):
    def test_n_below_five_is_not_public(self):
        rating = rate_difficulty_metrics('ladder', _metrics(n=4), _norm())
        result = {'n': 4, **rating}
        self.assertFalse(result['is_visible'])
        self.assertIsNone(_public_context(result))

    def test_n_five_shrinks_extreme_rating_to_three(self):
        rating = rate_difficulty_metrics(
            'ladder',
            _metrics(n=5, median_time=1000, median_errors=100, help_rate=1, unfinished_rate=1),
            _norm(),
        )
        self.assertEqual(rating['raw_rating'], 5)
        self.assertEqual(rating['adjusted_rating'], 4)
        self.assertTrue(rating['is_preliminary'])
        self.assertEqual(rating['stars'], 4)

    def test_fast_simple_game_gets_low_rating(self):
        rating = rate_difficulty_metrics(
            'ladder',
            _metrics(median_time=10, median_errors=0, help_rate=0, unfinished_rate=0),
            _norm(median_errors=5),
        )
        self.assertLess(rating['adjusted_rating'], 1.2)
        self.assertEqual(rating['stars'], 1)

    def test_slow_error_prone_game_gets_high_rating(self):
        rating = rate_difficulty_metrics(
            'alphabetty',
            _metrics(median_time=500, median_errors=20, help_rate=0.8, unfinished_rate=0.8),
            _norm(),
        )
        self.assertGreater(rating['adjusted_rating'], 4.8)
        self.assertEqual(rating['stars'], 5)

    def test_missing_component_is_removed_and_weights_are_renormalized(self):
        metrics = _metrics(median_errors=None, error_available=False)
        rating = rate_difficulty_metrics('salad', metrics, _norm(median_errors=None))
        self.assertNotIn('errors', rating['weights'])
        self.assertAlmostEqual(sum(rating['weights'].values()), 1.0)
        self.assertEqual(set(rating['weights']), {'time', 'help', 'unfinished'})

    def test_preliminary_status_ends_at_ten(self):
        self.assertTrue(rate_difficulty_metrics('ladder', _metrics(n=9), _norm())['is_preliminary'])
        self.assertFalse(rate_difficulty_metrics('ladder', _metrics(n=10), _norm())['is_preliminary'])

    def test_adjusted_value_is_always_in_range(self):
        for n in (0, 1, 5, 10, 100000):
            rating = rate_difficulty_metrics('ladder', _metrics(n=n), _norm())
            self.assertGreaterEqual(rating['adjusted_rating'], 1)
            self.assertLessEqual(rating['adjusted_rating'], 5)
            self.assertGreaterEqual(rating['stars'], 1)
            self.assertLessEqual(rating['stars'], 5)


class DifficultyObservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project, _ = Project.objects.get_or_create(id='difficulty-tests')
        cls.checker, _ = CheckerType.objects.get_or_create(id='raddle')
        cls.game, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={
                'name': 'Difficulty ladder',
                'author': 'test',
                'project': cls.project,
                'is_tournament': False,
                'requires_ticket': False,
            },
        )
        cls.group = TaskGroup.objects.create(label='difficulty ladder')
        cls.placement = GameTaskGroup.objects.create(
            game=cls.game,
            task_group=cls.group,
            number='9991',
            name='Difficulty ladder #1',
        )
        with patch('games.views.track.track_task_change'):
            cls.task = Task.objects.create(
                task_group=cls.group,
                number='1',
                task_type='raddle',
                checker=cls.checker,
                checker_data=json.dumps({
                    'lengths': [3, 3, 3],
                    'hints': ['a', 'b'],
                    'words': ['AAA', 'BBB', 'CCC'],
                }),
            )

    def _attempt(self, anon_key, when, word, status):
        attempt = Attempt.manager.create(
            anon_key=anon_key,
            task=self.task,
            game=self.game,
            task_revision=self.task.attempt_revision,
            text=json.dumps({'word_index': 1, 'word': word}),
            status=status,
            points=0,
            state=json.dumps({'solved_indices': [0, 1, 2] if status == 'Ok' else [0, 2]}),
        )
        Attempt.manager.filter(pk=attempt.pk).update(time=when)
        attempt.time = when
        return attempt

    def test_many_attempts_from_one_actor_are_one_observation(self):
        base = timezone.now() - timedelta(hours=2)
        for index in range(5):
            self._attempt('same-player', base + timedelta(seconds=index), 'XXX', 'Wrong')

        metrics = calculate_observed_metrics(self.placement, now=timezone.now())

        self.assertEqual(metrics['n'], 1)
        self.assertEqual(metrics['median_errors'], 5)

    def test_long_pause_is_capped_and_does_not_break_median(self):
        base = timezone.now() - timedelta(days=1)
        for actor, seconds in (('p1', 10), ('p2', 20), ('p3', 4 * 60 * 60)):
            self._attempt(actor, base, 'XXX', 'Wrong')
            self._attempt(actor, base + timedelta(seconds=seconds), 'BBB', 'Ok')

        metrics = calculate_observed_metrics(self.placement, now=timezone.now())

        self.assertEqual(metrics['n'], 3)
        self.assertEqual(metrics['median_time'], 20)
        self.assertEqual(metrics['median_errors'], 1)


class DifficultyHistoricalNormTests(TestCase):
    def _placement(self, game, number):
        group = TaskGroup.objects.create(label='{}-{}'.format(game.pk, number))
        return GameTaskGroup.objects.create(
            game=game, task_group=group, number=str(number), name=str(number),
        )

    def test_norm_does_not_mix_game_types(self):
        # Use production ids in snapshots because the service intentionally keys
        # the norm by exact daily game id.
        sections, _ = Project.objects.get_or_create(id='sections')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name)
        ladder, _ = Game.objects.get_or_create(
            id='ladder', defaults={'name': 'Ladder', 'author': 'test', 'project': sections},
        )
        alphabetty, _ = Game.objects.get_or_create(
            id='alphabetty', defaults={'name': 'Alphabetty', 'author': 'test', 'project': sections},
        )
        current = self._placement(ladder, 9901)
        ladder_history = self._placement(ladder, 9902)
        alphabet_history = self._placement(alphabetty, 9901)
        DailyGameDifficulty.objects.create(
            placement=ladder_history,
            n=30,
            payload={'metrics': {'median_time': 100}},
        )
        DailyGameDifficulty.objects.create(
            placement=alphabet_history,
            n=30,
            payload={'metrics': {'median_time': 1000}},
        )

        norm = historical_norm(current, _metrics(median_time=500))

        self.assertEqual(norm['median_time'], 100)
